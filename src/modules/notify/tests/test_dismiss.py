"""poll_once dismisses (snoozes) handled notifications on-device.

Recognized notifications get snoozed by key once handled; unrecognized ones and
the disabled-setting case do not. The on-device call is faked so these run with
no ADB / Redis.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.notify import adb_reader, config, service
from modules.notify.service import MonitorService

# One recognized gof.global record carrying a key, plus an unrecognized one.
_DUMP = (
    "  NotificationRecord(0x1: pkg=com.gof.global user=UserHandle{0} id=5 tag=null "
    "key=0|com.gof.global|5|null|10080: Notification(channel=Default))\n"
    "    extras={\n"
    "      android.title=New Intel (String)\n"
    "      android.text=New Intel in the Lighthouse (String)\n"
    "    }\n"
    "  NotificationRecord(0x2: pkg=com.gof.global user=UserHandle{0} id=6 tag=null "
    "key=0|com.gof.global|6|null|10080: Notification(channel=Default))\n"
    "    extras={\n"
    "      android.title=Random chatter (String)\n"
    "      android.text=Nothing we have a pattern for (String)\n"
    "    }\n"
)


def _settings(dismiss_handled: str = "1") -> dict[str, str]:
    return {
        "adb_path": "adb",
        "adb_serial": "",
        "dismiss_handled": dismiss_handled,
        "dismiss_snooze_ms": "604800000",
    }


def _patch_common(monkeypatch, settings):
    monkeypatch.setattr(service.db, "get_setting", lambda k, d=None: settings.get(k, d))
    monkeypatch.setattr(service.db, "list_players", list)
    monkeypatch.setattr(service.adb_reader, "dump_notifications", lambda **_: _DUMP)
    snoozed: list[dict] = []
    monkeypatch.setattr(
        service.adb_reader,
        "snooze_notification",
        lambda key, **kw: snoozed.append({"key": key, **kw}) or True,
    )
    return snoozed


def _svc() -> MonitorService:
    return MonitorService(publisher=SimpleNamespace())


def test_recognized_notification_is_snoozed(monkeypatch):
    snoozed = _patch_common(monkeypatch, _settings(dismiss_handled="1"))
    svc = _svc()
    # title "New Intel" → recognized; the chatter one → unrecognized.
    monkeypatch.setattr(
        svc,
        "_handle",
        lambda n, _players, _serial="": (1, 0, 0) if "Intel" in n.title else (0, 1, 0),
    )

    summary = svc.poll_once()

    assert summary["recognized"] == 1
    assert summary["unrecognized"] == 1
    # Only the recognized one (id=5) is snoozed; the unrecognized stays visible.
    assert [s["key"] for s in snoozed] == ["0|com.gof.global|5|null|10080"]
    assert snoozed[0]["duration_ms"] == 604800000


def test_disabled_setting_skips_dismissal(monkeypatch):
    snoozed = _patch_common(monkeypatch, _settings(dismiss_handled="0"))
    svc = _svc()
    monkeypatch.setattr(svc, "_handle", lambda *_a, **_k: (1, 0, 0))

    svc.poll_once()
    assert snoozed == [], "dismiss_handled=0 must not snooze anything"


def test_unrecognized_only_is_not_snoozed(monkeypatch):
    snoozed = _patch_common(monkeypatch, _settings(dismiss_handled="1"))
    svc = _svc()
    monkeypatch.setattr(svc, "_handle", lambda *_a, **_k: (0, 1, 0))

    svc.poll_once()
    assert snoozed == [], "unrecognized notifications stay in the shade"


def test_dismiss_snooze_ms_falls_back_on_bad_value(monkeypatch):
    settings = _settings()
    settings["dismiss_snooze_ms"] = "not-a-number"
    _patch_common(monkeypatch, settings)
    svc = _svc()
    assert svc._dismiss_snooze_ms() == config.DEFAULT_DISMISS_SNOOZE_MS


def test_adb_reader_module_exposes_snooze():
    # Guard against an accidental rename of the seam the service depends on.
    assert hasattr(adb_reader, "snooze_notification")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
