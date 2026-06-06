from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException

from api.routers import map_stitch
from config.devices_db import upsert_device
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def test_capture_resolves_instance_id_to_adb_serial(
    sqlite_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    call: dict[str, object] = {}

    def fake_start_capture_job(**kwargs: object) -> str:
        call.update(kwargs)
        return "job-1"

    monkeypatch.setattr(map_stitch, "start_capture_job", fake_start_capture_job)

    out = map_stitch.post_capture(map_stitch.CaptureBody(instance_id="bs1"))

    assert out == {"job_id": "job-1"}
    assert call["instance_id"] == "bs1"
    assert call["serial"] == "127.0.0.1:5555"


def test_capture_rejects_unknown_instance(sqlite_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        map_stitch.post_capture(map_stitch.CaptureBody(instance_id="missing"))

    assert exc.value.status_code == 422
    assert "unknown instance" in str(exc.value.detail)


def test_capture_rejects_empty_instance_id() -> None:
    with pytest.raises(HTTPException) as exc:
        map_stitch.post_capture(map_stitch.CaptureBody(instance_id=" "))

    assert exc.value.status_code == 422
    assert "missing instance_id" in str(exc.value.detail)
