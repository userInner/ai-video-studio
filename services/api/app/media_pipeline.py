from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .config import Settings
from .storage import LocalAssetStore, StoredAsset


class MediaPipelineError(RuntimeError):
    pass


def chinese_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
        Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/53fe5be564086fefc7523ccd0a31200acf92e0e5.asset/AssetData/STHEITI.ttf"),
    ]
    font_path = next((path for path in candidates if path.is_file()), None)
    return ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()


@dataclass(frozen=True)
class ProducedAsset:
    kind: str
    stored: StoredAsset
    provider: str
    model: str
    scene_index: int | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MediaProductionResult:
    storyboard: dict[str, Any]
    assets: list[ProducedAsset]
    duration_seconds: float
    voice_provider: str
    image_provider: str


class Sub2APIImageClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate(self, prompt: str) -> bytes:
        key = self.settings.resolved_sub2api_key()
        if not key:
            raise MediaPipelineError("Sub2API 尚未配置")
        payload = {
            "model": self.settings.image_model,
            "prompt": prompt[:8000],
            "size": "1024x1536",
            "quality": self.settings.image_quality,
            "response_format": "b64_json",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.image_timeout_seconds, connect=30),
            follow_redirects=True,
        ) as client:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            response = await client.post(
                f"{self.settings.sub2api_base_url.rstrip('/')}/images/generations/async",
                headers=headers,
                json=payload,
            )
            if response.status_code == 202:
                task = response.json()
                task_id = task.get("task_id") or task.get("id")
                if not isinstance(task_id, str) or not task_id:
                    raise MediaPipelineError("Image2 异步任务缺少任务 ID")
                for _ in range(max(1, self.settings.image_timeout_seconds // 3)):
                    await asyncio.sleep(max(2, int(response.headers.get("Retry-After", "3"))))
                    response = await client.get(
                        f"{self.settings.sub2api_base_url.rstrip('/')}/images/tasks/{task_id}",
                        headers=headers,
                    )
                    response.raise_for_status()
                    task = response.json()
                    if task.get("status") == "completed":
                        response_payload = task.get("result") or {}
                        break
                    if task.get("status") == "failed":
                        error = task.get("error") or {}
                        raise MediaPipelineError(f"Image2 异步任务失败：{error.get('message', str(error))}")
                else:
                    raise MediaPipelineError("Image2 异步任务等待超时")
            elif response.status_code == 404:
                response = await client.post(
                    f"{self.settings.sub2api_base_url.rstrip('/')}/images/generations",
                    headers=headers,
                    json=payload,
                )
                response_payload = response.json() if response.status_code < 400 else {}
            else:
                response_payload = response.json() if response.status_code < 400 else {}
            if response.status_code >= 400:
                try:
                    error = response.json().get("error", {})
                    detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                except ValueError:
                    detail = response.text[:300]
                raise MediaPipelineError(f"Image2 请求失败（{response.status_code}）：{detail}")
            try:
                item = response_payload["data"][0]
            except (KeyError, IndexError, TypeError) as exc:
                raise MediaPipelineError("Image2 返回了无法识别的结果") from exc
            if item.get("b64_json"):
                try:
                    content = base64.b64decode(item["b64_json"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise MediaPipelineError("Image2 返回的图片数据已损坏") from exc
            elif item.get("url"):
                image_response = await client.get(item["url"])
                image_response.raise_for_status()
                content = image_response.content
            else:
                raise MediaPipelineError("Image2 没有返回图片")
        if not 1000 <= len(content) <= 30 * 1024 * 1024:
            raise MediaPipelineError("Image2 返回的图片为空或超过 30 MB")
        if not content.startswith((b"\x89PNG", b"\xff\xd8\xff", b"RIFF")):
            raise MediaPipelineError("Image2 返回的不是受支持的图片格式")
        return content


class SpeechSynthesizer:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def local_qwen_available(self) -> bool:
        return (
            self.settings.prefer_local_qwen_tts
            and self.settings.qwen_tts_python.is_file()
            and self.settings.qwen_tts_checkpoint.is_dir()
            and self.settings.qwen_tts_base_checkpoint.is_dir()
        )

    @property
    def preferred_extension(self) -> str:
        return "wav" if self.local_qwen_available else "mp3"

    async def synthesize_many(self, texts: list[str]) -> list[tuple[bytes, str, str, str]]:
        if self.local_qwen_available:
            return await asyncio.to_thread(self._local_qwen_batch, texts)
        results = []
        for text in texts:
            content, provider, model = await self.synthesize(text)
            results.append((content, provider, model, "mp3"))
        return results

    async def synthesize(self, text: str) -> tuple[bytes, str, str]:
        if self.settings.minimax_api_key:
            return await self._minimax(text), "minimax", self.settings.tts_model
        if self.settings.environment == "development" and self.settings.allow_native_tts_fallback:
            return await asyncio.to_thread(self._native_preview, text), "macos-preview", "Tingting"
        raise MediaPipelineError("MiniMax API Key 尚未配置，无法生成正式配音")

    def _local_qwen_batch(self, texts: list[str]) -> list[tuple[bytes, str, str, str]]:
        worker = Path(__file__).resolve().parents[1] / "scripts" / "qwen_tts_batch.py"
        if not worker.is_file():
            raise MediaPipelineError("本地 Qwen TTS 批处理脚本不存在")
        with tempfile.TemporaryDirectory(prefix="qwen-tts-") as temp_dir_value:
            temp_dir = Path(temp_dir_value)
            request = temp_dir / "request.json"
            output_dir = temp_dir / "audio"
            request.write_text(json.dumps({"texts": texts}, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [
                    str(self.settings.qwen_tts_python), str(worker), str(request), str(output_dir),
                    str(self.settings.qwen_tts_checkpoint), str(self.settings.qwen_tts_base_checkpoint),
                    str(self.settings.qwen_tts_voice_reference), self.settings.qwen_tts_voice_design,
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout)[-1600:]
                raise MediaPipelineError(f"本地 Qwen3-TTS 生成失败：{detail}")
            outputs = sorted(output_dir.glob("scene-*.wav"))
            if len(outputs) != len(texts):
                raise MediaPipelineError("本地 Qwen3-TTS 没有生成完整的分段音频")
            return [
                (path.read_bytes(), "qwen-local", "Qwen3-TTS-12Hz-1.7B-Base · locked-voice-v1", "wav")
                for path in outputs
            ]

    async def _minimax(self, text: str) -> bytes:
        payload = {
            "model": self.settings.tts_model,
            "text": text[:9999],
            "stream": False,
            "language_boost": "Chinese",
            "output_format": "hex",
            "voice_setting": {
                "voice_id": self.settings.tts_voice_id,
                "speed": 1.05,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=30)) as client:
            response = await client.post(
                f"{self.settings.minimax_base_url.rstrip('/')}/v1/t2a_v2",
                headers={"Authorization": f"Bearer {self.settings.minimax_api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise MediaPipelineError(f"MiniMax TTS 请求失败（{response.status_code}）")
        body = response.json()
        base_resp = body.get("base_resp") or {}
        if base_resp.get("status_code", 0) != 0:
            raise MediaPipelineError(f"MiniMax TTS 失败：{base_resp.get('status_msg', '未知错误')}")
        audio_hex = (body.get("data") or {}).get("audio")
        if not isinstance(audio_hex, str) or not audio_hex:
            raise MediaPipelineError("MiniMax TTS 没有返回音频")
        try:
            return bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise MediaPipelineError("MiniMax TTS 返回的音频数据已损坏") from exc

    @staticmethod
    def _native_preview(text: str) -> bytes:
        if not shutil.which("say") or not shutil.which("ffmpeg"):
            raise MediaPipelineError("本地预览配音需要 macOS say 和 FFmpeg")
        with tempfile.TemporaryDirectory(prefix="video-tts-") as temp_dir:
            aiff = Path(temp_dir) / "voice.aiff"
            mp3 = Path(temp_dir) / "voice.mp3"
            subprocess.run(["say", "-v", "Tingting", "-r", "145", "-o", str(aiff), text], check=True, capture_output=True)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "32000", "-ac", "1", "-b:a", "128k", str(mp3)],
                check=True,
                capture_output=True,
            )
            return mp3.read_bytes()


def build_storyboard(script: dict[str, Any]) -> dict[str, Any]:
    scenes = []
    for index, section in enumerate(script["sections"]):
        mode = "whiteboard_drawing"
        image_prompt = (
            "Create one clean vertical whiteboard hand-drawn illustration for a Chinese explainer video. "
            f"Narrative meaning: {section['visual_direction']}. "
            "Arrange 2 to 4 separate visual islands from top to bottom in narrative order, with generous empty space "
            "between them so each area can be drawn independently by a moving hand. Warm ivory paper #F5EBD7, "
            "charcoal pencil outlines, sparse muted vermilion, ochre and grey-blue accents, flat simple editorial doodle style. "
            "Use objects, symbols, arrows and simple anonymous figures instead of interface cards. "
            "No words, no Chinese characters, no letters, no numbers, no labels, no logos, no watermark, "
            "no photorealism, no 3D, no dense background. 2:3 portrait composition."
        )
        scenes.append(
            {
                "index": index,
                "title": section["title"],
                "narration": section["narration"],
                "visual_direction": section["visual_direction"],
                "visual_mode": mode,
                "planned_seconds": section["estimated_seconds"],
                "claim_source_urls": section["claim_source_urls"],
                "image_prompt": image_prompt,
            }
        )
    return {"version": 1, "format": "1080x1920", "fps": 25, "scenes": scenes}


class VerticalFrameRenderer:
    width = 1080
    height = 1920
    paper = "#F5F3EC"
    ink = "#18211D"
    green = "#17634A"
    signal = "#EF5A3C"

    def __init__(self) -> None:
        candidates = [
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/AssetsV2/com_apple.MobileAsset_Font8/53fe5be564086fefc7523ccd0a31200acf92e0e5f9fc31e138.asset/AssetData/STHEITI.ttf"),
            Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        ]
        self.font_path = next((path for path in candidates if path.exists()), None)

    def font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return ImageFont.truetype(str(self.font_path), size) if self.font_path else chinese_font(size)

    def render(self, scene: dict[str, Any], illustration: bytes | None = None) -> bytes:
        canvas = Image.new("RGB", (self.width, self.height), self.paper)
        draw = ImageDraw.Draw(canvas)
        mode = scene["visual_mode"]
        if illustration:
            with tempfile.NamedTemporaryFile(suffix=".png") as handle:
                handle.write(illustration)
                handle.flush()
                art = Image.open(handle.name).convert("RGB")
                art = ImageOps.contain(art, (980, 1780), method=Image.Resampling.LANCZOS)
            art = ImageEnhance.Color(art).enhance(0.82).filter(ImageFilter.GaussianBlur(0.2))
            canvas.paste(art, ((self.width - art.width) // 2, (self.height - art.height) // 2))
            with tempfile.NamedTemporaryFile(suffix=".png") as handle:
                canvas.save(handle.name, format="PNG", optimize=True)
                return Path(handle.name).read_bytes()
        else:
            text_color = self.ink
            muted = "#67746D"
            draw.ellipse((760, -180, 1260, 320), outline="#DBE5DF", width=3)
            draw.ellipse((845, -95, 1175, 235), outline="#E4E9E5", width=2)

        draw.text((86, 100), "传播引擎  ·  AI VIDEO STUDIO", font=self.font(24), fill=muted)
        draw.rounded_rectangle((84, 174, 260, 224), radius=25, fill=self.signal)
        draw.text((116, 184), f"SCENE {scene['index'] + 1:02d}", font=self.font(22), fill="white")

        title_font = self.font(74 if len(scene["title"]) <= 15 else 62)
        y = 310
        for line in self._wrap(scene["title"], title_font, 890, max_lines=4):
            draw.text((84, y), line, font=title_font, fill=text_color, stroke_width=1)
            y += int(title_font.size * 1.25) if hasattr(title_font, "size") else 80

        if mode == "timeline":
            self._timeline(draw, y + 70, text_color, muted)
        elif mode == "whiteboard":
            self._whiteboard(draw, y + 80, text_color, muted, scene["visual_direction"])
        elif mode == "evidence_card":
            self._cards(draw, y + 70, text_color, muted, scene["visual_direction"])
        elif mode == "kinetic_text":
            draw.line((84, y + 80, 996, y + 80), fill=self.signal, width=10)
            draw.text((84, y + 135), "先别急着下结论", font=self.font(48), fill=text_color)

        footer_y = 1515
        draw.rounded_rectangle((60, footer_y, 1020, 1810), radius=36, fill=(24, 33, 29, 225) if illustration else "#E8ECE8")
        draw.text((94, footer_y + 40), "画面意图", font=self.font(24), fill=self.signal)
        body_color = "#F1F4F1" if illustration else self.ink
        body_font = self.font(31)
        body_y = footer_y + 92
        for line in self._wrap(scene["visual_direction"], body_font, 850, max_lines=4):
            draw.text((94, body_y), line, font=body_font, fill=body_color)
            body_y += 48
        draw.text((84, 1850), "WHITEBOARD IS A CAPABILITY, NOT THE BOUNDARY", font=self.font(20), fill=muted)

        with tempfile.NamedTemporaryFile(suffix=".png") as handle:
            canvas.save(handle.name, format="PNG", optimize=True)
            return Path(handle.name).read_bytes()

    def _wrap(self, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        lines: list[str] = []
        current = ""
        for char in text.strip():
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = char
                if len(lines) == max_lines - 1:
                    break
            else:
                current = candidate
        if current and len(lines) < max_lines:
            lines.append(current)
        consumed = sum(len(line) for line in lines)
        if consumed < len(text.strip()) and lines:
            lines[-1] = lines[-1][:-1] + "…"
        return lines

    def _timeline(self, draw: ImageDraw.ImageDraw, y: int, text: str, muted: str) -> None:
        draw.line((160, y, 160, y + 600), fill=self.green, width=8)
        for offset, year in enumerate(("2023", "2024", "2026")):
            cy = y + offset * 250
            draw.ellipse((132, cy - 28, 188, cy + 28), fill=self.signal if offset == 2 else self.green)
            draw.text((235, cy - 37), year, font=self.font(54), fill=text)
            draw.text((235, cy + 35), ("风险显现", "清盘与处罚", "一审宣判")[offset], font=self.font(30), fill=muted)

    def _whiteboard(self, draw: ImageDraw.ImageDraw, y: int, text: str, muted: str, direction: str) -> None:
        labels = [part[:10] for part in re.split(r"[，、；。]", direction) if len(part.strip()) >= 3][:3]
        if not labels:
            labels = ["责任认定", "资产执行", "程序衔接"]
        centers = [(210, y + 150), (540, y + 420), (850, y + 150)]
        draw.line((250, y + 180, 500, y + 390), fill=self.green, width=7)
        draw.line((580, y + 390, 810, y + 180), fill=self.green, width=7)
        for index, (cx, cy) in enumerate(centers):
            draw.ellipse((cx - 125, cy - 90, cx + 125, cy + 90), outline=self.ink, width=7, fill="#FFFFFF")
            label = labels[index] if index < len(labels) else f"关键点 {index + 1}"
            draw.text((cx, cy), label, font=self.font(28), fill=text, anchor="mm")
        draw.text((84, y + 650), "把复杂问题画成一条能看懂的因果链", font=self.font(33), fill=muted)

    def _cards(self, draw: ImageDraw.ImageDraw, y: int, text: str, muted: str, direction: str) -> None:
        labels = [part.strip() for part in re.split(r"[，、；。]", direction) if len(part.strip()) >= 3][:3]
        for index in range(3):
            top = y + index * 205
            draw.rounded_rectangle((84, top, 996, top + 160), radius=24, fill="#FFFFFF", outline="#D9DFDA", width=3)
            draw.text((118, top + 28), f"0{index + 1}", font=self.font(25), fill=self.signal)
            label = labels[index][:24] if index < len(labels) else ("已核验事实", "仍待确认", "不能直接推导")[index]
            draw.text((190, top + 52), label, font=self.font(34), fill=text)
            draw.text((190, top + 104), "EVIDENCE-BOUND", font=self.font(18), fill=muted)


class VideoCompositor:
    def __init__(self, asset_store: LocalAssetStore):
        self.assets = asset_store
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        if not self.ffmpeg or not self.ffprobe:
            raise MediaPipelineError("视频合成需要 FFmpeg 与 FFprobe")

    def duration(self, path: Path) -> float:
        result = subprocess.run(
            [self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])

    def subtitle(self, path: Path, narration: str, duration: float) -> None:
        chunks = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()]
        if not chunks:
            chunks = [narration]
        weights = [max(len(chunk), 1) for chunk in chunks]
        total = sum(weights)
        cursor = 0.0
        lines = [
            "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Caption,PingFang SC,44,&H00FFFFFF,&H000000FF,&H00141E1A,&H90000000,-1,0,0,0,100,100,0,0,1,4,1,2,75,75,155,1",
            "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for chunk, weight in zip(chunks, weights):
            span = duration * weight / total
            end = min(duration, cursor + span)
            clean = chunk.replace("\n", " ").replace("{", "（").replace("}", "）")
            lines.append(f"Dialogue: 0,{self._ass_time(cursor)},{self._ass_time(end)},Caption,,0,0,0,,{clean}")
            cursor = end
        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _ass_time(seconds: float) -> str:
        centiseconds = max(0, round(seconds * 100))
        hours, rem = divmod(centiseconds, 360000)
        minutes, rem = divmod(rem, 6000)
        secs, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"

    def scene_video(self, frame: Path, audio: Path, narration: str, output: Path, mode: str, whiteboard: Path | None) -> None:
        duration = self.duration(audio)
        work_dir = output.parent / f"{output.stem}-frames"
        work_dir.mkdir(parents=True, exist_ok=True)
        chunks = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()] or [narration]
        weights = [max(len(chunk), 1) for chunk in chunks]
        if whiteboard is not None:
            self._whiteboard_scene_video(whiteboard, audio, chunks, weights, duration, work_dir, output)
            return
        still_seconds = duration
        caption_inputs: list[str] = []
        caption_filters: list[str] = []
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            caption_path = work_dir / f"caption-{index:02d}.png"
            self._caption_frame(frame, caption_path, chunk)
            span = still_seconds * weight / sum(weights)
            caption_inputs.extend(["-loop", "1", "-framerate", "25", "-t", f"{span:.4f}", "-i", str(caption_path)])
            caption_filters.append(f"[{index}:v]scale=1080:1920,setsar=1[v{index}]")
        joined_inputs = "".join(f"[v{index}]" for index in range(len(chunks)))
        caption_filters.append(f"{joined_inputs}concat=n={len(chunks)}:v=1:a=0[v]")
        still_video = work_dir / "stills.mp4"
        still_result = subprocess.run(
            [
                self.ffmpeg, "-y", "-loglevel", "error", *caption_inputs,
                "-filter_complex", ";".join(caption_filters), "-map", "[v]",
                "-t", f"{still_seconds:.4f}", "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "21", "-pix_fmt", "yuv420p", "-r", "25", str(still_video),
            ],
            capture_output=True,
            text=True,
        )
        if still_result.returncode != 0:
            raise MediaPipelineError(f"字幕画面合成失败：{still_result.stderr[-800:]}")

        inputs = ["-i", str(still_video), "-i", str(audio)]
        maps = ["-map", "0:v:0", "-map", "1:a:0"]
        cmd = [
            self.ffmpeg, "-y", "-loglevel", "error", *inputs, *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "25",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise MediaPipelineError(f"场景合成失败：{result.stderr[-800:]}")

    def _whiteboard_scene_video(
        self,
        whiteboard: Path,
        audio: Path,
        chunks: list[str],
        weights: list[int],
        duration: float,
        work_dir: Path,
        output: Path,
    ) -> None:
        inputs = ["-i", str(whiteboard)]
        filters = [
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=#F5F3EC,setsar=1[v0]"
        ]
        cursor = 0.0
        total_weight = sum(weights)
        for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            overlay = work_dir / f"subtitle-{index:02d}.png"
            self._caption_overlay(overlay, chunk)
            inputs.extend(["-loop", "1", "-framerate", "25", "-i", str(overlay)])
            end = min(duration, cursor + duration * weight / total_weight)
            filters.append(
                f"[v{index - 1}][{index}:v]overlay=0:0:enable='between(t,{cursor:.4f},{end:.4f})'[v{index}]"
            )
            cursor = end
        audio_index = len(chunks) + 1
        inputs.extend(["-i", str(audio)])
        result = subprocess.run(
            [
                self.ffmpeg, "-y", "-loglevel", "error", *inputs,
                "-filter_complex", ";".join(filters), "-map", f"[v{len(chunks)}]", "-map", f"{audio_index}:a:0",
                "-t", f"{duration:.4f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-b:a", "160k", "-movflags", "+faststart", str(output),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MediaPipelineError(f"白板场景合成失败：{result.stderr[-800:]}")

    @staticmethod
    def _caption_overlay(output: Path, caption: str) -> None:
        overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = chinese_font(43)
        draw.rounded_rectangle((58, 1565, 1022, 1810), radius=30, fill=(8, 15, 12, 218), outline=(255, 255, 255, 36), width=2)
        lines: list[str] = []
        current = ""
        for char in caption:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > 850:
                lines.append(current)
                current = char
                if len(lines) == 2:
                    break
            else:
                current = candidate
        if current and len(lines) < 3:
            lines.append(current)
        if sum(len(line) for line in lines) < len(caption) and lines:
            lines[-1] = lines[-1][:-1] + "…"
        y = 1622 if len(lines) <= 2 else 1595
        for line in lines[:3]:
            draw.text((540, y), line, font=font, fill="white", anchor="ma", stroke_width=1, stroke_fill=(0, 0, 0, 180))
            y += 64
        overlay.save(output, format="PNG", optimize=True)

    @staticmethod
    def _caption_frame(frame: Path, output: Path, caption: str) -> None:
        image = Image.open(frame).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = chinese_font(43)
        draw.rounded_rectangle((58, 1265, 1022, 1510), radius=30, fill=(8, 15, 12, 218), outline=(255, 255, 255, 36), width=2)
        lines: list[str] = []
        current = ""
        for char in caption:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > 850:
                lines.append(current)
                current = char
                if len(lines) == 2:
                    break
            else:
                current = candidate
        if current and len(lines) < 3:
            lines.append(current)
        if sum(len(line) for line in lines) < len(caption) and lines:
            lines[-1] = lines[-1][:-1] + "…"
        y = 1322 if len(lines) <= 2 else 1295
        for line in lines[:3]:
            draw.text((540, y), line, font=font, fill="white", anchor="ma", stroke_width=1, stroke_fill=(0, 0, 0, 180))
            y += 64
        Image.alpha_composite(image, overlay).convert("RGB").save(output, format="PNG", optimize=True)

    def concatenate(self, scene_paths: list[Path], output: Path) -> None:
        manifest = output.with_suffix(".txt")
        manifest.write_text("\n".join(f"file '{path}'" for path in scene_paths), encoding="utf-8")
        result = subprocess.run(
            [self.ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(output)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MediaPipelineError(f"整片拼接失败：{result.stderr[-800:]}")


class MediaPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.assets = LocalAssetStore(settings.asset_root)
        self.images = Sub2APIImageClient(settings)
        self.speech = SpeechSynthesizer(settings)
        self.frames = VerticalFrameRenderer()

    async def produce(
        self,
        project_id: str,
        script_version: int,
        build_version: int,
        script: dict[str, Any],
    ) -> MediaProductionResult:
        storyboard = build_storyboard(script)
        produced: list[ProducedAsset] = []
        voice_provider = ""
        image_provider = "not-needed"
        base = f"projects/{project_id}/media/v{script_version}/build-{build_version}"
        with tempfile.TemporaryDirectory(prefix="video-production-") as temp_dir_value:
            temp_dir = Path(temp_dir_value)
            compositor = VideoCompositor(self.assets)
            illustration_by_scene: dict[int, bytes] = {}
            image_semaphore = asyncio.Semaphore(2)

            async def load_whiteboard_source(scene: dict[str, Any]) -> tuple[int, bytes, ProducedAsset, str]:
                index = scene["index"]
                relative = f"projects/{project_id}/whiteboard-sources/v{script_version}/scene-{index + 1:02d}.png"
                path = self.settings.asset_root / relative
                if path.is_file():
                    content = self.assets.read_bytes(relative)
                    provider = "sub2api-cache"
                else:
                    async with image_semaphore:
                        content = await self.images.generate(scene["image_prompt"])
                    provider = "sub2api"
                stored = self.assets.write_bytes(relative, content)
                asset = ProducedAsset(
                    "raw_illustration", stored, "sub2api", self.settings.image_model, index,
                    {"prompt": scene["image_prompt"], "whiteboard_source": True, "cached": provider.endswith("cache")},
                )
                return index, content, asset, provider

            async def prepare_audio() -> dict[int, tuple[bytes, str, str, str]]:
                audio_extension = self.speech.preferred_extension
                values: dict[int, tuple[bytes, str, str, str]] = {}
                missing: list[dict[str, Any]] = []
                for item in storyboard["scenes"]:
                    item_index = item["index"]
                    relative = f"{base}/audio/scene-{item_index + 1:02d}.{audio_extension}"
                    if (self.settings.asset_root / relative).is_file():
                        provider = "qwen-local" if audio_extension == "wav" else ("minimax" if self.settings.minimax_api_key else "macos-preview")
                        model = "Qwen3-TTS-12Hz-1.7B-Base · locked-voice-v1" if provider == "qwen-local" else (self.settings.tts_model if provider == "minimax" else "Tingting")
                        values[item_index] = (self.assets.read_bytes(relative), provider, model, audio_extension)
                    else:
                        missing.append(item)
                if missing:
                    generated = await self.speech.synthesize_many([item["narration"] for item in missing])
                    for item, result in zip(missing, generated):
                        values[item["index"]] = result
                return values

            audio_task = asyncio.create_task(prepare_audio())
            image_results = await asyncio.gather(*(load_whiteboard_source(scene) for scene in storyboard["scenes"]))
            image_provider = "sub2api-cache" if all(item[3] == "sub2api-cache" for item in image_results) else "sub2api"
            for index, content, asset, _ in image_results:
                illustration_by_scene[index] = content
                produced.append(asset)
            audio_by_scene = await audio_task

            scene_videos: list[Path] = []
            for scene in storyboard["scenes"]:
                index = scene["index"]
                audio_bytes, provider, model, extension = audio_by_scene[index]
                audio_relative = f"{base}/audio/scene-{index + 1:02d}.{extension}"
                voice_provider = provider
                audio_stored = self.assets.write_bytes(audio_relative, audio_bytes)
                audio_path = self.assets.path_for_read(audio_stored.relative_path)
                duration = compositor.duration(audio_path)
                scene["actual_seconds"] = round(duration, 3)
                produced.append(ProducedAsset("scene_audio", audio_stored, provider, model, index, {"duration_seconds": duration}))

                frame_relative = f"{base}/frames/scene-{index + 1:02d}.png"
                frame_cached = self.settings.asset_root / frame_relative
                frame_bytes = self.assets.read_bytes(frame_relative) if frame_cached.is_file() else self.frames.render(scene, illustration_by_scene.get(index))
                frame_stored = self.assets.write_bytes(frame_relative, frame_bytes)
                frame_path = self.assets.path_for_read(frame_stored.relative_path)
                visual_provider = "sub2api" if index in illustration_by_scene else "local-graphics"
                visual_model = self.settings.image_model if index in illustration_by_scene else "editorial-card-v1"
                produced.append(ProducedAsset("scene_visual", frame_stored, visual_provider, visual_model, index, {"visual_mode": scene["visual_mode"]}))

                subtitle_path = temp_dir / f"scene-{index + 1:02d}.ass"
                compositor.subtitle(subtitle_path, scene["narration"], duration)
                subtitle_stored = self.assets.write_bytes(f"{base}/subtitles/scene-{index + 1:02d}.ass", subtitle_path.read_bytes())
                produced.append(ProducedAsset("scene_subtitle", subtitle_stored, "local", "ass-v1", index, {"duration_seconds": duration}))

                whiteboard_path, annotation = await asyncio.to_thread(
                    self._render_whiteboard, frame_path, temp_dir, index, duration, scene["narration"]
                )
                if whiteboard_path is None:
                    raise MediaPipelineError(f"第 {index + 1} 个场景没有生成白板绘制动画")
                annotation_stored = self.assets.write_json(
                    f"{base}/whiteboard/scene-{index + 1:02d}.annotation.json", annotation
                )
                produced.append(ProducedAsset(
                    "whiteboard_annotation", annotation_stored, "local", "semantic-regions-v1", index,
                    {"element_count": len(annotation["elements"]), "duration_seconds": duration},
                ))
                clip_stored = self.assets.write_bytes(
                    f"{base}/whiteboard/scene-{index + 1:02d}.mp4",
                    whiteboard_path.read_bytes(),
                )
                produced.append(ProducedAsset("whiteboard_clip", clip_stored, "legacy-whiteboard", "stream-render-v1", index, {"animation_seconds": duration}))

                scene_output = temp_dir / f"scene-{index + 1:02d}.mp4"
                await asyncio.to_thread(
                    compositor.scene_video, frame_path, audio_path, scene["narration"], scene_output, scene["visual_mode"], whiteboard_path
                )
                video_stored = self.assets.write_bytes(
                    f"{base}/scenes/scene-{index + 1:02d}.mp4",
                    scene_output.read_bytes(),
                )
                produced.append(ProducedAsset("scene_video", video_stored, "ffmpeg", "h264-aac", index, {"duration_seconds": duration}))
                scene_videos.append(scene_output)

            final_path = temp_dir / "final.mp4"
            await asyncio.to_thread(compositor.concatenate, scene_videos, final_path)
            final_duration = compositor.duration(final_path)
            final_stored = self.assets.write_bytes(
                f"{base}/final/video.mp4", final_path.read_bytes()
            )
            produced.append(
                ProducedAsset(
                    "final_video", final_stored, "ffmpeg", "h264-aac", None,
                    {"duration_seconds": final_duration, "width": 1080, "height": 1920, "fps": 25, "voice_provider": voice_provider, "image_provider": image_provider},
                )
            )
            first_frame = next(item for item in produced if item.kind == "scene_visual" and item.scene_index == 0)
            produced.append(ProducedAsset("poster", first_frame.stored, first_frame.provider, first_frame.model, None, {"width": 1080, "height": 1920}))
        return MediaProductionResult(storyboard, produced, final_duration, voice_provider, image_provider)

    def _render_whiteboard(
        self,
        frame: Path,
        temp_dir: Path,
        index: int,
        duration_seconds: float,
        narration: str,
    ) -> tuple[Path | None, dict[str, Any]]:
        legacy_root = self.settings.whiteboard_renderer_root.resolve()
        renderer = legacy_root / "scripts" / "render_stream_whiteboard.py"
        renderer = renderer.resolve()
        annotation = self._build_whiteboard_annotation(index, duration_seconds, narration)
        if not renderer.is_file():
            return None, annotation
        output_dir = temp_dir / f"whiteboard-{index}"
        output_dir.mkdir(parents=True, exist_ok=True)
        annotation_path = output_dir / "annotation.json"
        annotation_path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
        output = output_dir / "render.mp4"
        result = subprocess.run(
            [
                str(Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"), str(renderer),
                str(frame), str(annotation_path), str(output), str(legacy_root / "assets" / "drawing-hand.png"),
                "--total-ms", str(max(2500, round(duration_seconds * 1000))), "--fps", "15",
                "--grid-edge", "12", "--pause", "auto", "--ink-path", "grid",
                "--color-fill", "contour-wipe", "--cap-long-edge", "1080",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None, annotation
        return (output.resolve() if output.is_file() else None), annotation

    @staticmethod
    def _build_whiteboard_annotation(index: int, duration_seconds: float, narration: str) -> dict[str, Any]:
        chunks = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()] or [narration]
        if len(chunks) > 4:
            grouped: list[str] = []
            group_size = max(1, (len(chunks) + 3) // 4)
            for start in range(0, len(chunks), group_size):
                grouped.append("".join(chunks[start:start + group_size]))
            chunks = grouped[:4]
        total_ms = max(2500, round(duration_seconds * 1000))
        drawing_ms = max(1800, total_ms - 700)
        weights = [max(1, len(chunk)) for chunk in chunks]
        total_weight = sum(weights)
        pad_x = 34
        pad_y = 45
        usable_height = 1920 - pad_y * 2
        cursor = 100
        elements = []
        for position, (chunk, weight) in enumerate(zip(chunks, weights)):
            y0 = pad_y + round(usable_height * position / len(chunks))
            y1 = pad_y + round(usable_height * (position + 1) / len(chunks))
            span = max(550, round(drawing_ms * weight / total_weight))
            duration_ms = max(500, min(span - 100, total_ms - cursor - 500))
            elements.append({
                "id": f"event-{position + 1:02d}",
                "label": chunk[:18],
                "sequence": position + 1,
                "narrativeRole": "按旁白顺序绘制的视觉事件",
                "subtitle": chunk,
                "type": "subject",
                "region": {"x": pad_x, "y": y0, "width": 1080 - pad_x * 2, "height": max(1, y1 - y0)},
                "reveal": {
                    "direction": "top_to_bottom",
                    "startMs": cursor,
                    "durationMs": duration_ms,
                    "maskPaddingPx": 14,
                    "protectedRegions": [],
                },
                "handPath": {
                    "start": [540, y0],
                    "end": [540, max(y0, y1 - 1)],
                    "easing": "easeInOut",
                },
            })
            cursor += span
        return {
            "sceneId": f"scene-{index + 1:02d}",
            "canvas": {"width": 1080, "height": 1920},
            "storyBasis": narration,
            "sceneDurationMs": total_ms,
            "elements": elements,
        }
