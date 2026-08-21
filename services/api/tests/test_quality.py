from __future__ import annotations

import io

from PIL import Image, ImageDraw

from app.quality import QualityGate


def test_storyboard_quality_detects_slow_and_duplicate_shots() -> None:
    storyboard = {
        "scenes": [
            {
                "index": 0,
                "planned_seconds": 18,
                "visual_mode": "whiteboard_drawing",
                "scene_type": "timeline",
                "camera_motion": "vertical_pan",
                "beats": [{"caption": "只有一个变化"}],
            },
            {
                "index": 1,
                "planned_seconds": 18,
                "visual_mode": "whiteboard_drawing",
                "scene_type": "timeline",
                "camera_motion": "vertical_pan",
                "beats": [{"caption": "仍然只有一个变化"}],
            },
        ]
    }
    report = QualityGate.audit_storyboard(storyboard)
    assert not report.passed
    assert {issue.code for issue in report.issues} >= {"slow_rhythm", "duplicate_shot"}
    repaired = QualityGate.repair_storyboard(storyboard)
    assert repaired["scenes"][0]["camera_motion"] != repaired["scenes"][1]["camera_motion"]
    assert repaired["quality_report"]["passed"]
    assert all(len(scene["beats"]) == 4 for scene in repaired["scenes"])


def test_frame_quality_rejects_empty_image_and_accepts_clear_drawing() -> None:
    empty = Image.new("RGB", (1080, 1920), "#F5EBD7")
    empty_bytes = io.BytesIO()
    empty.save(empty_bytes, format="PNG")
    assert not QualityGate.assess_frame(empty_bytes.getvalue(), 0).passed

    drawing = empty.copy()
    draw = ImageDraw.Draw(drawing)
    draw.rectangle((180, 300, 900, 1500), fill="#18211D")
    drawing_bytes = io.BytesIO()
    drawing.save(drawing_bytes, format="PNG")
    assert QualityGate.assess_frame(drawing_bytes.getvalue(), 0).passed
