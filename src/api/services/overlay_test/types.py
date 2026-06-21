"""TypedDict payloads for the overlay-test API endpoints."""
from __future__ import annotations

from typing import Any, TypedDict

# Runtime import (not TYPE_CHECKING): FastAPI/Pydantic resolves these TypedDict
# annotations when building the route response models, so ``OverlayShape`` must
# be importable from this module's namespace at runtime.
from api.services.click_approval_overlay import OverlayShape  # noqa: TC001


class OverlayRuleRow(TypedDict):
    """One row in the overlay-test result table."""

    name: str
    node: str
    region: str
    action: str
    search_region: str
    matched: bool
    score: float | None
    threshold: float | None
    reason: str
    notes: str


class ModuleAnalyzerRun(TypedDict):
    """Per-module overlay analyzer timing (sequential probe)."""

    module_id: str
    label: str
    duration_ms: int
    rule_count: int
    matched_count: int


class PushScenarioCandidate(TypedDict):
    """Scenario the worker would enqueue from a matched overlay rule (dry-run)."""

    scenario: str
    rule: str
    region: str
    priority: int
    selected: bool
    skip_reason: str


class OverlayAnalysisSummary(TypedDict):
    """Aggregate analyzer run stats for the overlay-test UI."""

    module_runs: list[ModuleAnalyzerRun]
    modules_total_ms: int
    full_run_ms: int
    screen_detect_ms: int
    screen_source: str
    push_candidates: list[PushScenarioCandidate]
    has_active_player: bool
    simulated_no_player: bool
    device_level_only: bool


class OverlayTestResult(TypedDict):
    """Response payload for ``GET /api/instances/{id}/overlay-test``."""

    instance_id: str
    current_screen: str
    detected_screen: str
    active_player: str
    preview: dict[str, Any]
    rules: list[OverlayRuleRow]
    overlays: list[OverlayShape]
    total_rules: int
    matched_count: int
    analysis: OverlayAnalysisSummary


class ProbeCropSide(TypedDict, total=False):
    available: bool
    width: int
    height: int
    label: str
    data_url: str


class ProbeCrops(TypedDict, total=False):
    region: str
    resolved_region: str
    reference_rel: str
    live: ProbeCropSide
    template: ProbeCropSide


class AreaRegionProbeResult(TypedDict):
    """Response payload for a single ``area.json`` region probe."""

    instance_id: str
    current_screen: str
    active_player: str
    selected_region: str
    regions: list[str]
    preview: dict[str, Any]
    result: dict[str, Any] | None
    overlays: list[OverlayShape]
    crops: ProbeCrops | None


class RegionOcrRow(TypedDict):
    """One region's live OCR read for the region-ocr endpoint."""

    region: str
    text: str
    confidence: float | None
    threshold: float | None
    low_confidence: bool
    # ok | empty | error | no_region | no_frame
    status: str
    # Wall-clock OCR time for this region (ms); None when not OCR'd.
    duration_ms: float | None


class RegionOcrResult(TypedDict):
    """Response payload for live OCR of one or more area regions."""

    instance_id: str
    current_screen: str
    preview: dict[str, Any]
    rows: list[RegionOcrRow]


class ScreenDetectResult(TypedDict):
    """Response payload for lightweight live screen detection."""

    instance_id: str
    detected_screen: str
    screen_source: str
    preview: dict[str, Any]
    duration_ms: int


class RegionOcrTestResult(TypedDict):
    """Response payload for OCR + screen detection on an uploaded test image."""

    instance_id: str
    detected_screen: str
    screen_source: str
    preview: dict[str, Any]
    rows: list[RegionOcrRow]
