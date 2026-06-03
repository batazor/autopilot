from __future__ import annotations

import re

from licensing import fingerprint as fp_mod
from licensing.fingerprint import fingerprint_components, generate_fingerprint

_FP_RE = re.compile(r"^[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}$")


def test_fingerprint_format() -> None:
    fp = generate_fingerprint()
    assert _FP_RE.match(fp), f"unexpected format: {fp!r}"


def test_fingerprint_is_stable() -> None:
    assert generate_fingerprint() == generate_fingerprint()


def test_components_returns_expected_keys() -> None:
    parts = fingerprint_components()
    assert set(parts.keys()) == {"machine_id", "mac", "hostname"}


def test_machine_id_anchors_fingerprint_across_containers(monkeypatch) -> None:
    """Same machine-id ⇒ same fingerprint, even when hostname/mac diverge.

    Mirrors a host-network worker vs a bridge API on one host: both bind-mount
    the same ``/etc/machine-id`` but see different hostname/mac. They must agree.
    """
    worker = {"machine_id": "shared-host-id", "mac": "aabbccddeeff", "hostname": "docker-desktop"}
    api = {"machine_id": "shared-host-id", "mac": "", "hostname": "4c09c7cb4099"}

    monkeypatch.setattr(fp_mod, "_components", lambda: worker)
    worker_fp = generate_fingerprint()
    monkeypatch.setattr(fp_mod, "_components", lambda: api)
    api_fp = generate_fingerprint()

    assert worker_fp == api_fp


def test_falls_back_to_hostname_mac_without_machine_id(monkeypatch) -> None:
    """No machine-id ⇒ hostname/mac still provide a distinguishing fingerprint."""
    a = {"machine_id": "", "mac": "aabbccddeeff", "hostname": "host-a"}
    b = {"machine_id": "", "mac": "aabbccddeeff", "hostname": "host-b"}

    monkeypatch.setattr(fp_mod, "_components", lambda: a)
    fp_a = generate_fingerprint()
    monkeypatch.setattr(fp_mod, "_components", lambda: b)
    fp_b = generate_fingerprint()

    assert fp_a != fp_b
