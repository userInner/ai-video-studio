from __future__ import annotations

import re
from typing import Any


SCENE_TYPE_BY_SECTION = {
    "hook": "hook_burst",
    "context": "timeline",
    "evidence": "evidence_stack",
    "analysis": "causal_chain",
    "turn": "reversal",
    "takeaway": "checklist",
    "closing": "takeaway_stamp",
}

CAMERA_BY_SCENE_TYPE = {
    "hook_burst": "push_in",
    "timeline": "vertical_pan",
    "evidence_stack": "slow_push",
    "causal_chain": "horizontal_pan",
    "reversal": "snap_push",
    "checklist": "slow_push",
    "takeaway_stamp": "locked_then_push",
}

ALTERNATE_SCENE_TYPE_BY_SECTION = {
    "hook": "evidence_stack",
    "context": "causal_chain",
    "evidence": "timeline",
    "analysis": "evidence_stack",
    "turn": "causal_chain",
    "takeaway": "evidence_stack",
    "closing": "checklist",
}

BEAT_KIND_PATTERNS = (
    ("question", re.compile(r"[？?]|为什么|怎么会|到底")),
    ("big_number", re.compile(r"\d|万|亿|百分之|倍")),
    ("contrast", re.compile(r"但是|然而|却|反而|并不是|不是.+而是")),
    ("causal", re.compile(r"因为|因此|所以|导致|意味着|结果")),
)


def _split_beats(narration: str, maximum: int = 5) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()]
    if not sentences:
        sentences = [narration.strip()]

    beats: list[str] = []
    for sentence in sentences:
        if len(sentence) <= 28:
            beats.append(sentence)
            continue
        clauses = [item.strip() for item in re.split(r"(?<=[，：])", sentence) if item.strip()]
        if len(clauses) > 1:
            beats.extend(clauses)
        else:
            beats.extend(sentence[start:start + 28] for start in range(0, len(sentence), 28))

    if len(beats) <= maximum:
        return beats
    grouped: list[str] = []
    group_size = (len(beats) + maximum - 1) // maximum
    for start in range(0, len(beats), group_size):
        grouped.append("".join(beats[start:start + group_size]))
    return grouped[:maximum]


def _beat_kind(text: str, position: int) -> str:
    for kind, pattern in BEAT_KIND_PATTERNS:
        if pattern.search(text):
            return kind
    return "sketch" if position % 2 == 0 else "keyword"


def _emphasis(text: str) -> str:
    number = re.search(r"(?:\d[\d,.]*(?:%|万|亿|倍)?|百分之\S{1,5})", text)
    if number:
        return number.group(0)[:12]
    clean = re.sub(r"[，。！？；：、,.!?;:\s]", "", text)
    return clean[: min(8, len(clean))]


def _inferred_data_points(narration: str) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for match in re.finditer(r"(?P<value>\d[\d,.]*)(?P<unit>%|万|亿|倍|元|美元)?", narration):
        raw = match.group("value")
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        start = max(0, match.start() - 8)
        label = re.sub(r"[，。！？；：、,.!?;:\s]", "", narration[start:match.start()])[-8:] or "关键数据"
        unit = match.group("unit") or ""
        points.append(
            {
                "label": label,
                "value": value,
                "display_value": f"{raw}{unit}",
                "unit": unit,
                "source_url": "",
            }
        )
        if len(points) == 4:
            break
    return points


def choose_visual_mode(
    section: dict[str, Any],
    direction: dict[str, Any],
    index: int,
    previous_visual_mode: str | None,
) -> str:
    data_points = section.get("data_points") or _inferred_data_points(str(section.get("narration", "")))
    relationships = section.get("relationships") or []
    sources = section.get("claim_source_urls") or []
    scene_type = direction["scene_type"]
    candidate = "whiteboard_drawing"
    if data_points and scene_type in {"timeline", "evidence_stack"}:
        candidate = "data_animation"
    elif relationships and scene_type in {"causal_chain", "reversal"}:
        candidate = "relationship_map"
    elif sources and scene_type == "evidence_stack":
        candidate = "evidence_screenshot"

    # Special visuals are accents, not the product's entire visual identity.
    if candidate != "whiteboard_drawing" and previous_visual_mode not in {None, "whiteboard_drawing"}:
        return "whiteboard_drawing"
    return candidate


def split_section_narration(narration: str, target_chars: int = 56, max_chars: int = 78) -> list[str]:
    """Split a long script section into image-sized visual scenes."""

    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", narration) if item.strip()]
    if not sentences:
        return [narration.strip()]

    units: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue
        clauses = [item.strip() for item in re.split(r"(?<=[，：])", sentence) if item.strip()]
        for clause in clauses:
            units.extend(clause[start:start + max_chars] for start in range(0, len(clause), max_chars))

    scenes: list[str] = []
    current = ""
    for unit in units:
        if current and (len(current) >= target_chars or len(current) + len(unit) > max_chars):
            scenes.append(current)
            current = unit
        else:
            current += unit
    if current:
        scenes.append(current)
    return scenes


def direct_scene(section: dict[str, Any], index: int, previous_scene_type: str | None = None) -> dict[str, Any]:
    """Turn a script section into a constrained Douyin visual plan.

    The plan is deterministic so rendering stays reliable without a second
    model call. A model-generated plan can replace this later while keeping
    the same contract and quality audit.
    """

    section_type = str(section.get("section_type", "analysis"))
    scene_type = SCENE_TYPE_BY_SECTION.get(section_type, "causal_chain")
    if scene_type == previous_scene_type:
        scene_type = ALTERNATE_SCENE_TYPE_BY_SECTION.get(section_type, "evidence_stack")

    chunks = _split_beats(str(section.get("narration", "")))
    weights = [max(1, len(chunk)) for chunk in chunks]
    total = sum(weights) or 1
    beats = [
        {
            "index": position,
            "caption": chunk,
            "kind": _beat_kind(chunk, position),
            "emphasis": _emphasis(chunk),
            "duration_ratio": round(weight / total, 4),
            "motion": ("pop", "draw_fast", "underline", "circle")[position % 4],
        }
        for position, (chunk, weight) in enumerate(zip(chunks, weights))
    ]
    return {
        "scene_type": scene_type,
        "camera_motion": CAMERA_BY_SCENE_TYPE[scene_type],
        "rhythm_profile": "douyin_explainer_v2",
        "beats": beats,
        "data_points": section.get("data_points") or _inferred_data_points(str(section.get("narration", ""))),
        "entities": section.get("entities") or [],
        "relationships": section.get("relationships") or [],
        "evidence_source_urls": section.get("claim_source_urls") or [],
        "rules": {
            "max_static_seconds": 1.5,
            "target_change_seconds": 2.2,
            "max_hand_ratio": 0.38,
            "caption_max_chars": 28,
        },
    }


def audit_storyboard(storyboard: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    scenes = storyboard.get("scenes") or []
    for scene in scenes:
        beats = scene.get("beats") or []
        if not 1 <= len(beats) <= 5:
            issues.append(f"scene {scene.get('index')} must contain 1-5 visual beats")
        if scene.get("rules", {}).get("max_hand_ratio", 1) > 0.4:
            issues.append(f"scene {scene.get('index')} shows the drawing hand for too long")
        for beat in beats:
            if len(beat.get("caption", "")) > 56:
                issues.append(f"scene {scene.get('index')} contains an overlong visual beat")
    for left, right in zip(scenes, scenes[1:]):
        if left.get("scene_type") == right.get("scene_type"):
            issues.append(f"scenes {left.get('index')} and {right.get('index')} repeat a layout")
    return issues
