"""The fleet orchestrator is dormant while fleet.yaml ships disabled.

Mirrors the disabled-path guarantee the stamina/resource/march planners have:
``_run_fleet_coordinator`` must bail before any IO when no campaign is enabled.
"""
from __future__ import annotations

from types import SimpleNamespace

from scheduler.runner import SchedulerRunner


async def test_fleet_coordinator_noop_when_all_disabled(monkeypatch, settings):
    # fleet.yaml ships every campaign disabled → build_inputs must never run.
    from games.wos.core.fleet import adapter as fleet

    async def _must_not_run(*_a, **_k):
        msg = "build_inputs must not run when all campaigns are disabled"
        raise AssertionError(msg)

    monkeypatch.setattr(fleet, "build_inputs", _must_not_run)

    fake = SimpleNamespace(_redis=object(), _queue=object(), _settings=settings)
    bound = SchedulerRunner._run_fleet_coordinator.__get__(fake, SchedulerRunner)
    # Returns cleanly (no IO, no exception) — the dormancy guarantee.
    await bound(0.0)


async def test_fleet_coordinator_runs_when_enabled(monkeypatch, settings, tmp_path):
    """When a campaign is enabled, the orchestrator proceeds to build inputs."""
    from games.wos.core.fleet import adapter as fleet

    cfg = tmp_path / "fleet.yaml"
    cfg.write_text("enabled: true\ncampaigns: {joint_event: true}\n", encoding="utf-8")
    orig_load = fleet.load_campaigns
    monkeypatch.setattr(fleet, "load_campaigns", lambda: orig_load(cfg))

    reached = {"build": False}

    async def _stub_build(_redis, _settings, _now):
        reached["build"] = True
        # return empty inputs so the rest of the tick is a no-op
        return [], fleet._PlannerFleet(set(), {}), fleet._CalendarView(())

    monkeypatch.setattr(fleet, "build_inputs", _stub_build)

    fake = SimpleNamespace(_redis=object(), _queue=object(), _settings=settings)
    bound = SchedulerRunner._run_fleet_coordinator.__get__(fake, SchedulerRunner)
    await bound(0.0)
    assert reached["build"] is True
