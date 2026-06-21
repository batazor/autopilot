"""STUB: learned close-button detector for the ad / webview tail.

The blurred-scrim heuristic in :mod:`popup.mask` covers native modals. It does
*not* cover full-bleed ads and embedded webviews, which have no blurred scrim
and place their close affordance arbitrarily.

The intended implementation trains two generic classes — ``close_button`` and
``modal_card`` — via autodistill → RF-DETR/YOLO on clustered pop-up
screenshots, run on the bbox crop only. This file provides the call surface so
:mod:`popup.detector` can invoke it today and a real model can be swapped in
later without touching callers.

Until weights ship, :meth:`available` returns ``False`` and :meth:`find` raises
``NotImplementedError`` — callers must gate on ``available()`` first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from layout.types import Point


class CloseButtonModel:
    """Pluggable close-button locator for the ad/webview fallback path."""

    def available(self) -> bool:
        """True once trained weights are present. Always ``False`` for the stub."""
        return False

    async def find(self, image: np.ndarray) -> Point | None:
        """Locate the close button in ``image`` (a bbox crop) — not yet trained.

        Raises:
            NotImplementedError: the model weights are not bundled yet. Gate
                calls on :meth:`available` first.
        """
        msg = "CloseButtonModel weights are not bundled; gate calls on available()"
        raise NotImplementedError(msg)
