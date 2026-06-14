"""Farm module wiring + the R5 owner-only discovery gate."""
from __future__ import annotations

from pathlib import Path

import yaml

from config import module_discovery
from licensing import plans

MODULE_DIR = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    return yaml.safe_load((MODULE_DIR / "module.yaml").read_text(encoding="utf-8"))


def test_r5_is_top_of_the_tier_ladder() -> None:
    assert plans.TIER_ORDER[-1] == "r5"
    assert plans.tier_at_least("r5", "r4") is True  # cumulative: r5 unlocks r4
    assert plans.tier_at_least("r4", "r5") is False
    assert plans.plan_by_id("r5") is not None


def test_farm_manifest_is_owner_gated_skeleton() -> None:
    m = _manifest()
    assert m["id"] == "farm"
    assert m["min_tier"] == "r5"
    # Skeleton: inert until the first flow + labeled regions land.
    assert m["enabled"] is False


def test_min_tier_gate_hides_module_below_tier(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "module.yaml"
    manifest.write_text("id: farm\nmin_tier: r5\n", encoding="utf-8")

    def _tier(value):
        monkeypatch.setattr("licensing.gate.current_tier", lambda: value)

    _tier("r4")
    assert module_discovery._module_manifest_tier_ok(manifest) is False
    _tier("r5")
    assert module_discovery._module_manifest_tier_ok(manifest) is True
    # No min_tier → always visible regardless of license.
    plain = tmp_path / "plain.yaml"
    plain.write_text("id: other\n", encoding="utf-8")
    _tier(None)
    assert module_discovery._module_manifest_tier_ok(plain) is True


def test_min_tier_gate_hides_on_licensing_error(monkeypatch, tmp_path: Path) -> None:
    # If the tier can't be resolved, a gated module must stay hidden (closed),
    # never leak to a tier we can't confirm.
    manifest = tmp_path / "module.yaml"
    manifest.write_text("id: farm\nmin_tier: r5\n", encoding="utf-8")

    def _boom():
        msg = "no license"
        raise RuntimeError(msg)

    monkeypatch.setattr("licensing.gate.current_tier", _boom)
    assert module_discovery._module_manifest_tier_ok(manifest) is False
