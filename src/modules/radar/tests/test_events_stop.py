"""Cooperative stop signalling over Redis (request → poll → clear)."""

from modules.radar.events import (
    RadarEventPublisher,
    clear_stop,
    read_active,
    request_stop,
    stop_requested,
)


class FakeRedis:
    """Just enough of the Redis surface the radar events touch."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.streamed: list[dict] = []
        self.published: list[str] = []

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key: str) -> None:
        self.store.pop(key, None)

    def xadd(self, *_args, **_kwargs) -> None:
        self.streamed.append(_kwargs)

    def publish(self, _channel: str, payload: str) -> None:
        self.published.append(payload)


def test_request_and_poll_stop_round_trip() -> None:
    client = FakeRedis()
    run = "2026-06-12_010203"

    assert stop_requested(client, run) is False
    request_stop(client, run)
    assert stop_requested(client, run) is True
    # A stop for one run does not trip a different run.
    assert stop_requested(client, "other") is False
    clear_stop(client)
    assert stop_requested(client, run) is False


def test_publisher_stop_requested_tracks_its_run() -> None:
    client = FakeRedis()
    pub = RadarEventPublisher(client, "run-A")

    assert pub.stop_requested() is False
    request_stop(client, "run-A")
    assert pub.stop_requested() is True


def test_scan_started_drops_a_stale_stop_flag() -> None:
    client = FakeRedis()
    request_stop(client, "old-run")  # left over from a previous run
    pub = RadarEventPublisher(client, "new-run")

    pub.scan_started(total=3, grid=[(0, 0), (1, 0)])

    assert stop_requested(client, "old-run") is False
    assert read_active(client) is not None  # the new run is now active


def test_scan_finished_clears_active_and_stop_and_flags_stopped() -> None:
    client = FakeRedis()
    pub = RadarEventPublisher(client, "run-A")
    pub.scan_started(total=3, grid=[(0, 0)])
    request_stop(client, "run-A")

    pub.scan_finished(12.3, stopped=True)

    assert read_active(client) is None
    assert stop_requested(client, "run-A") is False
    # The stopped flag rides the published scan_finished event.
    assert any('"stopped": true' in p for p in client.published)
