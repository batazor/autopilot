from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from analysis import overlay_engine
from layout.types import Region
from ocr.client import OCRResult


def _patch_overlay_ocr_getter(monkeypatch: Any, client: Any) -> None:
    """Batch OCR path calls :func:`services.get_ocr_client` — not ``OcrClient(...)``."""
    monkeypatch.setattr("services.get_ocr_client", lambda: client)
@pytest.mark.asyncio
async def test_overlay_action_text_attaches_push_scenario(monkeypatch: Any) -> None:
    """Regression: worker enqueues overlay pushes from ``payload['pushScenario']``.
    Text rules must attach ``pushScenario`` like findIcon/color_check do.
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    rule = {
        "name": "chapter.task.present",
        "region": "chapter.task",
        "action": "text",
        "screens": ["main_city"],
        "pushScenario": [
            {"name": "chapter_task_router", "priority": 70000, "ttl": "20s"},
        ],
    }

    class _StubOcr:
        async def ocr_region(self, _image_bgr: Any, _region_px: Region, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="Bunk Beds in Shelter 2", confidence=0.95)

        async def ocr_regions(
            self,
            _image_bgr: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            return [
                OCRResult(region_id=rid, text="Bunk Beds in Shelter 2", confidence=0.95)
                for rid in ids
            ]

    _patch_overlay_ocr_getter(monkeypatch, _StubOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        [rule],
        current_screen="main_city",
        rule_eval_state=None,
    )
    row = out.get("chapter.task.present")
    assert isinstance(row, dict)
    assert row.get("matched") is True
    pu = row.get("pushScenario")
    assert isinstance(pu, list) and len(pu) >= 1
    assert pu[0].get("type") == "chapter_task_router"


@pytest.mark.asyncio
async def test_overlay_action_text_skipped_when_screen_not_allowed(monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    rule = {
        "name": "chapter.task.present",
        "region": "chapter.task",
        "action": "text",
        "screens": ["main_city"],
        "pushScenario": [{"name": "chapter_task_router", "priority": 70000}],
    }

    class _StubOcr:
        async def ocr_region(self, _image_bgr: Any, _region_px: Region, **_kwargs: Any) -> OCRResult:
            raise AssertionError("OCR must not run when screen gate fails")

        async def ocr_regions(
            self,
            _image_bgr: Any,
            _regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            raise AssertionError("OCR must not run when screen gate fails")

    _patch_overlay_ocr_getter(monkeypatch, _StubOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        [rule],
        current_screen="mail",
        rule_eval_state=None,
    )
    assert "chapter.task.present" not in out


@pytest.mark.asyncio
async def test_overlay_screen_gate_is_case_insensitive(monkeypatch: Any) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
    rule = {
        "name": "chapter.task.present",
        "region": "chapter.task",
        "action": "text",
        "screens": ["main_city"],
        "pushScenario": [{"name": "chapter_task_router", "priority": 70000}],
    }

    class _StubOcr:
        async def ocr_region(self, _image_bgr: Any, _region_px: Region, **_kwargs: Any) -> OCRResult:
            return OCRResult(region_id="r0", text="x", confidence=0.95)

        async def ocr_regions(
            self,
            _image_bgr: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            return [
                OCRResult(region_id=rid, text="x", confidence=0.95) for rid in ids
            ]

    _patch_overlay_ocr_getter(monkeypatch, _StubOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr,
        area_doc,
        repo_root,
        [rule],
        current_screen="MAIN_CITY",
        rule_eval_state=None,
    )
    row = out.get("chapter.task.present")
    assert isinstance(row, dict)
    assert row.get("matched") is True


@pytest.mark.asyncio
async def test_text_rules_batch_into_two_ocr_calls(monkeypatch: Any) -> None:
    """All ``action: text`` rules in a tick share one primary ``ocr_regions``
    call; rules that miss against ``expected`` AND have a ``_search`` sibling
    add to a single fallback batch — never per-rule HTTP traffic.
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)

    rules = [
        {
            "name": "chapter.task.present",
            "region": "chapter.task",
            "action": "text",
        },
        {
            "name": "tap.anywhere.exit",
            "region": "tapanywhereyoexit",
            "action": "text",
            "expected": ["tap anywhere"],
            "threshold": 0.85,
        },
    ]

    calls: list[dict[str, Any]] = []

    class _RecordingOcr:
        async def ocr_region(self, *_a: Any, **_k: Any) -> OCRResult:
            raise AssertionError("text rules must go through ocr_regions, not ocr_region")

        async def ocr_regions(
            self,
            _image: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            calls.append({"phase": "primary" if not calls else "fallback", "ids": list(ids)})
            results: list[OCRResult] = []
            for rid in ids:
                # Primary text doesn't contain "tap anywhere" → fallback batch
                # gets the wider ``tapanywhereyoexit_search`` bbox.
                if rid.startswith("search::"):
                    results.append(
                        OCRResult(region_id=rid, text="Tap anywhere to continue", confidence=0.97)
                    )
                else:
                    results.append(
                        OCRResult(region_id=rid, text="Bunk Beds in Shelter 2", confidence=0.9)
                    )
            return results

    _patch_overlay_ocr_getter(monkeypatch, _RecordingOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, rule_eval_state=None
    )

    assert len(calls) == 2, f"expected 2 ocr_regions calls (primary + fallback), got {len(calls)}"
    # Primary batch covered both text rules in one HTTP round-trip.
    assert sorted(calls[0]["ids"]) == ["text::0", "text::1"]
    # Fallback batch only re-OCR'd the rule whose primary failed expected match.
    assert calls[1]["ids"] == ["search::1"]

    chapter = out.get("chapter.task.present")
    assert isinstance(chapter, dict) and chapter.get("matched") is True
    assert chapter.get("text") == "Bunk Beds in Shelter 2"

    tap = out.get("tap.anywhere.exit")
    assert isinstance(tap, dict) and tap.get("matched") is True
    # Fallback win bubbles the wider-bbox text + the ``_search`` ocr_source.
    assert tap.get("text") == "Tap anywhere to continue"
    assert tap.get("ocr_source") == "tapanywhereyoexit_search"


@pytest.mark.asyncio
async def test_rule_preprocess_flag_flows_to_ocr_regions(monkeypatch: Any) -> None:
    """``preprocess: enhance`` on a rule reaches ``ocr_regions`` as a
    per-slot tag; a sibling rule without the flag stays raw.

    Locks in the per-rule gating: ``ocr.preprocess.enhance_for_ocr`` is
    opt-in, never global, otherwise high-contrast UI text gets degraded.
    Fallback ``_search`` inherits the parent rule's tag.
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)

    rules = [
        {
            "name": "chapter.task.present",
            "region": "chapter.task",
            "action": "text",
            "preprocess": "enhance",
        },
        {
            "name": "tap.anywhere.exit",
            "region": "tapanywhereyoexit",
            "action": "text",
            "expected": ["tap anywhere"],
            "threshold": 0.85,
            "preprocess": "enhance",
        },
    ]

    seen_preprocess: list[list[str | None] | None] = []

    class _RecordingOcr:
        async def ocr_region(self, *_a: Any, **_k: Any) -> OCRResult:
            raise AssertionError("text rules must go through ocr_regions, not ocr_region")

        async def ocr_regions(
            self,
            _image: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            seen_preprocess.append(region_preprocess)
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            return [
                # Primary text doesn't match — drives the fallback batch so
                # we can also assert ``preprocess`` propagates onto ``_search``.
                OCRResult(region_id=rid, text="other text", confidence=0.9)
                if not rid.startswith("search::")
                else OCRResult(
                    region_id=rid, text="Tap anywhere to continue", confidence=0.97
                )
                for rid in ids
            ]

    _patch_overlay_ocr_getter(monkeypatch, _RecordingOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, rule_eval_state=None
    )
    # Primary batch tagged enhance for both rules.
    assert seen_preprocess[0] == ["enhance", "enhance"]
    # Fallback batch inherits the parent's enhance.
    assert seen_preprocess[1] == ["enhance"]
    assert out["tap.anywhere.exit"]["matched"] is True


@pytest.mark.asyncio
async def test_type_time_auto_enables_fast_line_preprocess(monkeypatch: Any) -> None:
    """A ``type: time`` rule with no explicit ``preprocess`` auto-derives
    ``fast_line`` so Tesseract uses single-line segmentation on a tiny
    countdown crop. Locks in the cheap-path default — flipping it off would
    silently revert overlay timers to block-style segmentation.
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)

    rules = [
        {
            "name": "chapter.task.timer",
            "region": "chapter.task",
            "action": "text",
            "type": "time",
        },
    ]

    seen_preprocess: list[list[str | None] | None] = []

    class _RecordingOcr:
        async def ocr_region(self, *_a: Any, **_k: Any) -> OCRResult:
            raise AssertionError("must go through ocr_regions")

        async def ocr_regions(
            self,
            _image: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            seen_preprocess.append(region_preprocess)
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            return [OCRResult(region_id=rid, text="01:30:00", confidence=0.95) for rid in ids]

    _patch_overlay_ocr_getter(monkeypatch, _RecordingOcr())

    await overlay_engine.evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, rule_eval_state=None
    )
    assert seen_preprocess == [["fast_line"]]


@pytest.mark.asyncio
async def test_explicit_preprocess_overrides_type_derived_default(monkeypatch: Any) -> None:
    """``preprocess: enhance`` on a ``type: time`` rule opts out of fast_line
    — the explicit value wins over the type-derived default. Escape hatch for
    timer regions where ``det=False`` misreads the line layout.
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)

    rules = [
        {
            "name": "chapter.task.timer",
            "region": "chapter.task",
            "action": "text",
            "type": "time",
            "preprocess": "enhance",
        },
    ]

    seen_preprocess: list[list[str | None] | None] = []

    class _RecordingOcr:
        async def ocr_region(self, *_a: Any, **_k: Any) -> OCRResult:
            raise AssertionError("must go through ocr_regions")

        async def ocr_regions(
            self,
            _image: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            seen_preprocess.append(region_preprocess)
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            return [OCRResult(region_id=rid, text="01:30:00", confidence=0.95) for rid in ids]

    _patch_overlay_ocr_getter(monkeypatch, _RecordingOcr())

    await overlay_engine.evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, rule_eval_state=None
    )
    assert seen_preprocess == [["enhance"]]


@pytest.mark.asyncio
async def test_no_preprocess_keyword_when_flag_absent(monkeypatch: Any) -> None:
    """Default rules don't tag ``region_preprocess`` at all — the client
    passes ``None`` so backend payloads stay byte-identical to pre-preprocess
    requests (omitted key, not a null value).
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)

    rules = [
        {
            "name": "chapter.task.present",
            "region": "chapter.task",
            "action": "text",
        },
    ]

    seen_preprocess: list[list[str | None] | None] = []

    class _RecordingOcr:
        async def ocr_region(self, *_a: Any, **_k: Any) -> OCRResult:
            raise AssertionError("must go through ocr_regions")

        async def ocr_regions(
            self,
            _image: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            seen_preprocess.append(region_preprocess)
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            return [OCRResult(region_id=rid, text="x", confidence=0.9) for rid in ids]

    _patch_overlay_ocr_getter(monkeypatch, _RecordingOcr())

    await overlay_engine.evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, rule_eval_state=None
    )
    assert seen_preprocess == [None]


@pytest.mark.asyncio
async def test_text_rules_skip_fallback_batch_when_all_primaries_match(
    monkeypatch: Any,
) -> None:
    """No ``_search`` batch when every primary already satisfies ``expected``.

    Confirms the fallback batch is opt-in by miss — rules without ``expected``
    or rules that matched in Phase 1 don't pay any HTTP cost for the second
    round-trip.
    """
    repo_root = Path(__file__).resolve().parents[2]
    area_doc: dict[str, Any] = json.loads(
        (repo_root / "area.json").read_text(encoding="utf-8")
    )
    image_bgr = np.zeros((720, 1280, 3), dtype=np.uint8)

    rules = [
        {
            "name": "chapter.task.present",
            "region": "chapter.task",
            "action": "text",
        },
        {
            "name": "tap.anywhere.exit",
            "region": "tapanywhereyoexit",
            "action": "text",
            "expected": ["tap anywhere"],
            "threshold": 0.85,
        },
    ]

    calls: list[list[str]] = []

    class _RecordingOcr:
        async def ocr_region(self, *_a: Any, **_k: Any) -> OCRResult:
            raise AssertionError("text rules must go through ocr_regions, not ocr_region")

        async def ocr_regions(
            self,
            _image: Any,
            regions: list[Region],
            *,
            region_ids: list[str] | None = None,
            region_preprocess: list[str | None] | None = None,
        ) -> list[OCRResult]:
            ids = region_ids or [f"r{i}" for i in range(len(regions))]
            calls.append(list(ids))
            return [
                OCRResult(region_id=rid, text="Tap anywhere to continue", confidence=0.95)
                for rid in ids
            ]

    _patch_overlay_ocr_getter(monkeypatch, _RecordingOcr())

    out = await overlay_engine.evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, rule_eval_state=None
    )

    assert len(calls) == 1
    assert sorted(calls[0]) == ["text::0", "text::1"]
    assert out["chapter.task.present"]["matched"] is True
    assert out["tap.anywhere.exit"]["matched"] is True
    assert out["tap.anywhere.exit"]["ocr_source"] == "tapanywhereyoexit"
