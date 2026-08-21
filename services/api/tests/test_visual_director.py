from __future__ import annotations

from app.visual_director import audit_storyboard, direct_scene, split_section_narration


def test_visual_director_extracts_number_and_question_beats() -> None:
    plan = direct_scene(
        {
            "section_type": "hook",
            "narration": "为什么一天上涨8%？真正的原因并不是市场情绪。",
        },
        0,
    )
    assert plan["scene_type"] == "hook_burst"
    assert plan["camera_motion"] == "push_in"
    assert plan["beats"][0]["kind"] == "question"
    assert "8" in plan["beats"][0]["emphasis"]
    assert plan["rules"]["max_hand_ratio"] <= 0.38


def test_storyboard_audit_rejects_repeated_layouts() -> None:
    issues = audit_storyboard(
        {
            "scenes": [
                {"index": 0, "scene_type": "timeline", "beats": [{"caption": "第一幕"}], "rules": {"max_hand_ratio": 0.3}},
                {"index": 1, "scene_type": "timeline", "beats": [{"caption": "第二幕"}], "rules": {"max_hand_ratio": 0.3}},
            ]
        }
    )
    assert any("repeat a layout" in issue for issue in issues)


def test_long_script_section_is_split_into_image_sized_scenes() -> None:
    narration = "第一句话解释事情的背景。" * 12
    scenes = split_section_narration(narration)
    assert len(scenes) > 1
    assert all(len(scene) <= 78 for scene in scenes)
    assert "".join(scenes) == narration
