from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from app.config import Settings
from app.media_pipeline import MediaPipeline, SpeechSynthesizer, VerticalFrameRenderer, VideoCompositor, build_storyboard
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
    assert all(scene["image_prompt"] for scene in storyboard["scenes"])
    assert all("No words" in scene["image_prompt"] for scene in storyboard["scenes"])


def test_vertical_frame_has_douyin_dimensions() -> None:
    scene = build_storyboard(sample_script())["scenes"][0]
    content = VerticalFrameRenderer().render(scene)
    image = Image.open(io.BytesIO(content))
    assert image.size == (1080, 1920)


def test_ass_subtitles_cover_scene_duration(tmp_path: Path) -> None:
    compositor = VideoCompositor(LocalAssetStore(tmp_path / "assets"))
    target = tmp_path / "scene.ass"
    compositor.subtitle(target, "第一句话。第二句话更长一些。", 12.5)
    text = target.read_text(encoding="utf-8")
    assert "Dialogue: 0,0:00:00.00" in text
    assert "0:00:12.50" in text


def test_whiteboard_annotation_sequences_narration_regions() -> None:
    annotation = MediaPipeline._build_whiteboard_annotation(0, 12.5, "第一件事。第二件事。第三件事。")
    assert annotation["sceneDurationMs"] == 12500
    assert len(annotation["elements"]) == 3
    assert [item["sequence"] for item in annotation["elements"]] == [1, 2, 3]
    assert annotation["elements"][0]["region"]["y"] < annotation["elements"][-1]["region"]["y"]


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
