from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.media_pipeline import MediaPipeline, SpeechSynthesizer, VerticalFrameRenderer, VideoCompositor, build_storyboard
from app.quality import QualityGate
from app.storage import LocalAssetStore


def sample_script() -> dict:
    return {
        "sections": [
            {
                "section_type": section_type,
                "title": f"第 {index + 1} 段",
                "narration": "这是第一句。这是第二句。",
                "visual_direction": "画出事实边界、时间线和因果关系。",
                "estimated_seconds": 20,
                "claim_source_urls": [],
            }
            for index, section_type in enumerate(("hook", "context", "evidence", "analysis", "turn", "takeaway", "closing"))
        ]
    }


def test_storyboard_requires_whiteboard_source_for_every_scene() -> None:
    storyboard = build_storyboard(sample_script())
    modes = {scene["visual_mode"] for scene in storyboard["scenes"]}
    assert modes == {"whiteboard_drawing"}
    assert storyboard["version"] == 2
    assert storyboard["visual_system"] == "douyin_whiteboard_v2"
    assert all(scene["image_prompt"] for scene in storyboard["scenes"])
    assert all("No words" in scene["image_prompt"] for scene in storyboard["scenes"])
    assert all(scene["beats"] for scene in storyboard["scenes"])
    assert len({scene["scene_type"] for scene in storyboard["scenes"]}) >= 5


def test_vertical_frame_has_douyin_dimensions() -> None:
    scene = build_storyboard(sample_script())["scenes"][0]
    content = VerticalFrameRenderer().render(scene)
    image = Image.open(io.BytesIO(content))
    assert image.size == (1080, 1920)


def test_sparse_frame_repair_adds_a_visible_focus_band() -> None:
    scene = build_storyboard(sample_script())["scenes"][0]
    regular = VerticalFrameRenderer().render(scene)
    repaired = VerticalFrameRenderer().render(scene, quality_corrections={"sparse_frame"})
    regular_report = QualityGate.assess_frame(regular, scene["index"])
    repaired_report = QualityGate.assess_frame(repaired, scene["index"])
    assert repaired_report.metrics["foreground_ratio"] > regular_report.metrics["foreground_ratio"]
    assert repaired_report.passed


def test_ass_subtitles_cover_scene_duration(tmp_path: Path) -> None:
    compositor = VideoCompositor(LocalAssetStore(tmp_path / "assets"))
    target = tmp_path / "scene.ass"
    compositor.subtitle(target, "第一句话。第二句话更长一些。", 12.5)
    text = target.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.00" in text
    assert "0:00:12.50" in text


def test_caption_overlay_uses_compact_light_paper_card(tmp_path: Path) -> None:
    target = tmp_path / "caption.png"
    VideoCompositor._caption_overlay(
        target,
        "ETF资金并不是只在价格上涨之后才出现，",
        beat_index=1,
        beat_count=4,
    )
    overlay = Image.open(target).convert("RGBA")
    alpha_bbox = overlay.getchannel("A").getbbox()
    assert alpha_bbox is not None
    assert alpha_bbox[2] - alpha_bbox[0] < 980
    assert alpha_bbox[3] - alpha_bbox[1] < 240
    card_pixel = overlay.getpixel((540, 1620))
    assert card_pixel[0] > 220
    assert card_pixel[1] > 220
    assert card_pixel[2] > 210


def test_whiteboard_annotation_sequences_narration_regions() -> None:
    annotation = MediaPipeline._build_whiteboard_annotation(0, 12.5, "第一件事。第二件事。第三件事。")
    assert annotation["sceneDurationMs"] == 12500
    assert annotation["handScreenRatio"] <= 0.38
    assert annotation["drawingDurationMs"] < annotation["sceneDurationMs"] / 2
    assert len(annotation["elements"]) == 3
    assert [item["sequence"] for item in annotation["elements"]] == [1, 2, 3]
    assert annotation["elements"][0]["region"]["y"] < annotation["elements"][-1]["region"]["y"]


def test_whiteboard_renderer_uses_current_python_runtime() -> None:
    assert MediaPipeline._renderer_python() == Path(sys.executable).resolve()
    assert MediaPipeline._renderer_python().is_file()


def test_visual_director_changes_camera_and_layout_by_section() -> None:
    storyboard = build_storyboard(sample_script())
    scenes = storyboard["scenes"]
    assert scenes[0]["scene_type"] == "hook_burst"
    assert scenes[0]["camera_motion"] == "push_in"
    assert scenes[1]["scene_type"] == "timeline"
    assert scenes[1]["camera_motion"] == "vertical_pan"
    assert all(scene["rules"]["max_static_seconds"] <= 1.5 for scene in scenes)


def test_douyin_camera_and_beat_overlays_render_with_ffmpeg(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    whiteboard = tmp_path / "whiteboard.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "scene.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
            "color=c=#F5EBD7:s=540x960:d=1.2:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(whiteboard),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
            "anullsrc=r=32000:cl=mono", "-t", "1.2", str(audio),
        ],
        check=True,
    )
    compositor = VideoCompositor(LocalAssetStore(tmp_path / "assets"))
    compositor.scene_video(
        tmp_path / "unused.png",
        audio,
        "为什么上涨8%？因为资金同时进入。",
        output,
        "whiteboard_drawing",
        whiteboard,
        {
            "scene_type": "hook_burst",
            "camera_motion": "horizontal_pan",
            "beats": [
                {"caption": "为什么上涨8%？", "emphasis": "8%", "kind": "question"},
                {"caption": "因为资金同时进入。", "emphasis": "资金进入", "kind": "causal"},
            ],
        },
    )
    assert output.is_file()
    assert compositor.duration(output) >= 1.1


def test_storyboard_selects_evidence_data_and_relationship_visuals() -> None:
    source_url = "https://example.com/report"
    script = {
        "sections": [
            {
                "section_type": "evidence",
                "title": "先看原始证据",
                "narration": "公开文件给出了明确结论。",
                "visual_direction": "展示来源页面和关键结论。",
                "estimated_seconds": 12,
                "claim_source_urls": [source_url],
                "data_points": [],
                "entities": [],
                "relationships": [],
            },
            {
                "section_type": "context",
                "title": "背景过渡",
                "narration": "先把这件事放回完整的时间背景。",
                "visual_direction": "绘制简洁的时间背景。",
                "estimated_seconds": 12,
                "claim_source_urls": [],
                "data_points": [],
                "entities": [],
                "relationships": [],
            },
            {
                "section_type": "evidence",
                "title": "数字发生变化",
                "narration": "金额从10亿元上升到25亿元。",
                "visual_direction": "用柱状图比较两个数字。",
                "estimated_seconds": 12,
                "claim_source_urls": [source_url],
                "data_points": [
                    {"label": "之前", "value": 10, "display_value": "10亿", "unit": "亿", "source_url": source_url},
                    {"label": "之后", "value": 25, "display_value": "25亿", "unit": "亿", "source_url": source_url},
                ],
                "entities": [],
                "relationships": [],
            },
            {
                "section_type": "takeaway",
                "title": "数字意味着什么",
                "narration": "数字背后还需要继续解释影响。",
                "visual_direction": "用白板画出影响方向。",
                "estimated_seconds": 12,
                "claim_source_urls": [],
                "data_points": [],
                "entities": [],
                "relationships": [],
            },
            {
                "section_type": "analysis",
                "title": "人物关系",
                "narration": "甲公司通过乙基金影响丙项目。",
                "visual_direction": "展示三方关系。",
                "estimated_seconds": 12,
                "claim_source_urls": [source_url],
                "data_points": [],
                "entities": ["甲公司", "乙基金", "丙项目"],
                "relationships": [
                    {"source": "甲公司", "target": "乙基金", "label": "出资"},
                    {"source": "乙基金", "target": "丙项目", "label": "投资"},
                ],
            },
        ]
    }
    sources = [
        {
            "title": "公开报告原文",
            "url": source_url,
            "publisher": "示例机构",
            "published_at": "2026-08-21",
            "credibility": "primary",
            "summary": "报告披露了关键事实。",
        }
    ]
    storyboard = build_storyboard(script, sources)
    modes = {scene["visual_mode"] for scene in storyboard["scenes"]}
    assert {"evidence_screenshot", "data_animation", "relationship_map"} <= modes
    for scene in storyboard["scenes"]:
        content = VerticalFrameRenderer().render(scene)
        assert Image.open(io.BytesIO(content)).size == (1080, 1920)
        if scene["visual_mode"] != "whiteboard_drawing":
            assert QualityGate.assess_frame(content, scene["index"]).passed


def test_local_qwen_requires_design_and_base_models(tmp_path: Path) -> None:
    python = tmp_path / "python"
    design = tmp_path / "design"
    base = tmp_path / "base"
    python.touch()
    design.mkdir()
    settings = Settings(
        prefer_local_qwen_tts=True,
        qwen_tts_python=python,
        qwen_tts_checkpoint=design,
        qwen_tts_base_checkpoint=base,
    )
    speech = SpeechSynthesizer(settings)
    assert not speech.local_qwen_available
    base.mkdir()
    assert speech.local_qwen_available
