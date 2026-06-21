"""Screenshot capture, rolling/approval previews, and approval-slot bookkeeping
for :class:`adb.controller.AdbController`."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from adb.approvals import (
    APPROVAL_CURRENT_TTL_SECONDS,
    _redis,
    click_approval_enabled,
)
from adb.screencap import adb_screencap_png
from config.paths import repo_root
from config.reference_naming import (
    rolling_preview_basename,
    temporal_png_abs_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

if TYPE_CHECKING:
    from adb._controller_host import _ControllerHost as _Base
else:
    _Base = object

logger = logging.getLogger(__name__)


class AdbPreviewMixin(_Base):
    """Frame capture plus the preview/approval plumbing built on top of it."""

    # ------------------------------------------------------------------
    # Screenshot via ADB
    # ------------------------------------------------------------------

    def screenshot_bytes(self) -> bytes:

        data, err = adb_screencap_png(self._adb_exe, self._serial)
        if data is None:
            raise RuntimeError(err)
        return data

    def _approval_payload_with_preview(self, payload: dict[str, object]) -> dict[str, object]:
        p = dict(payload)
        if click_approval_enabled(self._instance_id):
            self._attach_approval_preview(p)
            # Let ``_require_approval`` re-capture the preview right before
            # serialising for publish — the gap between this initial attach
            # and the actual SET can stretch into seconds (or longer) when
            # the approval slot is contended. Popped + invoked there.
            p["_preview_capturer"] = self._attach_approval_preview
        return p

    def attach_approval_preview(self, payload: dict[str, object]) -> None:
        if click_approval_enabled(self._instance_id):
            self._attach_approval_preview(payload)
            payload["_preview_capturer"] = self._attach_approval_preview

    def _write_png_bytes_atomic(self, *, path: Path, png: bytes, tmp_prefix: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=tmp_prefix, suffix=".png", dir=path.parent
        )
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(png)
            tmp.replace(path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _capture_to_rolling_preview(self, *, tmp_prefix: str) -> tuple[Path, Path] | None:
        """Capture a fresh frame and atomically overwrite ``current_state.png``.

        Returns ``(absolute_path, repo_root)`` on success, ``None`` on failure.
        Used by both pre-approval (`_attach_approval_preview`) and post-action
        (`_refresh_rolling_preview`) writers.
        """
        try:
            png = self.screenshot_bytes()
            root = repo_root()
            path = temporal_png_abs_path(
                root,
                rolling_preview_basename(self._instance_id),
            )
            self._write_png_bytes_atomic(path=path, png=png, tmp_prefix=tmp_prefix)
            return path, root
        except Exception:
            return None

    def _attach_approval_preview(self, payload: dict[str, object]) -> None:
        """Capture pre-approval frame, attach a stable request snapshot to payload."""
        try:
            png = self.screenshot_bytes()
            root = repo_root()
            rolling_path = temporal_png_abs_path(
                root,
                rolling_preview_basename(self._instance_id),
            )
            approval_path = temporal_png_abs_path(
                root,
                f"{self._instance_id}_approval_current",
            )
            self._write_png_bytes_atomic(
                path=rolling_path,
                png=png,
                tmp_prefix=".approval-live-",
            )
            self._write_png_bytes_atomic(
                path=approval_path,
                png=png,
                tmp_prefix=".approval-snapshot-",
            )
        except Exception:
            logger.debug(
                "Failed to capture approval preview for %s", self._instance_id, exc_info=True
            )
            return
        rel = approval_path.relative_to(root)
        payload["preview_png_rel"] = rel.as_posix()
        payload["preview_captured_at"] = time.time()

    def _refresh_rolling_preview(self) -> None:
        """Capture post-action frame so the rolling preview reflects the new state.

        Only runs when approval mode is enabled — outside approval mode the
        rolling timer loop in instance_worker is the only writer.
        """
        if not click_approval_enabled(self._instance_id):
            return
        if self._capture_to_rolling_preview(tmp_prefix=".post-action-") is None:
            logger.debug(
                "Failed to refresh rolling preview for %s", self._instance_id
            )

    @contextmanager
    def _approval_execution(self, req_id: str | None) -> Iterator[None]:
        """Mark the approval slot as ``executing`` for the duration of the action.

        Wraps the actual ADB ``input`` shell call so the approvals UI can
        distinguish "waiting for action to complete" from "still waiting for
        operator decision".  Cleans up the slot and per-request response key
        after the action returns (or raises).  No-op when approval is disabled
        (``req_id is None``).
        """
        if req_id is None:
            yield
            return
        current_key = f"wos:ui:click_approval:current:{self._instance_id}"
        try:
            raw = _redis().get(current_key)
            if raw:
                doc = json.loads(raw)  # ty: ignore[invalid-argument-type]
                doc["executed_at"] = time.time()
                doc["status"] = "executing"
                _redis().set(
                    current_key,
                    json.dumps(doc),
                    ex=APPROVAL_CURRENT_TTL_SECONDS,
                )
        except Exception:
            logger.debug("Failed to mark executed_at", exc_info=True)
        try:
            yield
        finally:
            try:
                _redis().delete(current_key)
                _redis().delete(f"wos:ui:click_approval:response:{req_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys", exc_info=True)
