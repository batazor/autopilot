from __future__ import annotations

import pytest

from analysis.overlay import evaluate_overlay_rules


@pytest.mark.asyncio
async def test_sync_overlay_wrapper_rejects_running_event_loop() -> None:
    with pytest.raises(RuntimeError, match="use await evaluate_overlay_rules_async"):
        evaluate_overlay_rules(
            image_bgr=None,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            area_doc={},
            repo_root=None,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
            overlay_rules=[],
        )
