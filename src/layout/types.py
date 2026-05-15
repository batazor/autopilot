from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int

    def scale(self, factor: float) -> Point:
        return Point(int(self.x * factor), int(self.y * factor))

    def offset(self, dx: int, dy: int) -> Point:
        return Point(self.x + dx, self.y + dy)


@dataclass(frozen=True, slots=True)
class Region:
    x: int
    y: int
    w: int
    h: int

    def scale(self, factor: float) -> Region:
        return Region(
            int(self.x * factor),
            int(self.y * factor),
            int(self.w * factor),
            int(self.h * factor),
        )

    def offset(self, dx: int, dy: int) -> Region:
        return Region(self.x + dx, self.y + dy, self.w, self.h)

    def center(self) -> Point:
        return Point(self.x + self.w // 2, self.y + self.h // 2)
