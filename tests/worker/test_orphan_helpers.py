from __future__ import annotations

from typing import TYPE_CHECKING

from worker import orphan_helpers

if TYPE_CHECKING:
    from pathlib import Path


class _FakeProc:
    def __init__(
        self,
        *,
        pid: int,
        ppid: int,
        cmdline: list[str],
        exe: str = "",
    ) -> None:
        self.pid = pid
        self._ppid = ppid
        self._cmdline = cmdline
        self._exe = exe
        self.terminated = False
        self.killed = False

    def ppid(self) -> int:
        return self._ppid

    def cmdline(self) -> list[str]:
        return self._cmdline

    def exe(self) -> str:
        return self._exe

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def test_cleanup_orphaned_sck_capture_helpers_only_targets_repo_orphans(
    tmp_path: Path,
    monkeypatch,
) -> None:
    helper = tmp_path / ".cache" / "sck" / "sck_capture_helper"
    helper.parent.mkdir(parents=True)
    helper.write_text("", encoding="utf-8")

    target = _FakeProc(pid=101, ppid=1, cmdline=[str(helper)])
    managed = _FakeProc(pid=102, ppid=999, cmdline=[str(helper)])
    other = _FakeProc(pid=103, ppid=1, cmdline=["/tmp/other/sck_capture_helper"])
    exe_target = _FakeProc(pid=104, ppid=1, cmdline=["sck_capture_helper"], exe=str(helper))
    procs = [target, managed, other, exe_target]

    monkeypatch.setattr(orphan_helpers.psutil, "process_iter", lambda _attrs: procs)
    monkeypatch.setattr(orphan_helpers.psutil, "wait_procs", lambda p, **_kwargs: (list(p), []))
    monkeypatch.setattr(orphan_helpers.time, "sleep", lambda _seconds: None)

    assert orphan_helpers.cleanup_orphaned_sck_capture_helpers(root=tmp_path) == [101, 104]
    assert target.terminated is True
    assert exe_target.terminated is True
    assert managed.terminated is False
    assert other.terminated is False


def test_cleanup_orphaned_sck_capture_helpers_returns_empty_without_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    called = False

    def _process_iter(_attrs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(orphan_helpers.psutil, "process_iter", _process_iter)

    assert orphan_helpers.cleanup_orphaned_sck_capture_helpers(root=tmp_path) == []
    assert called is False
