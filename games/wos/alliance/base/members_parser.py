"""Parse the Alliance Members screen.

The screen is mostly regular geometry: rank headers are wide blue rows, expanded
groups show member cards in a two-column grid, and the counters/text fields are
stable relative to those anchors.  This module keeps that geometry in Python so
the eventual scanner can combine OCR + taps + swipes without growing a fragile
YAML scenario.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Point, Region

_RANK_FALLBACK = (4, 3, 2, 1, 0)
_GREEN_LO = np.array((45, 90, 90), dtype=np.uint8)
_GREEN_HI = np.array((85, 255, 255), dtype=np.uint8)
_HEADER_BLUE_LO = np.array((100, 60, 120), dtype=np.uint8)
_HEADER_BLUE_HI = np.array((115, 180, 255), dtype=np.uint8)
_CARD_BLUE_LO = np.array((92, 80, 120), dtype=np.uint8)
_CARD_BLUE_HI = np.array((108, 230, 255), dtype=np.uint8)
_R5_YELLOW_LO = np.array((15, 80, 140), dtype=np.uint8)
_R5_YELLOW_HI = np.array((40, 255, 255), dtype=np.uint8)


@dataclass(frozen=True, slots=True)
class OcrRegionSpec:
    id: str
    region: Region
    preprocess: str | None = None


@dataclass(frozen=True, slots=True)
class RankGroup:
    rank: int
    label: str
    count: int
    max_count: int
    expanded: bool
    online_marker: bool
    bbox: Region
    tap: Point


@dataclass(frozen=True, slots=True)
class MemberEntry:
    rank: int
    name: str
    power: int
    level: int
    status: str
    online: bool
    last_online_text: str
    last_online_seconds: int | None
    bbox: Region
    tap: Point


@dataclass(frozen=True, slots=True)
class AllianceMembersSnapshot:
    online_count: int
    total_count: int
    ranks: dict[int, RankGroup] = field(default_factory=dict)
    members: list[MemberEntry] = field(default_factory=list)

    def rank_count(self, rank: int) -> int:
        group = self.ranks.get(rank)
        return group.count if group else 0


class OcrClientLike(Protocol):
    async def ocr_regions(
        self,
        image: np.ndarray,
        regions: list[Region],
        *,
        region_ids: list[str] | None = None,
        region_preprocess: list[str | None] | None = None,
        region_digit_count: list[int | None] | None = None,
        region_digit_x0: list[int] | None = None,
    ) -> list[Any]:
        ...


@dataclass(frozen=True, slots=True)
class _HeaderAnchor:
    index: int
    bbox: Region


@dataclass(frozen=True, slots=True)
class _CardAnchor:
    index: int
    bbox: Region


def _clip_region(r: Region, w: int, h: int) -> Region:
    x1 = max(0, min(int(r.x), w))
    y1 = max(0, min(int(r.y), h))
    x2 = max(x1, min(int(r.x + r.w), w))
    y2 = max(y1, min(int(r.y + r.h), h))
    return Region(x1, y1, x2 - x1, y2 - y1)


def _region_from_pct(x: float, y: float, w: float, h: float, frame_w: int, frame_h: int) -> Region:
    return Region(
        int(round(x / 100.0 * frame_w)),
        int(round(y / 100.0 * frame_h)),
        int(round(w / 100.0 * frame_w)),
        int(round(h / 100.0 * frame_h)),
    )


def _subregion(parent: Region, x: int, y: int, w: int, h: int) -> Region:
    return Region(parent.x + x, parent.y + y, w, h)


def _crop(image: np.ndarray, r: Region) -> np.ndarray:
    h, w = image.shape[:2]
    rr = _clip_region(r, w, h)
    return image[rr.y : rr.y + rr.h, rr.x : rr.x + rr.w]


def _color_share(image: np.ndarray, r: Region, lo: np.ndarray, hi: np.ndarray) -> float:
    patch = _crop(image, r)
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lo, hi)
    return float(np.count_nonzero(mask)) / float(mask.size or 1)


def _green_share(image: np.ndarray, r: Region) -> float:
    return _color_share(image, r, _GREEN_LO, _GREEN_HI)


def _blue_card_share(image: np.ndarray, r: Region) -> float:
    return _color_share(image, r, _CARD_BLUE_LO, _CARD_BLUE_HI)


def _parse_int(text: object) -> int:
    digits = re.sub(r"\D+", "", str(text or ""))
    return int(digits) if digits else 0


def _parse_power(text: object) -> int:
    raw = str(text or "").strip().replace(",", "")
    match = re.search(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<suffix>[KMB])?", raw, re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group("num"))
    suffix = (match.group("suffix") or "").upper()
    scale = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(value * scale)


def _parse_count_pair(text: object) -> tuple[int, int]:
    nums = [int(n) for n in re.findall(r"\d+", str(text or ""))]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], 0
    return 0, 0


def _parse_rank(text: object, fallback: int) -> int:
    match = re.search(r"\bR\s*([0-5])\b", str(text or ""), re.IGNORECASE)
    return int(match.group(1)) if match else fallback


def _clean_name(text: object) -> str:
    value = str(text or "").replace("\n", " ")
    return " ".join(value.split())


def _parse_last_online(status: object) -> tuple[bool, str, int | None]:
    text = _clean_name(status)
    lower = text.lower()
    if "online" in lower:
        return True, "Online", 0
    match = re.search(
        r"(?P<num>\d+)\s*(?P<unit>second|minute|hour|day|week|month)(?:\s*\(s\)|s)?\s*ago",
        lower,
    )
    if not match:
        return False, text, None
    amount = int(match.group("num"))
    unit = match.group("unit")
    scale = {
        "second": 1,
        "minute": 60,
        "hour": 60 * 60,
        "day": 24 * 60 * 60,
        "week": 7 * 24 * 60 * 60,
        "month": 30 * 24 * 60 * 60,
    }[unit]
    return False, text, amount * scale


class AllianceMembersParser:
    """Frame parser for Alliance Members and expanded rank groups."""

    def ocr_region_specs(self, image: np.ndarray) -> list[OcrRegionSpec]:
        h, w = image.shape[:2]
        specs: list[OcrRegionSpec] = [
            OcrRegionSpec(
                "summary.online",
                _region_from_pct(25.0, 14.1, 50.0, 4.1, w, h),
                "fast_line",
            )
        ]

        if self._has_r5_card(image):
            specs.extend(
                [
                    OcrRegionSpec("r5.name", Region(275, 302, 270, 40), "word_line"),
                    OcrRegionSpec("r5.power", Region(280, 346, 130, 36), "fast_line"),
                    OcrRegionSpec("r5.level", Region(500, 346, 105, 36), "fast_line"),
                    OcrRegionSpec("r5.status", Region(84, 386, 130, 34), "word_line"),
                ]
            )

        headers = self._rank_headers(image)
        for header in headers:
            y = header.bbox.y
            specs.extend(
                [
                    OcrRegionSpec(
                        f"rank_header.{header.index}.rank",
                        Region(58, y + 7, 55, 38),
                        "fast_line",
                    ),
                    OcrRegionSpec(
                        f"rank_header.{header.index}.label",
                        Region(118, y + 7, 330, 38),
                        "fast_line",
                    ),
                    OcrRegionSpec(
                        f"rank_header.{header.index}.count",
                        Region(558, y + 9, 85, 36),
                        "fast_line",
                    ),
                ]
            )

        for card in self._member_cards(image, headers):
            specs.extend(
                [
                    OcrRegionSpec(f"member.{card.index}.name", _subregion(card.bbox, 124, 14, 185, 35), "word_line"),
                    OcrRegionSpec(f"member.{card.index}.power", _subregion(card.bbox, 126, 51, 112, 32), "fast_line"),
                    OcrRegionSpec(f"member.{card.index}.level", _subregion(card.bbox, 160, 86, 100, 32), "fast_line"),
                    OcrRegionSpec(f"member.{card.index}.status", _subregion(card.bbox, 28, 94, 105, 30), "word_line"),
                ]
            )
        return specs

    async def parse_with_ocr(
        self,
        image: np.ndarray,
        ocr_client: OcrClientLike,
        *,
        expanded_rank_hint: int | None = None,
    ) -> AllianceMembersSnapshot:
        specs = self.ocr_region_specs(image)
        results = await ocr_client.ocr_regions(
            image,
            [s.region for s in specs],
            region_ids=[s.id for s in specs],
            region_preprocess=[s.preprocess for s in specs],
        )
        text = {str(r.region_id): str(r.text or "") for r in results}
        return self.parse(image, text, expanded_rank_hint=expanded_rank_hint)

    def parse(
        self,
        image: np.ndarray,
        ocr_text: dict[str, object],
        *,
        expanded_rank_hint: int | None = None,
    ) -> AllianceMembersSnapshot:
        online_count, total_count = _parse_count_pair(ocr_text.get("summary.online"))
        headers = self._rank_headers(image)
        expanded_rank = self._expanded_rank(image, headers, ocr_text)
        if expanded_rank is None:
            expanded_rank = expanded_rank_hint

        ranks: dict[int, RankGroup] = {}
        if self._has_r5_card(image):
            ranks[5] = RankGroup(
                rank=5,
                label="Alliance Rank 5",
                count=1,
                max_count=1,
                expanded=True,
                online_marker=self._r5_online(image, ocr_text),
                bbox=Region(64, 256, 590, 180),
                tap=Point(359, 346),
            )

        for header in headers:
            fallback = _RANK_FALLBACK[header.index] if header.index < len(_RANK_FALLBACK) else 0
            rank = _parse_rank(ocr_text.get(f"rank_header.{header.index}.rank"), fallback)
            label = _clean_name(ocr_text.get(f"rank_header.{header.index}.label")) or f"Alliance Rank {rank}"
            count, max_count = _parse_count_pair(ocr_text.get(f"rank_header.{header.index}.count"))
            ranks[rank] = RankGroup(
                rank=rank,
                label=label,
                count=count,
                max_count=max_count,
                expanded=rank == expanded_rank,
                online_marker=_green_share(image, Region(502, header.bbox.y + 12, 30, 30)) > 0.08,
                bbox=header.bbox,
                tap=header.bbox.center(),
            )

        members: list[MemberEntry] = []
        if self._has_r5_card(image):
            members.append(self._parse_r5_member(image, ocr_text))
        for card in self._member_cards(image, headers):
            rank = expanded_rank if expanded_rank is not None else 0
            name = _clean_name(ocr_text.get(f"member.{card.index}.name"))
            power = _parse_power(ocr_text.get(f"member.{card.index}.power"))
            level = _parse_int(ocr_text.get(f"member.{card.index}.level"))
            status = _clean_name(ocr_text.get(f"member.{card.index}.status"))
            online, last_online_text, last_online_seconds = _parse_last_online(status)
            if not online and _green_share(image, _subregion(card.bbox, 28, 94, 105, 30)) > 0.08:
                online = True
                last_online_text = "Online"
                last_online_seconds = 0
            if name or power or level or status:
                members.append(
                    MemberEntry(
                        rank=rank,
                        name=name,
                        power=power,
                        level=level,
                        status=status,
                        online=online,
                        last_online_text=last_online_text,
                        last_online_seconds=last_online_seconds,
                        bbox=card.bbox,
                        tap=card.bbox.center(),
                    )
                )

        return AllianceMembersSnapshot(
            online_count=online_count,
            total_count=total_count,
            ranks=ranks,
            members=members,
        )

    def _has_r5_card(self, image: np.ndarray) -> bool:
        return _color_share(image, Region(64, 256, 590, 180), _R5_YELLOW_LO, _R5_YELLOW_HI) > 0.25

    def _r5_online(self, image: np.ndarray, ocr_text: dict[str, object]) -> bool:
        status = str(ocr_text.get("r5.status") or "").lower()
        return "online" in status or _green_share(image, Region(84, 386, 130, 34)) > 0.08

    def _parse_r5_member(self, image: np.ndarray, ocr_text: dict[str, object]) -> MemberEntry:
        status = _clean_name(ocr_text.get("r5.status"))
        online = self._r5_online(image, ocr_text)
        _, last_online_text, last_online_seconds = _parse_last_online(status)
        if online:
            last_online_text = "Online"
            last_online_seconds = 0
        return MemberEntry(
            rank=5,
            name=_clean_name(ocr_text.get("r5.name")),
            power=_parse_power(ocr_text.get("r5.power")),
            level=_parse_int(ocr_text.get("r5.level")),
            status=status,
            online=online,
            last_online_text=last_online_text,
            last_online_seconds=last_online_seconds,
            bbox=Region(64, 256, 590, 180),
            tap=Point(359, 346),
        )

    def _rank_headers(self, image: np.ndarray) -> list[_HeaderAnchor]:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, _HEADER_BLUE_LO, _HEADER_BLUE_HI)
        x0, x1 = 45, min(675, image.shape[1])
        runs: list[tuple[int, int, float]] = []
        start: int | None = None
        max_share = 0.0
        for y in range(250, image.shape[0]):
            row = mask[y, x0:x1]
            share = float(np.count_nonzero(row)) / float(row.size or 1)
            if share > 0.25:
                if start is None:
                    start = y
                    max_share = share
                else:
                    max_share = max(max_share, share)
                continue
            if start is not None:
                runs.append((start, y - 1, max_share))
                start = None
                max_share = 0.0
        if start is not None:
            runs.append((start, image.shape[0] - 1, max_share))

        boxes: list[Region] = []
        for y0, y1, share in runs:
            height = y1 - y0 + 1
            if 34 <= height <= 72 and share > 0.98:
                boxes.append(Region(46, y0, 627, height))
        return [_HeaderAnchor(i, box) for i, box in enumerate(boxes[:5])]

    def _expanded_rank(
        self,
        image: np.ndarray,
        headers: list[_HeaderAnchor],
        ocr_text: dict[str, object],
    ) -> int | None:
        cards = self._member_cards(image, headers)
        if not cards:
            return None
        first_card_y = min(c.bbox.y for c in cards)
        candidate: _HeaderAnchor | None = None
        for header in headers:
            if header.bbox.y < first_card_y:
                candidate = header
        if candidate is None:
            return None
        fallback = _RANK_FALLBACK[candidate.index] if candidate.index < len(_RANK_FALLBACK) else 0
        return _parse_rank(ocr_text.get(f"rank_header.{candidate.index}.rank"), fallback)

    def _member_cards(self, image: np.ndarray, headers: list[_HeaderAnchor]) -> list[_CardAnchor]:
        anchors: list[_CardAnchor] = []
        if headers:
            for i, header in enumerate(headers):
                next_y = headers[i + 1].bbox.y if i + 1 < len(headers) else image.shape[0]
                available_h = next_y - header.bbox.y
                if available_h < 140:
                    continue
                y = header.bbox.y + 73
                while y + 110 < next_y - 8:
                    for x in (27, 369):
                        card = Region(x, y, 326, 128)
                        if _blue_card_share(image, card) > 0.20:
                            anchors.append(_CardAnchor(0, card))
                    y += 149

        anchors.extend(self._floating_member_cards(image, anchors))
        return [
            _CardAnchor(i, anchor.bbox)
            for i, anchor in enumerate(sorted(anchors, key=lambda a: (a.bbox.y, a.bbox.x)))
        ]

    def _floating_member_cards(
        self,
        image: np.ndarray,
        existing: list[_CardAnchor],
    ) -> list[_CardAnchor]:
        """Find cards after scrolling, when the expanded rank header may be offscreen."""
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, _CARD_BLUE_LO, _CARD_BLUE_HI)
        start_y = max(250, min((a.bbox.y for a in existing), default=250) - 20)
        runs: list[tuple[int, int]] = []
        start: int | None = None
        for y in range(start_y, image.shape[0]):
            share = float(np.count_nonzero(mask[y, 27:695])) / 668.0
            if share > 0.45:
                if start is None:
                    start = y
                continue
            if start is not None:
                runs.append((start, y - 1))
                start = None
        if start is not None:
            runs.append((start, image.shape[0] - 1))

        merged: list[tuple[int, int]] = []
        for y0, y1 in runs:
            if merged and y0 - merged[-1][1] <= 12:
                merged[-1] = (merged[-1][0], y1)
            else:
                merged.append((y0, y1))

        found: list[_CardAnchor] = []
        for y0, y1 in merged:
            if y1 - y0 + 1 < 70:
                continue
            y = max(0, y0 - 4)
            for x in (27, 369):
                candidate = _CardAnchor(0, Region(x, y, 326, 128))
                if _blue_card_share(image, candidate.bbox) <= 0.20:
                    continue
                if any(abs(anchor.bbox.x - x) < 16 and abs(anchor.bbox.y - y) < 60 for anchor in existing + found):
                    continue
                found.append(candidate)
        return found


def merge_members_by_name(entries: list[MemberEntry]) -> dict[str, MemberEntry]:
    """Merge scan fragments, preferring the newest non-empty record per member name."""
    out: dict[str, MemberEntry] = {}
    for entry in entries:
        key = re.sub(r"\s+", " ", entry.name.strip()).casefold()
        if not key:
            continue
        prev = out.get(key)
        if prev is None or (entry.power, entry.level, entry.status) > (prev.power, prev.level, prev.status):
            out[key] = entry
    return out
