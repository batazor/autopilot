from __future__ import annotations

import re

import pytest

from licensing import fingerprint as fp_mod
from licensing.fingerprint import fingerprint_components, generate_fingerprint

_FP_RE = re.compile(r"^[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}$")


@pytest.fixture(autouse=True)
def _license_file_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("WOS_LICENSE_FILE", str(tmp_path / "licence.jwt"))


def test_fingerprint_format() -> None:
    fp = generate_fingerprint()
    assert _FP_RE.match(fp), f"unexpected format: {fp!r}"


def test_fingerprint_is_stable() -> None:
    assert generate_fingerprint() == generate_fingerprint()


def test_components_returns_expected_keys() -> None:
    parts = fingerprint_components()
    assert set(parts.keys()) == {"machine_id", "shared_host_id", "mac", "hostname"}


def test_machine_id_anchors_fingerprint_across_containers(monkeypatch) -> None:
    """Same machine-id ⇒ same fingerprint, even when hostname/mac diverge.

    Mirrors a host-network worker vs a bridge API on one host: both bind-mount
    the same ``/etc/machine-id`` but see different hostname/mac. They must agree.
    """
    worker = {
        "machine_id": "shared-host-id",
        "shared_host_id": "",
        "mac": "aabbccddeeff",
        "hostname": "docker-desktop",
    }
    api = {
        "machine_id": "shared-host-id",
        "shared_host_id": "",
        "mac": "",
        "hostname": "4c09c7cb4099",
    }

    monkeypatch.setattr(fp_mod, "_components", lambda: worker)
    worker_fp = generate_fingerprint()
    monkeypatch.setattr(fp_mod, "_components", lambda: api)
    api_fp = generate_fingerprint()

    assert worker_fp == api_fp


def test_shared_host_id_anchors_fingerprint_without_machine_id(monkeypatch) -> None:
    worker = {
        "machine_id": "",
        "shared_host_id": "shared-volume-install-id",
        "mac": "aabbccddeeff",
        "hostname": "docker-desktop",
    }
    api = {
        "machine_id": "",
        "shared_host_id": "shared-volume-install-id",
        "mac": "",
        "hostname": "4c09c7cb4099",
    }

    monkeypatch.setattr(fp_mod, "_components", lambda: worker)
    worker_fp = generate_fingerprint()
    monkeypatch.setattr(fp_mod, "_components", lambda: api)
    api_fp = generate_fingerprint()

    assert worker_fp == api_fp


def test_falls_back_to_hostname_mac_without_machine_id(monkeypatch) -> None:
    """No machine-id ⇒ hostname/mac still provide a distinguishing fingerprint."""
    a = {"machine_id": "", "shared_host_id": "", "mac": "aabbccddeeff", "hostname": "host-a"}
    b = {"machine_id": "", "shared_host_id": "", "mac": "aabbccddeeff", "hostname": "host-b"}

    monkeypatch.setattr(fp_mod, "_components", lambda: a)
    fp_a = generate_fingerprint()
    monkeypatch.setattr(fp_mod, "_components", lambda: b)
    fp_b = generate_fingerprint()

    assert fp_a != fp_b


def test_fingerprint_survives_unreadable_host_components(monkeypatch) -> None:
    """Locked-down containers should not turn the license page into HTTP 500."""

    def boom() -> str:
        msg = "host component unavailable"
        raise OSError(msg)

    monkeypatch.setattr(fp_mod, "_read_machine_id", boom)
    monkeypatch.setattr(fp_mod, "_read_shared_host_id", boom)
    monkeypatch.setattr(fp_mod, "_read_mac", boom)
    monkeypatch.setattr(fp_mod, "_read_hostname", boom)

    assert _FP_RE.match(generate_fingerprint())
    assert fingerprint_components() == {"machine_id": "", "shared_host_id": "", "mac": "", "hostname": ""}
