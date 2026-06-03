"""Redis event publisher.

Publishes recognized events to per-player channels:
    wos:events:{nickname}      kingshot:events:{nickname}
The game id is used verbatim as the channel prefix.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from . import config
from .logging_setup import get_logger

log = get_logger("publisher")


def _queue_key(instance_id: str) -> str:
    """Same convention as ``scheduler.queue._queue_key`` / dispatcher."""
    iid = (instance_id or "").strip()
    return f"wos:queue:{iid}" if iid else "wos:queue:unknown"


class RedisPublisher:
    """Thin wrapper around redis-py with lazy connect + status tracking."""

    def __init__(self, url: str = config.REDIS_URL) -> None:
        self._url = url
        self._client = None
        self._lock = threading.Lock()
        self.connected = False
        self.last_error: str | None = None
        self.published_count = 0
        self.last_publish_ts: str | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                import redis  # imported lazily so the UI loads even w/o redis

                self._client = redis.from_url(
                    self._url, decode_responses=True, socket_connect_timeout=3,
                    socket_timeout=3,
                )
        return self._client

    def ping(self) -> bool:
        try:
            self._ensure_client().ping()
            self.connected = True
            self.last_error = None
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            log.warning("Redis ping failed: %s", exc)
        return self.connected

    def channel(self, game: str, nickname: str) -> str:
        return f"{game}:events:{nickname}"

    def publish_event(self, game: str, player: str, event_type: str, raw_text: str,
                      timestamp: str | None = None) -> bool:
        payload: dict[str, Any] = {
            "game": game,
            "player": player,
            "event_type": event_type,
            "raw_text": raw_text,
            "timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        channel = self.channel(game, player)
        try:
            self._ensure_client().publish(channel, json.dumps(payload))
            self.connected = True
            self.last_error = None
            self.published_count += 1
            self.last_publish_ts = payload["timestamp"]
            log.info("Published %s for %s -> %s", event_type, player, channel)
            return True
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            log.exception("Redis publish failed (%s)", channel)
            return False

    def enqueue_scenario(
        self,
        instance_id: str,
        player_id: str,
        scenario_key: str,
        *,
        priority: int = config.PUSH_SCENARIO_PRIORITY,
    ) -> bool:
        """Push a DSL scenario directly onto the worker queue.

        Writes a ``task_type=dsl_scenario`` envelope to ``wos:queue:<instance>``
        via ``ZADD`` with ``run_at=now`` as the score — the exact primitive the
        scheduler/optimizer use (see ``optimizer.dispatcher.enqueue_envelope``)
        so the worker picks it up through its normal ``pop_due`` path. Returns
        True on a successful write.
        """
        now = time.time()
        body: dict[str, Any] = {
            "task_id": f"notify:{uuid.uuid4().hex[:12]}",
            "player_id": str(player_id),
            "task_type": "dsl_scenario",
            "priority": int(priority),
            "run_at": now,
            "instance_id": str(instance_id),
            "created_at": now,
            "dsl_scenario": scenario_key,
        }
        qk = _queue_key(instance_id)
        try:
            self._ensure_client().zadd(qk, {json.dumps(body): now})
            self.connected = True
            self.last_error = None
            log.info(
                "Enqueued scenario %s for player=%s -> %s",
                scenario_key, player_id, qk,
            )
            return True
        except Exception as exc:
            self.connected = False
            self.last_error = str(exc)
            log.exception("Redis enqueue_scenario failed (%s)", qk)
            return False

    def status(self) -> dict[str, Any]:
        return {
            "url": self._url,
            "connected": self.connected,
            "last_error": self.last_error,
            "published_count": self.published_count,
            "last_publish_ts": self.last_publish_ts,
        }
