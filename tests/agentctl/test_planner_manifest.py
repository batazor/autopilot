"""The planner registry (``games/wos/planners.yaml``) must stay consistent.

Guards against a stale manifest: every referenced config path resolves, every
``enabled`` flag is readable, trace keys template ``{fid}``, and names are unique.
This is the contract ``botctl planners`` relies on.
"""

from __future__ import annotations

from agentctl import core
from config.paths import repo_root


def test_manifest_nonempty_and_unique_names() -> None:
    entries = core._load_planner_manifest()
    assert entries, "planner manifest is empty"
    names = [str(e.get("name", "")) for e in entries]
    assert all(names), "every planner needs a name"
    assert len(names) == len(set(names)), f"duplicate planner names: {names}"


def test_manifest_paths_and_keys_resolve() -> None:
    root = repo_root()
    for e in core._load_planner_manifest():
        name = e["name"]
        assert str(e.get("wired", "")).strip(), f"{name}: missing wired"
        assert isinstance(e.get("observed_inputs") or [], list), f"{name}: observed_inputs not a list"

        cfg = str(e.get("config", "")).strip()
        if cfg:
            assert (root / cfg).is_file(), f"{name}: config path missing → {cfg}"

        tk = str(e.get("trace_key", "")).strip()
        if tk:
            assert "{fid}" in tk, f"{name}: trace_key must template {{fid}} → {tk}"


def test_manifest_enabled_flags_are_readable() -> None:
    # Every config we point at carries an explicit enabled flag (bool, not None).
    for e in core._load_planner_manifest():
        cfg = str(e.get("config", "")).strip()
        if not cfg:
            continue
        val = core._yaml_enabled(cfg, str(e.get("enabled_key", "enabled")))
        assert isinstance(val, bool), f"{e['name']}: enabled flag unreadable in {cfg}"
