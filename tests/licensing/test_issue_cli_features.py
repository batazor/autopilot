"""Tier→feature resolution on the CLI issue path (``uv run issue-license``).

Without ``--features`` the claims must come from the plan catalog, so a paid
tier actually unlocks its gated features (radar stayed locked otherwise).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from licensing import cli
from licensing.plans import FEATURE_ALLIANCE_STATS, FEATURE_GIFT_EXTERNAL, FEATURE_RADAR

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _issue_payload(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict[str, object]:
    assert cli.main([*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_tier_r4_without_features_resolves_plan_features(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _issue_payload(
        capsys, ["--email", "alice@example.com", "--machine-id", "MID", "--tier", "r4"]
    )
    assert payload["tier"] == "r4"
    assert payload["features"] == [
        FEATURE_GIFT_EXTERNAL,
        FEATURE_RADAR,
        FEATURE_ALLIANCE_STATS,
    ]


def test_explicit_features_override_tier_default(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _issue_payload(
        capsys,
        [
            "--email", "alice@example.com",
            "--machine-id", "MID",
            "--tier", "r4",
            "--features", "heroes,mail",
        ],
    )
    assert payload["features"] == ["heroes", "mail"]
