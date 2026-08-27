from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .config import Settings
from .quality import QualityGate
from .storage import LocalAssetStore, StoredAsset
from .visual_director import audit_storyboard, choose_visual_mode, direct_scene, split_section_narration


class MediaPipelineError(RuntimeError):
    pass


ProgressCallback = Callable[[str, int, str, dict[str, Any]], Awaitable[None]]


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
        if self.settings.environment == "development" and self.settings.allow_native_tts_fallback:
            return await asyncio.to_thread(self._native_preview, text), "macos-preview", "Tingting"
        raise MediaPipelineError("浏览器直连 MiniMax 的配音尚未上传，无法开始合成")

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


def build_storyboard(script: dict[str, Any], sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    scenes = []
    previous_scene_type: str | None = None
    previous_visual_mode: str | None = None
    source_by_url = {str(source.get("url", "")): source for source in (sources or [])}
    for section_index, section in enumerate(script["sections"]):
        narration_parts = split_section_narration(section["narration"])
        total_chars = sum(len(part) for part in narration_parts) or 1
        for part_index, narration in enumerate(narration_parts):
            index = len(scenes)
            directed_section = {**section, "narration": narration}
            direction = direct_scene(directed_section, index, previous_scene_type)
            previous_scene_type = direction["scene_type"]
            mode = choose_visual_mode(directed_section, direction, index, previous_visual_mode)
            previous_visual_mode = mode
            evidence_sources = []
            for url in direction["evidence_source_urls"][:2]:
                source = source_by_url.get(str(url))
                evidence_sources.append(
                    source
                    or {
                        "title": urlsplit(str(url)).path.rsplit("/", 1)[-1] or "核验来源",
                        "url": str(url),
                        "publisher": urlsplit(str(url)).netloc,
                        "published_at": "",
                        "summary": "该来源支持本场景中的事实陈述。",
                        "credibility": "supporting",
                    }
                )
            beat_summary = "; ".join(
                f"{beat['kind']}: {beat['caption']}" for beat in direction["beats"]
            )
            image_prompt = (
                "Create one energetic vertical whiteboard illustration designed for a fast-paced Chinese short video. "
                f"Narrative meaning: {section['visual_direction']}. Current scene narration: {narration}. "
                f"Visual grammar: {direction['scene_type']}. Visual beats in order: {beat_summary}. "
                "Arrange one large focal object plus 2 to 4 clearly separated visual events in narrative order. "
                "Use bold scale contrast and strong directional flow; avoid tiny objects and excessive empty space. "
                "Leave clean separation between events so each area can be revealed independently. Warm ivory paper #F5EBD7, "
                "charcoal pencil outlines, sparse muted vermilion, ochre and grey-blue accents, flat simple editorial doodle style. "
                "Use objects, symbols, arrows and simple anonymous figures instead of interface cards. "
                "No words, no Chinese characters, no letters, no numbers, no labels, no logos, no watermark, "
                "no photorealism, no 3D, no dense background. 2:3 portrait composition."
            )
            title = section["title"] if len(narration_parts) == 1 else f"{section['title']} · {part_index + 1}"
            scenes.append(
                {
                    "index": index,
                    "source_section_index": section_index,
                    "source_part_index": part_index,
                    "title": title,
                    "narration": narration,
                    "visual_direction": section["visual_direction"],
                    "visual_mode": mode,
                    "planned_seconds": max(
                        3, round(section["estimated_seconds"] * len(narration) / total_chars)
                    ),
                    "pacing_seconds": round(max(2.5, len(narration) / 4.2), 2),
                    "claim_source_urls": section["claim_source_urls"],
                    "evidence_sources": evidence_sources,
                    "image_prompt": image_prompt,
                    **direction,
                }
            )
    storyboard = {
        "version": 2,
        "format": "1080x1920",
        "fps": 25,
        "visual_system": "douyin_whiteboard_v2",
        "brand_style": {
            "paper": "#F5EBD7",
            "ink": "#18211D",
            "signal": "#EF5A3C",
            "highlight": "#F2C94C",
        },
        "scenes": scenes,
    }
    issues = audit_storyboard(storyboard)
    if issues:
        raise MediaPipelineError("视觉导演输出不合格：" + "；".join(issues))
    storyboard = QualityGate.repair_storyboard(storyboard)
    quality = storyboard["quality_report"]
    if not quality["passed"]:
        errors = [item["message"] for item in quality["issues"] if item["severity"] == "error"]
        if errors:
            raise MediaPipelineError("分镜质量检测未通过：" + "；".join(errors))
    return storyboard


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

        if mode == "evidence_screenshot":
            self._evidence_screenshot(draw, y + 45, text_color, muted, scene)
        elif mode == "data_animation":
            self._data_animation(draw, y + 45, text_color, muted, scene)
        elif mode == "relationship_map":
            self._relationship_map(draw, y + 45, text_color, muted, scene)
        elif mode == "timeline":
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

    def _evidence_screenshot(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        text: str,
        muted: str,
        scene: dict[str, Any],
    ) -> None:
        source = (scene.get("evidence_sources") or [{}])[0]
        draw.rounded_rectangle((72, y, 1008, y + 760), radius=30, fill="#FFFFFF", outline="#D8DDD9", width=3)
        draw.rounded_rectangle((72, y, 1008, y + 76), radius=30, fill="#E9ECE9")
        for position, color in enumerate(("#EF5A3C", "#F2C94C", "#68A889")):
            cx = 112 + position * 38
            draw.ellipse((cx - 9, y + 29, cx + 9, y + 47), fill=color)
        publisher = str(source.get("publisher") or urlsplit(str(source.get("url", ""))).netloc or "核验来源")
        draw.text((112, y + 118), publisher, font=self.font(28), fill=self.green)
        credibility = str(source.get("credibility") or "supporting").upper()
        draw.rounded_rectangle((770, y + 105, 958, y + 157), radius=22, fill="#E9F4EE")
        draw.text((864, y + 131), credibility, font=self.font(18), fill=self.green, anchor="mm")
        title_y = y + 194
        title_font = self.font(45)
        for line in self._wrap(str(source.get("title") or scene["title"]), title_font, 800, max_lines=3):
            draw.text((112, title_y), line, font=title_font, fill=text)
            title_y += 66
        draw.line((112, title_y + 22, 942, title_y + 22), fill="#E3E7E3", width=3)
        summary_y = title_y + 66
        summary_font = self.font(30)
        summary = str(source.get("summary") or "该来源支持本段中的事实陈述。")
        for line in self._wrap(summary, summary_font, 790, max_lines=4):
            draw.text((112, summary_y), line, font=summary_font, fill=muted)
            summary_y += 47
        published_at = str(source.get("published_at") or "发布时间以原始页面为准")
        draw.text((112, y + 672), published_at, font=self.font(22), fill=muted)
        host = urlsplit(str(source.get("url", ""))).netloc
        draw.text((942, y + 672), host, font=self.font(22), fill=self.signal, anchor="ra")

    def _data_animation(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        text: str,
        muted: str,
        scene: dict[str, Any],
    ) -> None:
        points = (scene.get("data_points") or [])[:4]
        if not points:
            points = [{"label": "关键变化", "value": 1, "display_value": "1"}]
        values = [max(0.0, float(point.get("value") or 0)) for point in points]
        maximum = max(values) or 1
        chart_left, chart_right = 118, 952
        chart_top, chart_bottom = y + 100, y + 720
        draw.line((chart_left, chart_top, chart_left, chart_bottom), fill=self.ink, width=5)
        draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=self.ink, width=5)
        gap = (chart_right - chart_left) / len(points)
        for position, (point, value) in enumerate(zip(points, values)):
            bar_width = min(150, int(gap * 0.56))
            cx = int(chart_left + gap * (position + 0.5))
            height = max(28, int((chart_bottom - chart_top - 95) * value / maximum))
            color = self.signal if value == maximum else (self.green if position % 2 == 0 else "#D7A928")
            draw.rounded_rectangle((cx - bar_width // 2, chart_bottom - height, cx + bar_width // 2, chart_bottom), radius=18, fill=color)
            display = str(point.get("display_value") or value)
            draw.text((cx, chart_bottom - height - 42), display, font=self.font(31), fill=text, anchor="mm")
            label = str(point.get("label") or f"数据{position + 1}")[:8]
            draw.text((cx, chart_bottom + 42), label, font=self.font(24), fill=muted, anchor="mm")
        draw.text((118, y + 18), "数据不是装饰，它必须绑定来源", font=self.font(28), fill=muted)

    def _relationship_map(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        text: str,
        muted: str,
        scene: dict[str, Any],
    ) -> None:
        relationships = (scene.get("relationships") or [])[:4]
        entities = list(dict.fromkeys(str(item) for item in (scene.get("entities") or []) if item))
        if not relationships and len(entities) >= 2:
            relationships = [{"source": entities[0], "target": entities[1], "label": "关联"}]
        if not relationships:
            relationships = [{"source": "事件", "target": "结果", "label": "影响"}]
        nodes = list(
            dict.fromkeys(
                str(value)
                for relationship in relationships
                for value in (relationship.get("source"), relationship.get("target"))
                if value
            )
        )[:5]
        positions = [(230, y + 190), (820, y + 190), (525, y + 470), (230, y + 700), (820, y + 700)]
        node_positions = {node: positions[position] for position, node in enumerate(nodes)}
        for relationship in relationships:
            source = str(relationship.get("source", ""))
            target = str(relationship.get("target", ""))
            if source not in node_positions or target not in node_positions:
                continue
            x0, y0 = node_positions[source]
            x1, y1 = node_positions[target]
            draw.line((x0, y0, x1, y1), fill=self.green, width=7)
            mx, my = (x0 + x1) // 2, (y0 + y1) // 2
            draw.rounded_rectangle((mx - 84, my - 27, mx + 84, my + 27), radius=18, fill="#FFFFFF", outline="#CED7D1", width=2)
            draw.text((mx, my), str(relationship.get("label") or "关联")[:8], font=self.font(20), fill=muted, anchor="mm")
        for position, node in enumerate(nodes):
            cx, cy = positions[position]
            fill = "#FFF0E8" if position == 0 else "#FFFFFF"
            outline = self.signal if position == 0 else self.ink
            draw.ellipse((cx - 120, cy - 76, cx + 120, cy + 76), fill=fill, outline=outline, width=6)
            draw.text((cx, cy), node[:10], font=self.font(29), fill=text, anchor="mm")


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

    def scene_video(
        self,
        frame: Path,
        audio: Path,
        narration: str,
        output: Path,
        mode: str,
        whiteboard: Path | None,
        visual_plan: dict[str, Any] | None = None,
    ) -> None:
        duration = self.duration(audio)
        work_dir = output.parent / f"{output.stem}-frames"
        work_dir.mkdir(parents=True, exist_ok=True)
        beats = (visual_plan or {}).get("beats") or []
        chunks = [str(item.get("caption", "")).strip() for item in beats if item.get("caption")]
        if not chunks:
            chunks = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()] or [narration]
        weights = [max(len(chunk), 1) for chunk in chunks]
        if whiteboard is not None:
            self._whiteboard_scene_video(
                whiteboard, audio, chunks, weights, duration, work_dir, output, visual_plan or {}
            )
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
        visual_plan: dict[str, Any],
    ) -> None:
        overlay_manifest = work_dir / "subtitle-overlays.ffconcat"
        manifest_lines = ["ffconcat version 1.0"]
        total_weight = sum(weights)
        beats = visual_plan.get("beats") or []
        scene_type = str(visual_plan.get("scene_type", "causal_chain"))
        last_overlay: Path | None = None
        for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            overlay = work_dir / f"subtitle-{index:02d}.png"
            beat = beats[index - 1] if index - 1 < len(beats) else {}
            self._caption_overlay(
                overlay,
                chunk,
                str(beat.get("emphasis", "")),
                str(beat.get("kind", "sketch")),
                scene_type,
                index - 1,
                len(chunks),
            )
            span = duration * weight / total_weight
            escaped_path = str(overlay.resolve()).replace("'", "'\\''")
            manifest_lines.extend([f"file '{escaped_path}'", f"duration {span:.6f}"])
            last_overlay = overlay
        if last_overlay is not None:
            escaped_last = str(last_overlay.resolve()).replace("'", "'\\''")
            manifest_lines.append(f"file '{escaped_last}'")
        overlay_manifest.write_text("\n".join(manifest_lines), encoding="utf-8")

        filters = [
            self._camera_filter(str(visual_plan.get("camera_motion", "slow_push"))),
            "[v0][1:v]overlay=0:0:repeatlast=1:eof_action=repeat[v]",
        ]
        result = subprocess.run(
            [
                self.ffmpeg, "-y", "-loglevel", "error",
                "-i", str(whiteboard),
                "-f", "concat", "-safe", "0", "-i", str(overlay_manifest),
                "-i", str(audio),
                "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "2:a:0",
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
    def _camera_filter(motion: str) -> str:
        filters = {
            "push_in": (
                "[0:v]scale=1188:2112,crop=1080:1920:"
                "x='(iw-ow)/2+10*sin(t*1.2)':y='(ih-oh)/2+14*cos(t*0.8)',setsar=1[v0]"
            ),
            "snap_push": (
                "[0:v]scale=1210:2151,crop=1080:1920:"
                "x='(iw-ow)/2+18*sin(t*1.8)':y='(ih-oh)/2',setsar=1[v0]"
            ),
            "vertical_pan": (
                "[0:v]scale=1120:1992,crop=1080:1920:"
                "x='(iw-ow)/2':y='(ih-oh)*(0.5+0.35*sin(t*0.55))',setsar=1[v0]"
            ),
            "horizontal_pan": (
                "[0:v]scale=1140:2027,crop=1080:1920:"
                "x='(iw-ow)*(0.5+0.35*sin(t*0.65))':y='(ih-oh)/2',setsar=1[v0]"
            ),
            "locked_then_push": (
                "[0:v]scale=1155:2053,crop=1080:1920:"
                "x='(iw-ow)/2':y='(ih-oh)/2-10*sin(t*0.7)',setsar=1[v0]"
            ),
        }
        return filters.get(
            motion,
            "[0:v]scale=1134:2016,crop=1080:1920:"
            "x='(iw-ow)/2+12*sin(t*0.5)':y='(ih-oh)/2+12*cos(t*0.45)',setsar=1[v0]",
        )

    @staticmethod
    def _wrap_caption(
        draw: ImageDraw.ImageDraw,
        caption: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width: int = 790,
        max_lines: int = 2,
    ) -> list[str]:
        lines: list[str] = []
        current = ""
        clean_caption = " ".join(caption.split())
        for char in clean_caption:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = char
                if len(lines) == max_lines:
                    break
            else:
                current = candidate
        if current and len(lines) < max_lines:
            lines.append(current)
        if sum(len(line) for line in lines) < len(clean_caption) and lines:
            lines[-1] = lines[-1].rstrip("，。！？；：、")
            lines[-1] = (lines[-1][:-1] if lines[-1] else "") + "…"
        if len(lines) == 2:
            # Chinese captions look broken when a natural width wrap leaves only
            # one or two glyphs on the second line. Rebalance the pair while both
            # lines still fit the card.
            while len(lines[0]) > len(lines[1]) + 2:
                candidate_first = lines[0][:-1]
                candidate_second = lines[0][-1] + lines[1]
                if draw.textlength(candidate_second, font=font) > max_width:
                    break
                lines = [candidate_first, candidate_second]
        return lines or [" "]

    @classmethod
    def _draw_paper_caption(
        cls,
        draw: ImageDraw.ImageDraw,
        caption: str,
        *,
        bottom: int,
    ) -> tuple[int, int, int, int]:
        """Draw an adaptive paper-note caption that belongs to the whiteboard world."""
        font = chinese_font(46)
        lines = cls._wrap_caption(draw, caption, font)
        line_height = 62
        text_width = max(int(draw.textlength(line, font=font)) for line in lines)
        card_width = max(410, min(930, text_width + 126))
        card_height = 54 + line_height * len(lines)
        left = (1080 - card_width) // 2
        top = bottom - card_height
        right = left + card_width

        # A soft offset shadow and slightly imperfect marker stroke keep this from
        # reading like a generic app notification pasted over the illustration.
        draw.rounded_rectangle(
            (left + 5, top + 10, right + 5, bottom + 10),
            radius=21,
            fill=(24, 30, 26, 42),
        )
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=21,
            fill=(250, 247, 238, 244),
            outline=(48, 55, 50, 92),
            width=2,
        )
        marker_x = left + 31
        draw.line(
            ((marker_x + 1, top + 24), (marker_x - 2, bottom - 25)),
            fill=(232, 82, 55, 235),
            width=9,
        )
        draw.line(
            ((marker_x + 5, top + 27), (marker_x + 2, bottom - 28)),
            fill=(244, 118, 88, 150),
            width=3,
        )

        text_x = left + 61
        text_y = top + 27 + line_height / 2
        for line in lines:
            draw.text(
                (text_x, text_y),
                line,
                font=font,
                fill=(26, 34, 30, 255),
                anchor="lm",
            )
            text_y += line_height
        return left, top, right, bottom

    @classmethod
    def _caption_overlay(
        cls,
        output: Path,
        caption: str,
        emphasis: str = "",
        kind: str = "sketch",
        scene_type: str = "causal_chain",
        beat_index: int = 0,
        beat_count: int = 1,
    ) -> None:
        overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        _, _, _, caption_bottom = cls._draw_paper_caption(draw, caption, bottom=1768)

        if emphasis:
            if kind in {"big_number", "question", "contrast"} or scene_type in {"hook_burst", "reversal"}:
                accent = (239, 90, 60, 242) if kind != "question" else (242, 201, 76, 244)
                label_font = chinese_font(78 if len(emphasis) <= 6 else 60)
                label_width = min(920, int(draw.textlength(emphasis, font=label_font)) + 104)
                draw.rounded_rectangle((70, 105, 70 + label_width, 235), radius=30, fill=accent)
                draw.text((122, 169), emphasis, font=label_font, fill=(20, 28, 24, 255), anchor="lm")
            else:
                label_font = chinese_font(36)
                label_width = min(600, int(draw.textlength(emphasis, font=label_font)) + 86)
                draw.rounded_rectangle(
                    (70, 112, 70 + label_width, 190),
                    radius=20,
                    fill=(250, 243, 224, 238),
                    outline=(54, 61, 56, 80),
                    width=2,
                )
                draw.ellipse((92, 139, 108, 155), fill=(239, 90, 60, 255))
                draw.text((126, 151), emphasis, font=label_font, fill=(26, 34, 30, 255), anchor="lm")

        dot_gap = 28
        dot_start = 540 - (max(1, beat_count) - 1) * dot_gap / 2
        for position in range(max(1, beat_count)):
            color = (239, 90, 60, 255) if position == beat_index else (44, 52, 47, 92)
            cx = int(dot_start + position * dot_gap)
            dot_y = caption_bottom + 27
            draw.ellipse((cx - 5, dot_y - 5, cx + 5, dot_y + 5), fill=color)
        overlay.save(output, format="PNG", optimize=True)

    @classmethod
    def _caption_frame(cls, frame: Path, output: Path, caption: str) -> None:
        image = Image.open(frame).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        cls._draw_paper_caption(draw, caption, bottom=1510)
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
        sources: list[dict[str, Any]] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> MediaProductionResult:
        storyboard = build_storyboard(script, sources)

        async def report(step: str, progress: int, message: str, details: dict[str, Any] | None = None) -> None:
            if progress_callback is None:
                return
            try:
                await progress_callback(step, progress, message, details or {})
            except Exception:
                # Progress reporting must never abort an otherwise healthy render.
                return

        produced: list[ProducedAsset] = []
        voice_provider = ""
        image_provider = "not-needed"
        base = f"projects/{project_id}/media/v{script_version}/build-{build_version}"
        with tempfile.TemporaryDirectory(prefix="video-production-") as temp_dir_value:
            temp_dir = Path(temp_dir_value)
            compositor = VideoCompositor(self.assets)
            illustration_by_scene: dict[int, bytes] = {}
            image_semaphore = asyncio.Semaphore(2)
            image_progress_lock = asyncio.Lock()
            completed_images = 0

            async def load_whiteboard_source(scene: dict[str, Any]) -> tuple[int, bytes, ProducedAsset, str]:
                nonlocal completed_images
                index = scene["index"]
                relative = (
                    f"projects/{project_id}/whiteboard-sources/v{script_version}/"
                    f"douyin-whiteboard-v2/scene-{index + 1:02d}.png"
                )
                path = self.settings.asset_root / relative
                retry_count = 0

                async def generate_or_fallback(prompt: str) -> tuple[bytes, str]:
                    try:
                        async with image_semaphore:
                            return await self.images.generate(prompt), "sub2api"
                    except Exception:
                        source_index = int(scene.get("source_section_index", index))
                        legacy_relative = (
                            f"projects/{project_id}/whiteboard-sources/v{script_version}/"
                            f"scene-{source_index + 1:02d}.png"
                        )
                        legacy_path = self.settings.asset_root / legacy_relative
                        if not legacy_path.is_file():
                            raise
                        return self.assets.read_bytes(legacy_relative), "legacy-v1-fallback"

                if path.is_file():
                    content = self.assets.read_bytes(relative)
                    provider = "sub2api-cache"
                else:
                    content, provider = await generate_or_fallback(scene["image_prompt"])
                quality = QualityGate.assess_frame(content, index)
                while (
                    not quality.passed
                    and retry_count < self.settings.media_quality_max_retries
                    and self.settings.resolved_sub2api_key()
                ):
                    retry_count += 1
                    correction = QualityGate.correction_prompt(quality)
                    content, retry_provider = await generate_or_fallback(
                        f"{scene['image_prompt']} Quality correction: {correction}."
                    )
                    provider = "sub2api-redo" if retry_provider == "sub2api" else retry_provider
                    quality = QualityGate.assess_frame(content, index)
                if not quality.passed:
                    messages = "；".join(issue.message for issue in quality.issues)
                    raise MediaPipelineError(f"第 {index + 1} 个场景重做后仍未通过画面检测：{messages}")
                stored = self.assets.write_bytes(relative, content)
                asset = ProducedAsset(
                    "raw_illustration", stored, "sub2api", self.settings.image_model, index,
                    {
                        "prompt": scene["image_prompt"],
                        "whiteboard_source": True,
                        "cached": provider.endswith("cache"),
                        "quality_report": quality.as_dict(),
                        "redo_count": retry_count,
                    },
                )
                async with image_progress_lock:
                    completed_images += 1
                    image_progress = 32 + round(34 * completed_images / max(1, len(generated_scenes)))
                    await report(
                        "generating_media",
                        image_progress,
                        f"白板插图已完成 {completed_images}/{len(generated_scenes)}",
                        {
                            "completed": completed_images,
                            "total": len(generated_scenes),
                            "scene_index": index,
                            "asset_type": "whiteboard_illustration",
                        },
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
                        provider = "qwen-local" if audio_extension == "wav" else "minimax-browser"
                        model = "Qwen3-TTS-12Hz-1.7B-Base · locked-voice-v1" if provider == "qwen-local" else self.settings.tts_model
                        values[item_index] = (self.assets.read_bytes(relative), provider, model, audio_extension)
                    else:
                        missing.append(item)
                if missing:
                    generated = await self.speech.synthesize_many([item["narration"] for item in missing])
                    for item, result in zip(missing, generated):
                        values[item["index"]] = result
                return values

            audio_task = asyncio.create_task(prepare_audio())
            generated_scenes = [scene for scene in storyboard["scenes"] if scene["visual_mode"] == "whiteboard_drawing"]
            await report(
                "generating_media",
                30,
                f"开始生成 {len(generated_scenes)} 张白板插图",
                {"completed": 0, "total": len(generated_scenes), "scene_count": len(storyboard["scenes"])},
            )
            try:
                image_results = await asyncio.gather(*(load_whiteboard_source(scene) for scene in generated_scenes))
            except BaseException:
                audio_task.cancel()
                with suppress(BaseException):
                    await audio_task
                raise
            if not image_results:
                image_provider = "local-visuals"
            elif all(item[3] == "sub2api-cache" for item in image_results):
                image_provider = "sub2api-cache"
            elif any(item[3] == "sub2api-redo" for item in image_results):
                image_provider = "sub2api-redo"
            elif any(item[3] == "legacy-v1-fallback" for item in image_results):
                image_provider = "mixed-v2-fallback"
            else:
                image_provider = "sub2api"
            for index, content, asset, _ in image_results:
                illustration_by_scene[index] = content
                produced.append(asset)
            audio_by_scene = await audio_task
            await report(
                "generating_media",
                68,
                "插图与配音素材已经齐全，正在校验实际节奏",
                {"image_count": len(image_results), "audio_count": len(audio_by_scene)},
            )

            audio_file_by_scene: dict[int, tuple[Path, float]] = {}
            for scene in storyboard["scenes"]:
                index = scene["index"]
                audio_bytes, provider, model, extension = audio_by_scene[index]
                audio_relative = f"{base}/audio/scene-{index + 1:02d}.{extension}"
                voice_provider = provider
                audio_stored = self.assets.write_bytes(audio_relative, audio_bytes)
                audio_path = self.assets.path_for_read(audio_stored.relative_path)
                duration = compositor.duration(audio_path)
                scene["actual_seconds"] = round(duration, 3)
                audio_file_by_scene[index] = (audio_path, duration)
                produced.append(
                    ProducedAsset(
                        "scene_audio",
                        audio_stored,
                        provider,
                        model,
                        index,
                        {"duration_seconds": duration},
                    )
                )

            # TTS duration is the real clock. Re-run rhythm and repetition
            # repair after audio exists so optimistic script estimates cannot
            # leave a visually static scene in the final video.
            storyboard = QualityGate.repair_storyboard(storyboard)
            actual_quality = storyboard["quality_report"]
            if not actual_quality["passed"]:
                errors = [
                    item["message"]
                    for item in actual_quality["issues"]
                    if item["severity"] == "error"
                ]
                raise MediaPipelineError("实际配音节奏检测未通过：" + "；".join(errors))

            scene_videos: list[Path] = []
            await report(
                "generating_media",
                70,
                f"开始渲染 {len(storyboard['scenes'])} 个白板动画分镜",
                {"completed": 0, "total": len(storyboard["scenes"]), "asset_type": "scene_video"},
            )
            for scene in storyboard["scenes"]:
                index = scene["index"]
                audio_path, duration = audio_file_by_scene[index]

                frame_relative = f"{base}/frames/scene-{index + 1:02d}.png"
                frame_cached = self.settings.asset_root / frame_relative
                frame_bytes = self.assets.read_bytes(frame_relative) if frame_cached.is_file() else self.frames.render(scene, illustration_by_scene.get(index))
                frame_quality = QualityGate.assess_frame(frame_bytes, index)
                if not frame_quality.passed:
                    messages = "；".join(issue.message for issue in frame_quality.issues)
                    raise MediaPipelineError(f"第 {index + 1} 个场景画面质量检测未通过：{messages}")
                frame_stored = self.assets.write_bytes(frame_relative, frame_bytes)
                frame_path = self.assets.path_for_read(frame_stored.relative_path)
                visual_provider = "sub2api" if index in illustration_by_scene else "local-graphics"
                visual_model = self.settings.image_model if index in illustration_by_scene else "editorial-card-v1"
                visual_metadata = {
                    "visual_mode": scene["visual_mode"],
                    "quality_report": frame_quality.as_dict(),
                }
                produced.append(ProducedAsset("scene_visual", frame_stored, visual_provider, visual_model, index, visual_metadata))
                special_kind = {
                    "evidence_screenshot": "evidence_screenshot",
                    "data_animation": "data_animation_frame",
                    "relationship_map": "relationship_map",
                }.get(scene["visual_mode"])
                if special_kind:
                    produced.append(ProducedAsset(special_kind, frame_stored, "local", "visual-director-v2", index, visual_metadata))

                subtitle_path = temp_dir / f"scene-{index + 1:02d}.ass"
                compositor.subtitle(subtitle_path, scene["narration"], duration)
                subtitle_stored = self.assets.write_bytes(f"{base}/subtitles/scene-{index + 1:02d}.ass", subtitle_path.read_bytes())
                produced.append(ProducedAsset("scene_subtitle", subtitle_stored, "local", "ass-v1", index, {"duration_seconds": duration}))

                whiteboard_path, annotation = await asyncio.to_thread(
                    self._render_whiteboard, frame_path, temp_dir, index, duration, scene["narration"], scene
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
                    compositor.scene_video,
                    frame_path,
                    audio_path,
                    scene["narration"],
                    scene_output,
                    scene["visual_mode"],
                    whiteboard_path,
                    scene,
                )
                video_stored = self.assets.write_bytes(
                    f"{base}/scenes/scene-{index + 1:02d}.mp4",
                    scene_output.read_bytes(),
                )
                produced.append(ProducedAsset("scene_video", video_stored, "ffmpeg", "h264-aac", index, {"duration_seconds": duration}))
                scene_videos.append(scene_output)
                completed_scenes = len(scene_videos)
                await report(
                    "generating_media",
                    70 + round(16 * completed_scenes / max(1, len(storyboard["scenes"]))),
                    f"白板动画分镜已完成 {completed_scenes}/{len(storyboard['scenes'])}",
                    {
                        "completed": completed_scenes,
                        "total": len(storyboard["scenes"]),
                        "scene_index": index,
                        "asset_type": "scene_video",
                    },
                )

            final_path = temp_dir / "final.mp4"
            await report(
                "composing_video",
                90,
                "全部分镜已经完成，正在拼接最终视频",
                {"scene_count": len(scene_videos)},
            )
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
        visual_plan: dict[str, Any] | None = None,
    ) -> tuple[Path | None, dict[str, Any]]:
        legacy_root = self.settings.whiteboard_renderer_root.resolve()
        renderer = legacy_root / "scripts" / "render_stream_whiteboard.py"
        renderer = renderer.resolve()
        annotation = self._build_whiteboard_annotation(
            index, duration_seconds, narration, (visual_plan or {}).get("beats")
        )
        if not renderer.is_file():
            return None, annotation
        output_dir = temp_dir / f"whiteboard-{index}"
        output_dir.mkdir(parents=True, exist_ok=True)
        annotation_path = output_dir / "annotation.json"
        annotation_path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
        output = output_dir / "render.mp4"
        result = subprocess.run(
            [
                str(self._renderer_python()), str(renderer),
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
    def _renderer_python() -> Path:
        executable = Path(sys.executable).resolve()
        if not executable.is_file():
            raise MediaPipelineError("当前 Python 解释器不可用，无法启动白板渲染器")
        return executable

    @staticmethod
    def _build_whiteboard_annotation(
        index: int,
        duration_seconds: float,
        narration: str,
        beats: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        chunks = [str(item.get("caption", "")).strip() for item in (beats or []) if item.get("caption")]
        if not chunks:
            chunks = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()] or [narration]
        if len(chunks) > 4:
            grouped: list[str] = []
            group_size = max(1, (len(chunks) + 3) // 4)
            for start in range(0, len(chunks), group_size):
                grouped.append("".join(chunks[start:start + group_size]))
            chunks = grouped[:4]
        total_ms = max(2500, round(duration_seconds * 1000))
        # A Douyin scene should not spend its whole duration showing the hand.
        # Reveal the drawing quickly, then let typography, camera motion and the
        # completed composition carry the narration.
        drawing_ms = min(total_ms - 600, max(900, round(total_ms * 0.36)))
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
            duration_ms = max(220, min(span - 80, drawing_ms - cursor + 100))
            beat = beats[position] if beats and position < len(beats) else {}
            elements.append({
                "id": f"event-{position + 1:02d}",
                "label": chunk[:18],
                "sequence": position + 1,
                "narrativeRole": str(beat.get("kind", "sketch")),
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
            "drawingDurationMs": drawing_ms,
            "handScreenRatio": round(drawing_ms / total_ms, 3),
            "elements": elements,
        }
