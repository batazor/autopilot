"""Radar API: runs listing, tile serving, immediate scan start + 409 guard."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from api.deps import get_redis
from api.routers import radar
from modules.radar.events import ACTIVE_KEY, STREAM, RadarEventPublisher


class FakeRedis:
    """Just enough of redis.Redis for the radar router."""

    def __init__(self) -> None:
        self.kv = {}
        self.zsets = {}
        self.published = []
        self.streams = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def xadd(self, stream, fields, maxlen=None, approximate=True):
        rows = self.streams.setdefault(stream, [])
        entry_id = f"{len(rows) + 1}-0"
        rows.append((entry_id, fields))
        return entry_id

    def xread(self, streams, count=None, block=None):
        out = []
        for stream, last_id in streams.items():
            rows = self.streams.get(stream, [])
            selected = rows if last_id == "0-0" else []
            if count is not None:
                selected = selected[:count]
            if selected:
                out.append((stream, selected))
        return out


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_redis):
    monkeypatch.setenv("RADAR_RUNS_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(radar.router)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    return TestClient(app)


def _make_run(tmp_path, run_id="2026-06-10_120000", with_map=False):
    run = tmp_path / run_id
    run.mkdir()
    manifest = {
        "config": {"overlap": 0.25},
        "grid": {"count": 2, "points": [{"ix": 0, "iy": 0}, {"ix": 1, "iy": 0}]},
        "frames": {
            "00_00": {"ix": 0, "iy": 0, "ts": 100.0, "unstable": False, "file": "frame_00_00.png"},
            "01_00": {"ix": 1, "iy": 0, "ts": 112.5, "unstable": True, "file": "frame_01_00.png"},
        },
    }
    (run / "manifest.json").write_text(json.dumps(manifest))
    if with_map:
        Image.new("RGB", (300, 200), (10, 20, 30)).save(run / "map_full.png")
    return run


class TestRuns:
    def test_empty_when_no_runs_dir_content(self, client):
        assert client.get("/api/radar/runs").json() == []

    def test_summary_fields(self, client, tmp_path):
        _make_run(tmp_path)
        (rows,) = client.get("/api/radar/runs").json()
        assert rows["run_id"] == "2026-06-10_120000"
        assert rows["frames_done"] == 2
        assert rows["frames_total"] == 2
        assert rows["unstable_count"] == 1
        assert rows["duration_s"] == 12.5
        assert rows["has_tiles"] is False

    def test_manifest_roundtrip_and_bad_ids(self, client, tmp_path):
        _make_run(tmp_path)
        assert client.get("/api/radar/runs/2026-06-10_120000/manifest").status_code == 200
        assert client.get("/api/radar/runs/no-such-run/manifest").status_code == 404
        # Traversal-shaped ids must never reach the filesystem.
        assert client.get("/api/radar/runs/%2e%2e/manifest").status_code in (400, 404)

    def test_delete_run_removes_directory(self, client, tmp_path):
        run = _make_run(tmp_path)
        res = client.delete("/api/radar/runs/2026-06-10_120000")
        assert res.status_code == 200
        assert res.json() == {"run_id": "2026-06-10_120000", "status": "deleted"}
        assert not run.exists()
        assert client.get("/api/radar/runs").json() == []

    def test_delete_active_run_rejected(self, client, tmp_path, fake_redis):
        _make_run(tmp_path)
        fake_redis.set(
            ACTIVE_KEY,
            json.dumps({"run_id": "2026-06-10_120000", "status": "scanning", "done": 0, "total": 2}),
        )
        res = client.delete("/api/radar/runs/2026-06-10_120000")
        assert res.status_code == 409
        assert (tmp_path / "2026-06-10_120000").exists()

    def test_active_scan_snapshot(self, client, fake_redis):
        active = {"run_id": "run-1", "status": "scanning", "done": 17, "total": 29}
        fake_redis.set(ACTIVE_KEY, json.dumps(active))

        assert client.get("/api/radar/active").json() == {"active": active}


class TestTiles:
    def test_tile_serving_and_cache_headers(self, client, tmp_path, fake_redis):
        run = _make_run(tmp_path, with_map=True)
        from modules.radar.tiles import generate_tiles

        generate_tiles(run)
        ok = client.get("/api/radar/runs/2026-06-10_120000/tiles/0/0/0")
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == "public, max-age=31536000, immutable"
        missing = client.get("/api/radar/runs/2026-06-10_120000/tiles/0/9/9")
        assert missing.status_code == 404
        assert missing.headers["cache-control"] == "no-store"

    def test_tiles_meta_404_without_tiles(self, client, tmp_path):
        _make_run(tmp_path)
        assert client.get("/api/radar/runs/2026-06-10_120000/tiles.json").status_code == 404


@pytest.fixture
def stub_instances(monkeypatch):
    class _Inst:
        instance_id = "bs1"
        bluestacks_window_title = "127.0.0.1:5555"

    class _Settings:
        instances = (_Inst(),)

    monkeypatch.setattr(radar, "load_settings", lambda: _Settings())


@pytest.fixture
def immediate_scan_calls(monkeypatch):
    calls = []

    def fake_run_scan(run_id, instance_id, target, client):
        calls.append((run_id, instance_id, target, client))

    monkeypatch.setattr(radar, "_run_scan_now_blocking", fake_run_scan)
    return calls


@pytest.fixture
def radar_config(tmp_path, monkeypatch):
    path = tmp_path / "radar_config.yaml"
    path.write_text(
        """
version: 1
device_serial: 127.0.0.1:5555
adb_bin: adb
minimap:
  bbox: [0, 0, 220, 220]
  corners:
    top: [100.0, 0.0]
    right: [200.0, 100.0]
    bottom: [100.0, 200.0]
    left: [0.0, 100.0]
viewport:
  rect_w: 100
  rect_h: 100
overlap: 0.0
crop:
  x: 0
  y: 0
  w: 100
  h: 100
game_size: 1200
timings:
  post_tap_delay_ms: 300
  stabilize_interval_ms: 150
  stabilize_diff_threshold: 2.0
  stabilize_consecutive: 2
  stabilize_timeout_ms: 5000
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(radar, "_radar_config_path", lambda *_: path)
    return path


@pytest.mark.usefixtures("stub_instances")
class TestScan:
    def test_missing_config_rejected_before_background_scan(
        self, client, tmp_path, monkeypatch, immediate_scan_calls
    ):
        monkeypatch.setattr(radar, "_radar_config_path", lambda *_: tmp_path / "missing.yaml")
        res = client.post("/api/radar/scan", json={})
        assert res.status_code == 409
        assert "radar config not found" in res.json()["detail"]
        assert immediate_scan_calls == []

    def test_start_sets_guard_and_runs_scan_immediately(
        self, client, fake_redis, immediate_scan_calls, radar_config
    ):
        res = client.post("/api/radar/scan", json={})
        assert res.status_code == 200
        run_id = res.json()["run_id"]
        instance_id = res.json()["instance_id"]
        active = json.loads(fake_redis.kv[ACTIVE_KEY])
        assert active["run_id"] == run_id
        assert active["status"] == "scanning"
        assert active["total"] == 1
        assert active["target"] == "global_map"
        assert active["grid"] == [{"ix": 0, "iy": 0}]
        assert res.json()["total_frames"] == 1
        assert res.json()["target"] == "global_map"
        assert res.json()["grid"] == [{"ix": 0, "iy": 0}]
        assert immediate_scan_calls == [(run_id, instance_id, "global_map", fake_redis)]

    def test_second_scan_rejected_with_409(
        self, client, fake_redis, immediate_scan_calls, radar_config
    ):
        assert client.post("/api/radar/scan", json={}).status_code == 200
        res = client.post("/api/radar/scan", json={})
        assert res.status_code == 409
        assert "already" in res.json()["detail"]
        assert len(immediate_scan_calls) == 1

    def test_unknown_instance_404(self, client):
        res = client.post("/api/radar/scan", json={"instance_id": "not-a-device"})
        assert res.status_code == 404

    def test_scan_target_threads_through(
        self, client, fake_redis, immediate_scan_calls, radar_config
    ):
        res = client.post("/api/radar/scan", json={"target": "island"})
        assert res.status_code == 200
        run_id = res.json()["run_id"]
        instance_id = res.json()["instance_id"]
        assert res.json()["target"] == "island"
        assert json.loads(fake_redis.kv[ACTIVE_KEY])["target"] == "island"
        assert immediate_scan_calls == [(run_id, instance_id, "island", fake_redis)]

    def test_unknown_target_400(self, client, radar_config):
        res = client.post("/api/radar/scan", json={"target": "atlantis"})
        assert res.status_code == 400
        assert "unknown radar target" in res.json()["detail"]


class TestEvents:
    def test_frame_done_is_available_as_sse_stream_event(self, fake_redis):
        RadarEventPublisher(fake_redis, "run-1").frame_done(
            2,
            3,
            unstable=False,
            done=7,
            total=10,
        )

        last_id, lines = radar._read_stream(fake_redis, "0-0")

        assert last_id == "1-0"
        assert len(lines) == 1
        assert lines[0].startswith("id: 1-0\n")
        payload = json.loads(lines[0].split("data: ", 1)[1])
        assert payload == {
            "type": "frame_done",
            "run_id": "run-1",
            "target": "global_map",
            "ix": 2,
            "iy": 3,
            "unstable": False,
            "done": 7,
            "total": 10,
        }
        assert STREAM in fake_redis.streams
