from __future__ import annotations

import json
from typing import TYPE_CHECKING

from api.services import click_approval_store as store

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeRedis:
    def __init__(
        self,
        kv: dict[str, str] | None = None,
        *,
        on_set: Callable[[str, str], None] | None = None,
    ) -> None:
        self.kv = dict(kv or {})
        self.on_set = on_set
        self.deleted: list[str] = []
        self.published: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        del ex
        self.kv[key] = value
        if self.on_set is not None:
            self.on_set(key, value)
        return True

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return int(self.kv.pop(key, None) is not None)

    def publish(self, key: str, value: str) -> int:
        self.published.append((key, value))
        return 0


def _pending(request_id: str, *, response_key: str | None = None) -> str:
    return json.dumps(
        {
            "request_id": request_id,
            "response_key": response_key
            or f"wos:ui:click_approval:response:{request_id}",
            "status": "waiting",
        }
    )


def test_submit_decision_records_response_and_clears_current() -> None:
    current_key = "wos:ui:click_approval:current:bs1"
    req_id = "adb:bs1:req"
    client = _FakeRedis({current_key: _pending(req_id)})

    assert store.submit_decision(
        client,
        "bs1",
        "approve",
        request_id=req_id,
    )

    assert client.kv[f"wos:ui:click_approval:response:{req_id}"] == "approve"
    assert current_key not in client.kv
    assert (f"wos:ui:click_approval:decision:{req_id}", "approve") in client.published


def test_submit_decision_rejects_stale_request_id_without_touching_current() -> None:
    current_key = "wos:ui:click_approval:current:bs1"
    client = _FakeRedis({current_key: _pending("adb:bs1:new")})

    assert not store.submit_decision(
        client,
        "bs1",
        "approve",
        request_id="adb:bs1:old",
    )

    assert json.loads(client.kv[current_key])["request_id"] == "adb:bs1:new"
    assert "wos:ui:click_approval:response:adb:bs1:old" not in client.kv


def test_submit_decision_is_idempotent_when_same_response_already_recorded() -> None:
    req_id = "adb:bs1:req"
    client = _FakeRedis({f"wos:ui:click_approval:response:{req_id}": "skip"})

    assert store.submit_decision(client, "bs1", "skip", request_id=req_id)


def test_submit_decision_does_not_delete_new_current_if_slot_changes_mid_submit() -> None:
    current_key = "wos:ui:click_approval:current:bs1"
    old_req_id = "adb:bs1:old"
    new_req_id = "adb:bs1:new"
    client: _FakeRedis

    def replace_current_after_response(key: str, value: str) -> None:
        del value
        if key == f"wos:ui:click_approval:response:{old_req_id}":
            client.kv[current_key] = _pending(new_req_id)

    client = _FakeRedis(
        {current_key: _pending(old_req_id)},
        on_set=replace_current_after_response,
    )

    assert store.submit_decision(
        client,
        "bs1",
        "reject",
        request_id=old_req_id,
    )

    assert json.loads(client.kv[current_key])["request_id"] == new_req_id
    assert current_key not in client.deleted
