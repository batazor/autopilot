"""Tests for the fish-video validator: swipe heuristic, sampling, upload guards."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.services import fish_video
from api.services.fish_common import FishDetectionRow
from api.services.fish_video import _compass, predict_swipes
from inference.roboflow_client import Detection


def _row(cx: int, cy: int) -> FishDetectionRow:
    return FishDetectionRow(
        x=cx - 5, y=cy - 5, width=10, height=10,
        center_x=cx, center_y=cy, confidence=0.9, class_name="fish",
    )


# --- compass -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("dx", "dy", "expected"),
    [
        (10, 0, "E"),
        (-10, 0, "W"),
        (0, -10, "N"),  # screen y grows downward → negative dy is up = North
        (0, 10, "S"),
        (10, -10, "NE"),
        (-10, 10, "SW"),
    ],
)
def test_compass(dx: int, dy: int, expected: str) -> None:
    assert _compass(dx, dy) == expected


# --- swipe heuristic ---------------------------------------------------------
def test_predict_swipe_escape_and_catch_opposite() -> None:
    prev = [_row(100, 100)]
    cur = [_row(140, 100)]  # moved +40px right in one 500ms step
    out = predict_swipes(prev, cur, interval_ms=500)
    assert len(out) == 1
    s = out[0]
    assert s["escape_compass"] == "E"
    assert s["catch_compass"] == "W"  # catch is opposite the escape
    assert s["speed_px_s"] == pytest.approx(80.0)  # 40px / 0.5s
    # catch arrow points opposite the escape arrow
    assert s["catch_to_x"] < s["center_x"] < s["escape_to_x"]


def test_predict_swipe_skips_resting_fish() -> None:
    prev = [_row(100, 100)]
    cur = [_row(102, 101)]  # < motion epsilon
    assert predict_swipes(prev, cur, interval_ms=500) == []


def test_predict_swipe_skips_far_jumps() -> None:
    prev = [_row(50, 50)]
    cur = [_row(700, 700)]  # beyond max-match distance → not the same fish
    assert predict_swipes(prev, cur, interval_ms=500) == []


def test_predict_swipe_no_prev_frame() -> None:
    assert predict_swipes([], [_row(100, 100)], interval_ms=500) == []


# --- sampling (end to end, detector stubbed) ---------------------------------
class _FakeDetector:
    """Stand-in that yields canned detections per frame, no network."""

    model_id = "fake-fish/1"

    def __init__(self, per_frame: list[list[Detection]]) -> None:
        self._seq = per_frame
        self._i = 0

    @classmethod
    def from_settings(cls, _cfg: object) -> _FakeDetector:  # pragma: no cover - set per test
        raise NotImplementedError

    def available(self) -> bool:
        return True

    async def detect(self, _frame: np.ndarray, *, threshold: float | None = None) -> list[Detection]:
        out = self._seq[self._i] if self._i < len(self._seq) else []
        self._i += 1
        return out


def _write_clip(path: str, *, n_frames: int, fps: int = 10, size: tuple[int, int] = (160, 120)) -> None:
    import cv2

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, size)
    for i in range(n_frames):
        frame = np.full((size[1], size[0], 3), i * 10 % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_process_video_samples_at_interval(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    clip = tmp_path / "clip.mp4"
    _write_clip(str(clip), n_frames=10, fps=10)

    # fps=10, interval=500ms → step=5 → sampled frames at pos 0 and 5 = 2 samples.
    fake = _FakeDetector([
        [Detection(x=40, y=40, width=10, height=10, confidence=0.9, class_name="fish")],
        [Detection(x=70, y=40, width=10, height=10, confidence=0.9, class_name="fish")],
    ])
    monkeypatch.setattr(fish_video, "RoboflowDetector", _FakeDetector)
    monkeypatch.setattr(_FakeDetector, "from_settings", classmethod(lambda _cls, _cfg: fake))

    job_id = "testjob01"
    fish_video._JOBS[job_id] = fish_video.FishVideoJob(
        job_id=job_id, state="queued", processed=0, total=0, fps_in=0.0,
        duration_ms=0, frame_width=0, frame_height=0, interval_ms=500,
        threshold=0.4, model_id="fake-fish/1", available=True, error="", frames=[],
    )
    fish_video.job_dir(job_id).mkdir(parents=True, exist_ok=True)
    try:
        fish_video._process_video(job_id, clip)
        job = fish_video._JOBS[job_id]
        assert job["state"] == "done", job["error"]
        assert job["processed"] == 2
        assert len(job["frames"]) == 2
        # Fish moved right between the two samples → escape East, catch West.
        swipes = job["frames"][1]["swipes"]
        assert swipes and swipes[0]["catch_compass"] == "W"
        # annotated PNGs were written
        assert fish_video.frame_image_path(job_id, 0).is_file()
        assert fish_video.frame_image_path(job_id, 1).is_file()
    finally:
        fish_video.delete_job(job_id)


# --- upload guards (TestClient) ----------------------------------------------
@pytest.fixture
def client() -> TestClient:
    from api.main import app

    return TestClient(app)


def test_upload_rejects_non_video(client: TestClient) -> None:
    r = client.post(
        "/api/fish-detect/video",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 415


def test_upload_rejects_empty(client: TestClient) -> None:
    r = client.post(
        "/api/fish-detect/video",
        files={"file": ("clip.mp4", b"", "video/mp4")},
    )
    assert r.status_code == 422


def test_upload_rejects_oversize(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The router imports the constant by value, so patch its binding there.
    monkeypatch.setattr("api.routers.fish_video.MAX_UPLOAD_BYTES", 16)
    r = client.post(
        "/api/fish-detect/video",
        files={"file": ("clip.mp4", b"x" * 64, "video/mp4")},
    )
    assert r.status_code == 413


def test_job_status_404(client: TestClient) -> None:
    assert client.get("/api/fish-detect/video/does-not-exist").status_code == 404
