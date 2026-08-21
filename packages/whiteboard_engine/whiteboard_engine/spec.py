from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderSpec:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    safe_top: int = 180
    safe_bottom: int = 280
    background: str = "#F7F6F0"
    stroke: str = "#17211D"

    def validate(self) -> None:
        if (self.width, self.height) != (1080, 1920):
            raise ValueError("first production profile must be Douyin 1080×1920")
        if self.fps not in {25, 30, 50, 60}:
            raise ValueError("unsupported frame rate")
        if self.safe_top + self.safe_bottom >= self.height:
            raise ValueError("safe areas leave no drawable canvas")
