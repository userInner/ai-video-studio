from __future__ import annotations

import io
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from PIL import Image, ImageStat


CAMERA_REPAIR_ORDER = (
    "push_in",
    "vertical_pan",
    "slow_push",
    "horizontal_pan",
    "snap_push",
    "locked_then_push",
)


@dataclass(frozen=True)
class QualityIssue:
    code: str
    scene_index: int
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class QualityReport:
    score: int
    passed: bool
    issues: list[QualityIssue]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "issues": [asdict(issue) for issue in self.issues],
            "metrics": self.metrics,
        }


class QualityGate:
    """Deterministic quality checks used before and during rendering."""

    @staticmethod
    def audit_storyboard(storyboard: dict[str, Any]) -> QualityReport:
        scenes = storyboard.get("scenes") or []
        issues: list[QualityIssue] = []
        hold_values: list[float] = []
        signatures: list[tuple[str, str, str]] = []
        for scene in scenes:
            index = int(scene.get("index", 0))
            beats = scene.get("beats") or []
            seconds = float(scene.get("actual_seconds") or scene.get("pacing_seconds") or scene.get("planned_seconds") or 0)
            hold = seconds / max(1, len(beats))
            hold_values.append(hold)
            if hold > 5.5:
                issues.append(QualityIssue("slow_rhythm", index, f"平均 {hold:.1f} 秒才发生一次视觉变化"))
            if len(beats) < 2 and seconds > 8:
                issues.append(QualityIssue("too_few_beats", index, "长场景缺少视觉节拍", "error"))
            signatures.append(
                (
                    str(scene.get("visual_mode", "")),
                    str(scene.get("scene_type", "")),
                    str(scene.get("camera_motion", "")),
                )
            )

        duplicate_pairs = 0
        for position, (left, right) in enumerate(zip(signatures, signatures[1:]), start=1):
            if left == right:
                duplicate_pairs += 1
                issues.append(QualityIssue("duplicate_shot", position, "与前一镜头的素材、构图和运镜完全重复", "error"))

        signature_counts = Counter(signatures)
        for signature, count in signature_counts.items():
            if count > 2:
                first_index = signatures.index(signature)
                issues.append(
                    QualityIssue(
                        "repeated_shot_pattern",
                        first_index,
                        f"同一种镜头组合在全片出现 {count} 次",
                    )
                )

        error_count = sum(issue.severity == "error" for issue in issues)
        score = max(0, 100 - error_count * 20 - (len(issues) - error_count) * 8)
        return QualityReport(
            score=score,
            passed=error_count == 0 and score >= 76,
            issues=issues,
            metrics={
                "scene_count": len(scenes),
                "visual_beat_count": sum(len(scene.get("beats") or []) for scene in scenes),
                "average_visual_hold_seconds": round(sum(hold_values) / max(1, len(hold_values)), 2),
                "duplicate_shot_pairs": duplicate_pairs,
                "overused_shot_patterns": sum(count > 2 for count in signature_counts.values()),
            },
        )

    @staticmethod
    def repair_storyboard(storyboard: dict[str, Any]) -> dict[str, Any]:
        repaired = deepcopy(storyboard)
        scenes = repaired.get("scenes") or []
        previous_signature: tuple[str, str, str] | None = None
        signature_counts: Counter[tuple[str, str, str]] = Counter()
        for scene in scenes:
            seconds = float(scene.get("actual_seconds") or scene.get("pacing_seconds") or scene.get("planned_seconds") or 0)
            beats = list(scene.get("beats") or [])
            while beats and seconds / len(beats) > 5.5 and len(beats) < 5:
                position = max(range(len(beats)), key=lambda item: len(str(beats[item].get("caption", ""))))
                beat = beats.pop(position)
                caption = str(beat.get("caption", ""))
                split_at = max(1, len(caption) // 2)
                left, right = caption[:split_at], caption[split_at:]
                if not right:
                    right = left
                beats[position:position] = [
                    {**beat, "caption": left, "emphasis": left[:8], "motion": "pop"},
                    {**beat, "caption": right, "emphasis": right[:8], "motion": "underline"},
                ]
            for position, beat in enumerate(beats):
                beat["index"] = position
                beat["duration_ratio"] = round(1 / max(1, len(beats)), 4)
            scene["beats"] = beats
            signature = (
                str(scene.get("visual_mode", "")),
                str(scene.get("scene_type", "")),
                str(scene.get("camera_motion", "")),
            )
            if signature == previous_signature or signature_counts[signature] >= 2:
                current = str(scene.get("camera_motion", "slow_push"))
                position = CAMERA_REPAIR_ORDER.index(current) if current in CAMERA_REPAIR_ORDER else 0
                for offset in range(1, len(CAMERA_REPAIR_ORDER) + 1):
                    candidate = CAMERA_REPAIR_ORDER[(position + offset) % len(CAMERA_REPAIR_ORDER)]
                    candidate_signature = (signature[0], signature[1], candidate)
                    if signature_counts[candidate_signature] < 2 and candidate_signature != previous_signature:
                        scene["camera_motion"] = candidate
                        break
                signature = (signature[0], signature[1], scene["camera_motion"])
            signature_counts[signature] += 1
            previous_signature = signature
        repaired["quality_report"] = QualityGate.audit_storyboard(repaired).as_dict()
        return repaired

    @staticmethod
    def assess_frame(content: bytes, scene_index: int) -> QualityReport:
        image = Image.open(io.BytesIO(content)).convert("RGB").resize((180, 320))
        pixels = list(image.getdata())
        corners = [image.getpixel((2, 2)), image.getpixel((177, 2)), image.getpixel((2, 317)), image.getpixel((177, 317))]
        background = tuple(sum(pixel[channel] for pixel in corners) / 4 for channel in range(3))
        foreground = sum(
            sum(abs(pixel[channel] - background[channel]) for channel in range(3)) > 42
            for pixel in pixels
        ) / max(1, len(pixels))
        grayscale = image.convert("L")
        contrast = float(ImageStat.Stat(grayscale).stddev[0])
        issues: list[QualityIssue] = []
        if foreground < 0.025:
            issues.append(QualityIssue("sparse_frame", scene_index, "有效画面元素过少", "error"))
        elif foreground > 0.78:
            issues.append(QualityIssue("dense_frame", scene_index, "画面过密，缺少视觉焦点"))
        if contrast < 10:
            issues.append(QualityIssue("low_contrast", scene_index, "线稿与背景对比不足", "error"))
        error_count = sum(issue.severity == "error" for issue in issues)
        score = max(0, 100 - error_count * 30 - (len(issues) - error_count) * 12)
        return QualityReport(
            score=score,
            passed=error_count == 0 and score >= 70,
            issues=issues,
            metrics={"foreground_ratio": round(foreground, 4), "contrast_stddev": round(contrast, 2)},
        )

    @staticmethod
    def correction_prompt(report: QualityReport) -> str:
        codes = {issue.code for issue in report.issues}
        corrections: list[str] = []
        if "sparse_frame" in codes:
            corrections.append("Enlarge the focal objects and fill roughly 60 percent of the usable canvas")
        if "dense_frame" in codes:
            corrections.append("Remove decorative details and keep one dominant focal object")
        if "low_contrast" in codes:
            corrections.append("Use darker charcoal outlines with stronger separation from the paper")
        return ". ".join(corrections) or "Improve visual hierarchy and make the main idea immediately readable"
