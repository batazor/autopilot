"""Type-check-only host Protocol for the ``AdbController`` mixin family.

The controller is composed of four mixin classes
(``AdbDisplayMixin``, ``AdbProcessMixin``, ``AdbInputMixin``,
``AdbPreviewMixin``) and a host ``AdbController`` class that supplies the
shared attributes (``_instance_id``, ``_serial``, ``_adb_exe`` …) and the
shell plumbing (``_shell`` / ``_shell_full``).

Each mixin freely calls into siblings and reads host attrs (e.g.
``self._serial``), but from a single-mixin perspective those names are
unresolved — only the host class brings everything together. This module
declares one ``Protocol`` listing every attribute and method that crosses a
mixin boundary. Each mixin inherits from this Protocol **only under**
``TYPE_CHECKING`` (runtime base stays ``object``, so the MRO of the composed
``AdbController`` class is unchanged):

    if TYPE_CHECKING:
        from adb._controller_host import _ControllerHost as _Base
    else:
        _Base = object

    class AdbProcessMixin(_Base):
        ...

See ``tasks/_dsl_task_host.py`` for the original rationale behind the
Protocol-as-conditional-base pattern.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from adb.controller_types import _ShellOutcome
    from adb.scrcpy import ScrcpyClient


class _ControllerHost(Protocol):
    # ------------------------------------------------------------------
    # Host-class attributes (initialized in ``AdbController.__init__``)
    # ------------------------------------------------------------------
    _instance_id: str
    _adb_exe: str
    _serial: str
    _supports_motionevent: bool | None
    _screen_resolution: tuple[int, int] | None
    _input_backend: str
    _scrcpy_client_getter: Callable[[], ScrcpyClient | None] | None

    # ------------------------------------------------------------------
    # Shell plumbing (concrete implementation on ``AdbController``)
    # ------------------------------------------------------------------
    def _shell(self, *args: str, timeout: float = 15.0) -> str: ...
    def _shell_full(self, *args: str, timeout: float = 15.0) -> _ShellOutcome: ...

    # ------------------------------------------------------------------
    # Cross-mixin method stubs. Signatures are deliberately loose where a
    # precise one buys nothing — the goal is only to give ``ty`` knowledge
    # that the names *exist*; each mixin keeps the concrete signature.
    # ------------------------------------------------------------------

    # AdbDisplayMixin
    def get_screen_resolution(self) -> tuple[int, int]: ...

    # AdbPreviewMixin
    def screenshot_bytes(self) -> bytes: ...
    def _refresh_rolling_preview(self) -> None: ...
    def _approval_payload_with_preview(
        self, payload: dict[str, object]
    ) -> dict[str, object]: ...
    def _attach_approval_preview(self, payload: dict[str, object]) -> None: ...
    def _approval_execution(self, req_id: str | None) -> Any: ...
