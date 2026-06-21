"""Run overlay analyzers on the current rolling frame and build a UI-ready report.

Operator tool ("what does the bot currently see?"): mirrors the worker's overlay
pass against the latest rolling preview PNG, returns per-rule matched/score data
plus pre-rendered ``OverlayShape`` rectangles for the Next.js canvas.

Import the public names from this package, not the submodules.
"""
from api.services.overlay_test.breakdown import (
    _collect_push_candidates,
    _module_has_overlay_rules,
    _run_module_analyzer_breakdown,
    _run_module_analyzer_breakdown_async,
)
from api.services.overlay_test.cache import (
    _detect_screen_from_preview_png,
    _load_overlay_test_preview,
    _overlay_test_frame_fingerprint,
    _overlay_test_recall_hint,
    _overlay_test_remember_hint,
    _overlay_test_result_cache,
    _overlay_test_result_cache_get,
    _overlay_test_result_cache_lock,
    _overlay_test_result_cache_put,
    _screen_detect_hint,
)
from api.services.overlay_test.common import (
    _area_region_names,
    _bbox_pct_to_px,
    _coerce_float,
    _decode_png_to_bgr,
    _detect_screen_on_frame,
    _evaluate_rules_ignoring_screen_gate,
    _ordered_unique,
    _rule_metadata,
)
from api.services.overlay_test.ocr import run_region_ocr, run_region_ocr_test
from api.services.overlay_test.probe import run_area_region_probe
from api.services.overlay_test.run import (
    _overlay_test_cond_context,
    load_overlay_test_image,
    run_overlay_test,
    run_screen_detect,
)
from api.services.overlay_test.types import (
    AreaRegionProbeResult,
    ModuleAnalyzerRun,
    OverlayAnalysisSummary,
    OverlayRuleRow,
    OverlayTestResult,
    ProbeCrops,
    ProbeCropSide,
    PushScenarioCandidate,
    RegionOcrResult,
    RegionOcrRow,
    RegionOcrTestResult,
    ScreenDetectResult,
)

__all__ = [
    "AreaRegionProbeResult",
    "ModuleAnalyzerRun",
    "OverlayAnalysisSummary",
    "OverlayRuleRow",
    "OverlayTestResult",
    "ProbeCropSide",
    "ProbeCrops",
    "PushScenarioCandidate",
    "RegionOcrResult",
    "RegionOcrRow",
    "RegionOcrTestResult",
    "ScreenDetectResult",
    "_area_region_names",
    "_bbox_pct_to_px",
    "_coerce_float",
    "_collect_push_candidates",
    "_decode_png_to_bgr",
    "_detect_screen_from_preview_png",
    "_detect_screen_on_frame",
    "_evaluate_rules_ignoring_screen_gate",
    "_load_overlay_test_preview",
    "_module_has_overlay_rules",
    "_ordered_unique",
    "_overlay_test_cond_context",
    "_overlay_test_frame_fingerprint",
    "_overlay_test_recall_hint",
    "_overlay_test_remember_hint",
    "_overlay_test_result_cache",
    "_overlay_test_result_cache_get",
    "_overlay_test_result_cache_lock",
    "_overlay_test_result_cache_put",
    "_rule_metadata",
    "_run_module_analyzer_breakdown",
    "_run_module_analyzer_breakdown_async",
    "_screen_detect_hint",
    "load_overlay_test_image",
    "run_area_region_probe",
    "run_overlay_test",
    "run_region_ocr",
    "run_region_ocr_test",
    "run_screen_detect",
]
