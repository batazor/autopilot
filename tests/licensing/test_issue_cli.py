"""Tier-based issuance on the CLI path (``uv run issue-license``).

Issuance carries the subscription ``tier`` and resolves the per-game external
account cap from the plan catalog so a paid tier unlocks its gated capabilities.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from licensing import cli

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _issue_payload(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict[str, object]:
    assert cli.main([*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_tier_r4_resolves_external_account_cap(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _issue_payload(
        capsys, ["--email", "alice@example.com", "--machine-id", "MID", "--tier", "r4"]
    )
    assert payload["tier"] == "r4"
    # The cap is resolved from the plan catalog, not passed explicitly.
    assert payload["max_external_accounts"] == 50
    # Tokens no longer carry a feature claim — capabilities are gated by tier.
    assert "features" not in payload


def test_tier_r3_resolves_external_account_cap(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _issue_payload(
        capsys, ["--email", "bob@example.com", "--machine-id", "MID", "--tier", "r3"]
    )
    assert payload["tier"] == "r3"
    assert payload["max_external_accounts"] == 5


def test_trial_flag_defaults_to_r2_wildcard(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _issue_payload(
        capsys,
        ["--email", "trial@example.com", "--trial", "--days", "60"],
    )
    assert payload["tier"] == "r2"
    assert payload["machine_id"] == "*"
    assert payload["max_external_accounts"] == 0


def test_legacy_tier_names_are_rejected(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    for tier in ("trial", "pro", "free"):
        assert cli.main(
            [
                "--email", "legacy@example.com",
                "--trial",
                "--tier", tier,
                "--json",
            ]
        ) == 1
        captured = capsys.readouterr()
        assert "tier must be one of: r2, r3, r4, r5" in captured.err


def test_explicit_external_account_cap_overrides_tier_default(
    keypair_paths: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _issue_payload(
        capsys,
        [
            "--email", "carol@example.com",
            "--machine-id", "MID",
            "--tier", "r4",
            "--max-external-accounts", "7",
        ],
    )
    assert payload["tier"] == "r4"
    assert payload["max_external_accounts"] == 7
