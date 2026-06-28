"""Per-account scan-root scoping: a city map belongs to ONE account.

Before this, ``runs_root()`` was global, so a ``main_city`` scan taken on one
account was reused for another — ``navigate_to_building`` then routed against the
wrong city (or returned ``not_in_map``). ``account_runs_root`` scopes the runs
tree by the active account; these tests pin the scoping + isolation contract.
"""

import json

from modules.radar.config import ACCOUNTS_DIRNAME, account_runs_root, runs_root
from modules.radar.navigator import latest_city_run


def _mk_city_run(d, buildings, target="main_city"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "map_full.png").write_bytes(b"x")
    (d / "buildings.json").write_text(json.dumps(buildings))
    (d / "manifest.json").write_text(json.dumps({"config": {"target": target}}))


def test_account_runs_root_scopes_by_account(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_RUNS_DIR", str(tmp_path))
    assert runs_root() == tmp_path
    # A real account → its own subtree under accounts/.
    assert account_runs_root("401227964") == tmp_path / ACCOUNTS_DIRNAME / "401227964"
    assert account_runs_root("3295843") == tmp_path / ACCOUNTS_DIRNAME / "3295843"
    # Blank / None / whitespace → the global root (untargeted callers unchanged).
    assert account_runs_root("") == tmp_path
    assert account_runs_root(None) == tmp_path
    assert account_runs_root("   ") == tmp_path


def test_latest_city_run_isolates_accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_RUNS_DIR", str(tmp_path))
    _mk_city_run(account_runs_root("A") / "scan1", {"furnace": 1})
    _mk_city_run(account_runs_root("B") / "scan1", {"hero_hall": 1})

    run_a = latest_city_run(account_runs_root("A"))
    run_b = latest_city_run(account_runs_root("B"))
    assert run_a is not None and run_b is not None
    # Each account resolves to ITS OWN scan, never the other's.
    assert json.loads((run_a / "buildings.json").read_text()) == {"furnace": 1}
    assert json.loads((run_b / "buildings.json").read_text()) == {"hero_hall": 1}
    assert run_a != run_b


def test_unscanned_account_gets_no_map_not_a_neighbours(monkeypatch, tmp_path):
    # The whole point: an account WITHOUT a scan must return None, NOT silently
    # fall back to another account's (or the global) city — that was the bs3 bug.
    monkeypatch.setenv("RADAR_RUNS_DIR", str(tmp_path))
    _mk_city_run(account_runs_root("A") / "scan1", {"furnace": 1})
    _mk_city_run(tmp_path / "global_scan", {"sawmill": 1})  # a global-root scan

    assert latest_city_run(account_runs_root("C")) is None
    # And the global root only sees the global scan, not account A's.
    glob = latest_city_run(account_runs_root(""))
    assert glob is not None
    assert json.loads((glob / "buildings.json").read_text()) == {"sawmill": 1}
