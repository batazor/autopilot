from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import redis.asyncio as aioredis

from config.devices import player_ids_for_device_candidates
from config.loader import Settings
from config.paths import repo_root

logger = logging.getLogger(__name__)

# Dynamic ranking knobs (ADR 0001 §"Ranking model"). Defaults are bounded so a
# single debuff cannot cross a configured 10k YAML priority band.
RECENT_RUNS_WINDOW_SECONDS = 1800
RECENT_RUNS_CAP = 3
W_HOPS = 500
W_RECENT = 1000
HOPS_DEBUFF_CAP_HOPS = 5
UNREACHABLE_DEBUFF = 5000
HOPS_SENTINEL = 10**9


def _queue_key(instance_id: str) -> str:
    iid = str(instance_id or "").strip()
    return f"wos:queue:{iid}" if iid else "wos:queue:unknown"


# Atomic dedup-then-ZADD. Two producers (e.g. rolling overlay tick and the
# after-task overlay tick) used to both pass ``has_pending_duplicate`` and
# both ZADD the same logical scenario, since the Python guard read the ZSET
# in one round-trip and wrote in another. Moving the scan + write into a
# single ``EVAL`` makes Redis the serialization point.
#
# Match semantics (same as :meth:`RedisQueue.has_pending_duplicate`):
# * always filter ``(instance_id, task_type)``;
# * region filters unless ``ignore_region == "1"``;
# * player-bound enqueue (``player_id != ""``) matches in-flight items whose
#   ``data.player_id`` is either ``""`` (device-level) or the same player —
#   so a queued cross-player item for the same task_type doesn't block;
# * device-level enqueue (``player_id == ""``) matches any player — one
#   queued device-level item blocks re-pushing for everyone.
#
# Returns 1 if ZADD was applied, 0 if a duplicate was found and ZADD skipped.
_DEDUP_ZADD_LUA = """
local function s(v)
    if v == nil or v == cjson.null then return "" end
    return tostring(v)
end

local task_type = ARGV[3]
local player_id = ARGV[4]
local instance_id = ARGV[5]
local want_region = ARGV[6]
local ignore_region = ARGV[7] == "1"
local device_level_enqueue = player_id == ""

local items = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", "+inf")
for i = 1, #items do
    local ok, data = pcall(cjson.decode, items[i])
    if ok and type(data) == "table" then
        if s(data.instance_id) == instance_id
           and s(data.task_type) == task_type then
            local data_pid = s(data.player_id)
            if device_level_enqueue or data_pid == "" or data_pid == player_id then
                if ignore_region or s(data.region) == want_region then
                    return 0
                end
            end
        end
    end
end

redis.call("ZADD", KEYS[1], ARGV[2], ARGV[1])
return 1
"""


def _recent_runs_key(instance_id: str) -> str:
    iid = str(instance_id or "").strip() or "unknown"
    return f"wos:instance:{iid}:recent_runs"


@lru_cache(maxsize=1024)
def _bfs_hops(src: str, dst: str) -> int | None:
    """Shortest-path hop count over the screen graph, or None if unreachable.

    Process-cached on (src, dst). Topology is loaded once at import; the cache
    only needs to be cleared if ``screen_graph`` is reloaded — out of scope here.
    """
    from navigation.screen_graph import bfs_route

    if not src or not dst:
        return None
    path = bfs_route(src, dst)
    if path is None:
        return None
    return max(0, len(path) - 1)


@dataclass(frozen=True)
class QueueItem:
    task_id: str
    player_id: str
    task_type: str
    priority: int
    run_at: float
    instance_id: str
    # Region name in area.json; bbox is resolved when the task runs.
    region: str | None = None
    # Optional tap override (% of framebuffer). Used when overlay matched inside ``search_region``.
    tap_x_pct: float | None = None
    tap_y_pct: float | None = None
    # Optional overlay match threshold (when task_type == "overlay_tap")
    threshold: float | None = None
    # Optional overlay match score/confidence (when task_type == "overlay_tap")
    score: float | None = None
    # Optional "assume screen after tap" (overlay_tap only)
    set_node: str | None = None
    # Optional DSL scenario key (imperative_drafts runner).
    dsl_scenario: str | None = None
    # Optional template match box for UI/debug (overlay-derived tasks).
    match_top_left_x: int | None = None
    match_top_left_y: int | None = None
    template_w: int | None = None
    template_h: int | None = None
    tap_match_x_pct: float | None = None
    tap_match_y_pct: float | None = None
    # Step index to resume from when re-enqueuing after a hand-pointer interruption.
    start_step_index: int = 0
    # Stable last tie-breaker for ranking — set at schedule() time. Missing on
    # legacy items defaults to 0.0 (those sort first, harmless for stragglers).
    created_at: float = 0.0
    # Computed at pop / peek time. Carries the rank decision through to the
    # worker / DSL task for preemption checks. ``0`` outside of ranked contexts.
    effective_priority: int = 0


class RedisQueue:
    def __init__(
        self,
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
        settings: Settings,
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        # Registered once per RedisQueue; redis-py caches the SHA and reissues
        # ``SCRIPT LOAD`` on ``NOSCRIPT`` automatically.
        self._dedup_zadd_script = redis_client.register_script(_DEDUP_ZADD_LUA)

    async def schedule(
        self,
        task_id: str,
        player_id: str,
        task_type: str,
        priority: int,
        run_at: float,
        instance_id: str,
        region: str | None = None,
        *,
        tap_x_pct: float | None = None,
        tap_y_pct: float | None = None,
        threshold: float | None = None,
        score: float | None = None,
        set_node: str | None = None,
        dsl_scenario: str | None = None,
        match_top_left_x: int | None = None,
        match_top_left_y: int | None = None,
        template_w: int | None = None,
        template_h: int | None = None,
        tap_match_x_pct: float | None = None,
        tap_match_y_pct: float | None = None,
        start_step_index: int = 0,
        skip_if_duplicate: bool = False,
        dedup_ignore_region: bool = False,
    ) -> bool:
        """Enqueue a task.

        Returns False if ``skip_if_duplicate`` and a matching item is already queued.

        By default, the duplicate signature includes ``region``.
        Set ``dedup_ignore_region=True`` to deduplicate by (instance_id, player_id, task_type)
        while still preserving ``region`` in the payload for UI/debugging.

        The dedup check + ZADD run inside a single Lua script when
        ``skip_if_duplicate=True`` so concurrent producers (e.g. rolling overlay
        tick + after-task overlay tick) cannot both pass the guard and both
        write the same logical task. ``pop_due`` already serializes pops via
        ``ZREM``; this closes the symmetric gap on the enqueue side.
        """
        import json

        body: dict[str, object] = {
            "task_id": task_id,
            "player_id": player_id,
            "task_type": task_type,
            "priority": priority,
            "run_at": run_at,
            "instance_id": instance_id,
        }
        if region is not None and str(region).strip() != "":
            body["region"] = str(region).strip()
        if tap_x_pct is not None:
            body["tap_x_pct"] = float(tap_x_pct)
        if tap_y_pct is not None:
            body["tap_y_pct"] = float(tap_y_pct)
        if threshold is not None:
            body["threshold"] = float(threshold)
        if score is not None:
            fs = float(score)
            body["score"] = fs
            # Alias: some payloads/tools only preserved one key; pop_due prefers this first.
            body["overlay_match_score"] = fs
        if set_node is not None and str(set_node).strip() != "":
            body["set_node"] = str(set_node).strip()
        if dsl_scenario is not None and str(dsl_scenario).strip() != "":
            body["dsl_scenario"] = str(dsl_scenario).strip()
        if match_top_left_x is not None:
            body["match_top_left_x"] = int(match_top_left_x)
        if match_top_left_y is not None:
            body["match_top_left_y"] = int(match_top_left_y)
        if template_w is not None:
            body["template_w"] = int(template_w)
        if template_h is not None:
            body["template_h"] = int(template_h)
        if tap_match_x_pct is not None:
            body["tap_match_x_pct"] = float(tap_match_x_pct)
        if tap_match_y_pct is not None:
            body["tap_match_y_pct"] = float(tap_match_y_pct)
        if start_step_index:
            body["start_step_index"] = int(start_step_index)
        body["created_at"] = time.time()
        payload = json.dumps(body)
        # Score = run_at unix ts (earlier = higher priority in ZADD)
        qk = _queue_key(instance_id)

        if skip_if_duplicate:
            want_region = (
                str(region).strip() if region is not None and str(region).strip() else ""
            )
            rv = await self._dedup_zadd_script(
                keys=[qk],
                args=[
                    payload,
                    run_at,
                    task_type,
                    player_id or "",
                    instance_id or "",
                    want_region,
                    "1" if dedup_ignore_region else "0",
                ],
            )
            try:
                applied = int(rv) == 1
            except (TypeError, ValueError):
                applied = False
            if not applied:
                logger.debug(
                    "Skip duplicate queue item: player=%s type=%s region=%r",
                    player_id,
                    task_type,
                    region,
                )
                return False
        else:
            await self._redis.zadd(qk, {payload: run_at})

        return True

    async def has_pending_duplicate(
        self,
        *,
        player_id: str,
        task_type: str,
        region: str | None,
        instance_id: str | None = None,
        ignore_region: bool = False,
    ) -> bool:
        """True if the queue already has a matching item.

        Matching rules:
        - Always filters by ``(instance_id, task_type)``.
        - Region filters unless ``ignore_region=True``.
        - Player-bound enqueue (``player_id != ""``) matches device-level
          (``data.player_id == ""``) and same-player in-flight items. A
          cross-player in-flight item with the same ``task_type`` does **not**
          block — players on one instance run independent task streams.
        - Device-level enqueue (``player_id == ""``) matches any player's
          in-flight item — one device-level push blocks re-pushing for everyone.

        Read-only counterpart to the atomic ``_DEDUP_ZADD_LUA`` used inside
        :meth:`schedule`; both implement the same predicate. Direct callers
        (``scheduler/runner.py``) use this as a best-effort pre-filter before
        ``schedule(skip_if_duplicate=True)`` makes the final atomic decision.

        Always scans the queue ZSET — the previous index-based fast path stored
        full payloads (with random ``task_id`` and varying ``run_at``), so two
        ``SADD`` calls for the same logical task produced two different members
        instead of one. The index also went silently stale on ``WRONGTYPE`` /
        suppressed errors and let duplicates through. The scan is O(N) over the
        per-instance queue which is small (tens of items) — correctness over
        a microsecond fast path.
        """
        import json

        iid = str(instance_id or "")
        pid = str(player_id or "")
        device_level_enqueue = pid == ""
        want_region = "" if ignore_region else (
            str(region).strip() if region is not None else ""
        )
        all_items = await self._redis.zrangebyscore(_queue_key(iid), "-inf", "+inf")
        for raw in all_items:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if str(data.get("instance_id", "")) != iid:
                continue
            if str(data.get("task_type", "")) != task_type:
                continue
            data_pid = str(data.get("player_id", ""))
            if not device_level_enqueue and data_pid != "" and data_pid != pid:
                continue
            if ignore_region:
                return True
            got = data.get("region")
            got_s = str(got).strip() if got is not None else ""
            if got_s == want_region:
                return True
        return False

    @staticmethod
    def _task_types_requiring_node() -> set[str]:
        """Task types from cron YAML specs that declare ``node`` — used for the
        "don't even pop if ``current_screen`` is empty" gate in :meth:`pop_due`.

        Stays cron-only on purpose: overlay-/DSL-pushed node-bound scenarios
        get a different treatment — the worker pops them, and DSL execute
        early-exits with ``awaiting_screen_identity`` until the rolling screen
        detector repopulates ``current_screen``. Promoting them into
        this set would silently leave them parked in the queue until screen
        identity returned, which is harder to debug. The ranking-side
        :meth:`_task_type_to_required_node` does cover all scenarios — that's
        a separate concern (hops penalty, not a hard gate).
        """
        root = repo_root()
        fp = RedisQueue._cron_specs_fingerprint(root)
        return RedisQueue._task_types_requiring_node_cached(fp)

    @staticmethod
    def _cron_specs_fingerprint(repo_root: Path) -> tuple[str, tuple[tuple[str, int, int], ...]]:
        """Stable fingerprint for all cron YAML specs (cache invalidation)."""
        from scenarios.cron_specs import iter_cron_yaml_files_for_repo

        root = repo_root.resolve()
        items: list[tuple[str, int, int]] = []
        for p in iter_cron_yaml_files_for_repo(root):
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            items.append((rel, int(st.st_mtime_ns), int(st.st_size)))
        items.sort(key=lambda x: x[0])
        return (str(root), tuple(items))

    @staticmethod
    @lru_cache(maxsize=8)
    def _task_types_requiring_node_cached(
        fp: tuple[str, tuple[tuple[str, int, int], ...]]
    ) -> set[str]:
        return set(RedisQueue._task_type_to_required_node_cron_only_cached(fp).keys())

    @staticmethod
    @lru_cache(maxsize=8)
    def _task_type_to_required_node_cron_only_cached(
        fp: tuple[str, tuple[tuple[str, int, int], ...]]
    ) -> dict[str, str]:
        """Cron-only ``task_type → node`` map, used by the gating set.

        Split off from :meth:`_task_type_to_required_node_cached` (which now
        covers every scenario including templates) because gating and ranking
        ask different questions: gating wants the smaller "must have screen
        identity" set (cron only); ranking wants every task_type so it can
        score hops.
        """
        from scenarios.cron_specs import resolve_cron_task_type

        root = Path(fp[0])
        try:
            import yaml
        except Exception:
            return {}

        out: dict[str, str] = {}
        for rel, _, _ in fp[1]:
            yml = root / rel
            try:
                raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            node = str(raw.get("node") or "").strip()
            if not node:
                continue
            t = resolve_cron_task_type(raw, yml)
            if t:
                out[t] = node
        return out

    @staticmethod
    def _task_type_to_required_node() -> dict[str, str]:
        """Map ``task_type → required_node`` for **every** runnable scenario.

        Covers cron, overlay-pushed, and DSL-pushed scenarios — plus template
        files expanded to one entry per concrete key (``{hero}.yaml`` → 62
        entries × rendered node). Ranking uses this map to compute BFS hops
        from ``current_screen`` to each candidate's target node; without the
        template / non-cron entries, ranking silently degenerated to FIFO for
        anything not on a ``cron:`` schedule (so an overlay-pushed
        ``heroes.bahiti.wiki`` 0 hops away lost to a 6-hop ``molly`` queued a
        few seconds earlier).
        """
        root = repo_root()
        from scenarios.registry import scenario_yaml_tree_fingerprint

        fp = scenario_yaml_tree_fingerprint(root)
        return RedisQueue._task_type_to_required_node_cached(fp)

    @staticmethod
    @lru_cache(maxsize=8)
    def _task_type_to_required_node_cached(
        fp: tuple[str, tuple[tuple[str, int, int], ...]]
    ) -> dict[str, str]:
        from scenarios.template_resolver import iter_resolved_keys, load_doc

        repo_root_path = Path(fp[0])
        out: dict[str, str] = {}
        for resolved in iter_resolved_keys(repo_root_path):
            loaded = load_doc(repo_root_path, resolved.key)
            if loaded is None:
                continue
            _path, doc = loaded
            node = str(doc.get("node") or "").strip()
            if not node:
                continue
            # Template-rendered node strings: ``load_doc`` already applied the
            # substitution context, so ``heroes.${hero_id}.wiki`` arrived here
            # as ``heroes.ahmose.wiki`` and so on — no extra rendering needed.
            out[resolved.key] = node
        return out

    @staticmethod
    def _task_types_device_level() -> set[str]:
        """Task types from runnable scenario YAMLs that declare ``device_level: true``.

        These scenarios are explicitly safe to run without ``active_player`` —
        identity probes (``who_i_am``), tutorial dismissals
        (``skip_button``/``hand_pointer*``), and popup taps.  Everything else
        is treated as player-bound and gated until ``active_player`` is set.
        """
        from scenarios.registry import scenario_yaml_tree_fingerprint

        root = repo_root()
        fp = scenario_yaml_tree_fingerprint(root)
        return RedisQueue._task_types_device_level_cached(fp)

    @staticmethod
    @lru_cache(maxsize=8)
    def _task_types_device_level_cached(
        fp: tuple[str, tuple[tuple[str, int, int], ...]]
    ) -> set[str]:
        from scenarios import template_resolver

        root = Path(fp[0])
        out: set[str] = set()
        for resolved in template_resolver.iter_resolved_keys(root):
            loaded = template_resolver.load_doc(root, resolved.key)
            if loaded is None:
                continue
            _path, raw = loaded
            if not isinstance(raw, dict):
                continue
            if raw.get("device_level") is not True:
                continue
            out.add(resolved.key)
        return out

    async def _collect_ranked_due(
        self, instance_id: str, current_screen: str, now: float
    ) -> list[
        tuple[
            tuple[int, int, int, float, float],
            str,
            dict[str, object],
            dict[str, object],
        ]
    ]:
        """Return post-gate, ranked due items. Shared by pop_due / peek_top_due.

        Applies the existing time + instance + player + node + active_player gates
        from ``pop_due``, then runs ``_rank_candidates`` and returns the sorted list
        (smallest tuple first — i.e. highest effective_priority).
        """
        import json

        instance_players = self._players_for_instance(instance_id)
        ap = ""
        try:
            raw_ap = await self._redis.hget(
                f"wos:instance:{instance_id}:state", "active_player"
            )
            ap = (raw_ap.decode() if isinstance(raw_ap, bytes) else str(raw_ap or "")).strip()
            if ap:
                instance_players = instance_players | {ap}
        except Exception:
            pass
        key = _queue_key(instance_id)
        candidates = await self._redis.zrangebyscore(key, "-inf", now)

        due: list[tuple[str, dict[str, object]]] = []
        for raw in candidates:
            data = json.loads(raw)
            pid = str(data.get("player_id", ""))
            if str(data.get("instance_id", "")) == instance_id and (pid == "" or pid in instance_players):
                due.append((raw, data))

        if not due:
            return []

        if str(current_screen or "").strip().lower() == "loading":
            return []

        if not str(current_screen or "").strip():
            gated = self._task_types_requiring_node()
            if gated:
                due = [
                    x for x in due
                    if bool(x[1].get("debug"))
                    or str(x[1].get("task_type") or "") not in gated
                ]
                if not due:
                    return []

        if not ap:
            device_level = self._task_types_device_level()
            due = [
                x for x in due
                if bool(x[1].get("debug"))
                or str(x[1].get("task_type") or "") in device_level
            ]
            if not due:
                return []

        recent_counts = await self._read_recent_counts(instance_id, now)
        ranked = self._rank_candidates(
            due,
            current_screen=str(current_screen or "").strip(),
            recent_counts=recent_counts,
            now=now,
        )
        ranked.sort(key=lambda x: x[0])
        return ranked

    async def pop_due(self, instance_id: str, *, current_screen: str = "") -> QueueItem | None:

        now = time.time()
        key = _queue_key(instance_id)
        ranked = await self._collect_ranked_due(instance_id, current_screen, now)
        # Atomic claim via ZREM: the return value tells us whether *this* call
        # actually removed the member. Two workers racing on the same instance
        # queue can both read the same top candidate; only one's ZREM returns
        # 1, the other gets 0 and must fall through to the next ranked item.
        # Without this guard the loser used to return the same QueueItem,
        # produce a double execution, and pollute recent_runs.
        for _sort_key, raw, data, winner_meta in ranked:
            try:
                claimed_n = int(await self._redis.zrem(key, raw))
            except (TypeError, ValueError):
                claimed_n = 0
            if claimed_n != 1:
                continue
            await self._append_recent_run(
                instance_id=instance_id,
                task_type=str(data.get("task_type", "")),
                player_id=str(data.get("player_id", "")),
                now=now,
            )
            self._log_pop_winner(instance_id=instance_id, data=data, meta=winner_meta)
            return self._build_queue_item(
                data,
                default_run_at=now,
                effective_priority=int(winner_meta.get("effective_priority", 0)),
            )
        return None

    async def explain_top_n(
        self,
        instance_id: str,
        *,
        current_screen: str = "",
        n: int = 10,
    ) -> list[dict[str, object]]:
        """Top-N due candidates with full effective_priority breakdown.

        Powers the debug command from ADR 0001 §"Debug / operator tools" and
        the deferred-v2 Streamlit panel. Reuses the same gate + ranking tuple
        as ``pop_due`` and ``peek_top_due`` without mutating Redis state.
        """
        now = time.time()
        ranked = await self._collect_ranked_due(instance_id, current_screen, now)
        out: list[dict[str, object]] = []
        for sort_key, _raw, data, meta in ranked[: max(0, int(n))]:
            out.append(
                {
                    "task_id": str(data.get("task_id") or ""),
                    "task_type": str(data.get("task_type") or ""),
                    "player_id": str(data.get("player_id") or ""),
                    "base_priority": int(meta["base_priority"]),
                    "effective_priority": int(meta["effective_priority"]),
                    "graph_debuff": int(meta["graph_debuff"]),
                    "recent_debuff": int(meta["recent_debuff"]),
                    "hops": int(meta["hops"]),
                    "reachable": meta["unreachable_flag"] == 0,
                    "required_node": str(meta.get("required_node") or ""),
                    "recent_count": int(meta["recent_count"]),
                    "run_at": float(data.get("run_at", now)),
                    "created_at": float(data.get("created_at", 0.0)),
                    "sort_key": list(sort_key),
                }
            )
        return out

    async def peek_top_due(
        self, instance_id: str, *, current_screen: str = ""
    ) -> QueueItem | None:
        """Return the top due candidate without popping or appending recent_runs.

        Used by cooperative preemption (ADR 0001 §5): the running scenario checks
        between steps whether a pending task outranks it by ``PREEMPT_MARGIN``.
        Uses exactly the same gate + ranking tuple as ``pop_due``.
        """
        now = time.time()
        ranked = await self._collect_ranked_due(instance_id, current_screen, now)
        if not ranked:
            return None
        _sort_key, _raw, data, winner_meta = ranked[0]
        return self._build_queue_item(
            data,
            default_run_at=now,
            effective_priority=int(winner_meta.get("effective_priority", 0)),
        )

    @staticmethod
    def _build_queue_item(
        data: dict[str, object],
        *,
        default_run_at: float,
        effective_priority: int = 0,
    ) -> QueueItem:
        """Reconstruct a ``QueueItem`` from a queue payload dict."""
        reg = data.get("region")
        region = str(reg).strip() if reg is not None and str(reg).strip() != "" else None
        tap_x = data.get("tap_x_pct")
        tap_y = data.get("tap_y_pct")
        tap_x_pct = float(tap_x) if tap_x is not None else None
        tap_y_pct = float(tap_y) if tap_y is not None else None
        thr = data.get("threshold")
        threshold = float(thr) if thr is not None else None
        sc = data.get("overlay_match_score")
        if sc is None:
            sc = data.get("score")
        score = float(sc) if sc is not None else None
        sn = data.get("set_node")
        set_node = str(sn).strip() if sn is not None and str(sn).strip() != "" else None
        ds = data.get("dsl_scenario")
        dsl_scenario = str(ds).strip() if ds is not None and str(ds).strip() != "" else None
        mtlx = data.get("match_top_left_x")
        mtly = data.get("match_top_left_y")
        tw = data.get("template_w")
        th = data.get("template_h")
        tmx = data.get("tap_match_x_pct")
        tmy = data.get("tap_match_y_pct")
        match_top_left_x = int(mtlx) if mtlx is not None else None
        match_top_left_y = int(mtly) if mtly is not None else None
        template_w = int(tw) if tw is not None else None
        template_h = int(th) if th is not None else None
        tap_match_x_pct = float(tmx) if tmx is not None else None
        tap_match_y_pct = float(tmy) if tmy is not None else None
        ssi = data.get("start_step_index")
        start_step_index = int(ssi) if ssi is not None else 0
        ca = data.get("created_at")
        created_at = float(ca) if ca is not None else 0.0
        return QueueItem(
            task_id=data["task_id"],  # type: ignore[arg-type]
            player_id=data["player_id"],  # type: ignore[arg-type]
            task_type=data["task_type"],  # type: ignore[arg-type]
            priority=int(data.get("priority", 0)),  # type: ignore[arg-type]
            run_at=float(data.get("run_at", default_run_at)),  # type: ignore[arg-type]
            instance_id=str(data.get("instance_id") or ""),
            region=region,
            tap_x_pct=tap_x_pct,
            tap_y_pct=tap_y_pct,
            threshold=threshold,
            score=score,
            set_node=set_node,
            dsl_scenario=dsl_scenario,
            match_top_left_x=match_top_left_x,
            match_top_left_y=match_top_left_y,
            template_w=template_w,
            template_h=template_h,
            tap_match_x_pct=tap_match_x_pct,
            tap_match_y_pct=tap_match_y_pct,
            start_step_index=start_step_index,
            created_at=created_at,
            effective_priority=effective_priority,
        )

    async def peek_all(self) -> list[QueueItem]:
        import json

        results: list[QueueItem] = []
        keys: list[str] = []
        async for k in self._redis.scan_iter(match="wos:queue:*"):
            ks = k.decode() if isinstance(k, bytes) else str(k)
            if ":running" in ks:
                continue
            keys.append(ks)
        for key in keys:
            try:
                items = await self._redis.zrangebyscore(key, "-inf", "+inf", withscores=True)
            except Exception:
                continue
            for raw, score in items:
                data = json.loads(raw)
                results.append(self._build_queue_item(data, default_run_at=float(score)))
        return results

    async def remove(self, task_id: str) -> None:
        import json

        async for key in self._redis.scan_iter(match="wos:queue:*"):
            ks = key.decode() if isinstance(key, bytes) else str(key)
            if ":running" in ks:
                continue
            all_items = await self._redis.zrangebyscore(ks, "-inf", "+inf")
            for raw in all_items:
                data = json.loads(raw)
                if data["task_id"] == task_id:
                    await self._redis.zrem(ks, raw)
                    return

    async def remove_by_task_type(self, task_type: str, instance_id: str) -> int:
        """Remove all queued items matching task_type + instance_id. Returns count removed."""
        import json

        all_items = await self._redis.zrangebyscore(_queue_key(instance_id), "-inf", "+inf")
        removed = 0
        for raw in all_items:
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if (
                str(data.get("task_type") or "") == task_type
                and str(data.get("instance_id") or "") == instance_id
            ):
                await self._redis.zrem(_queue_key(instance_id), raw)
                removed += 1
        return removed

    def _players_for_instance(self, instance_id: str) -> set[str]:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return set(
                    player_ids_for_device_candidates(
                        inst.bluestacks_window_title,
                        inst.instance_id,
                    )
                )
        return set()

    async def _read_recent_counts(
        self, instance_id: str, now: float
    ) -> dict[tuple[str, str], int]:
        """ZRANGEBYSCORE recent_runs window → ``{(task_type, player_id): count}``.

        Members are ``"<task_type>|<player_id>|<uuid>"``; the uuid suffix lets
        multiple events per ``recent_key`` coexist as distinct ZSET members.
        Failures degrade ranking (returns empty); a metric/log on the failure is
        the only signal that recent_debuff has been silenced.
        """
        try:
            members = await self._redis.zrangebyscore(
                _recent_runs_key(instance_id),
                now - RECENT_RUNS_WINDOW_SECONDS,
                "+inf",
            )
        except Exception:
            logger.debug("recent_runs read failed; recent_debuff = 0", exc_info=True)
            return {}
        counts: dict[tuple[str, str], int] = {}
        for m in members:
            s = m.decode() if isinstance(m, bytes) else str(m)
            parts = s.split("|", 2)
            if len(parts) < 2:
                continue
            key = (parts[0], parts[1])
            counts[key] = counts.get(key, 0) + 1
        return counts

    async def _append_recent_run(
        self, *, instance_id: str, task_type: str, player_id: str, now: float
    ) -> None:
        """Record an execution-start event for ranking history.

        Pipeline: ZADD <now> "<recent_key>|<uuid>" + ZREMRANGEBYSCORE (prune
        older than the window) + EXPIRE (a dead worker won't leak garbage).
        Independent of task success/failure: a broken scenario still
        accumulates history and sinks under recent_debuff.
        """
        import uuid

        member = f"{task_type}|{player_id}|{uuid.uuid4().hex[:8]}"
        key = _recent_runs_key(instance_id)
        try:
            pipe = self._redis.pipeline()
            pipe.zadd(key, {member: now})
            pipe.zremrangebyscore(key, "-inf", now - RECENT_RUNS_WINDOW_SECONDS)
            pipe.expire(key, RECENT_RUNS_WINDOW_SECONDS * 2)
            await pipe.execute()
        except Exception:
            logger.debug("recent_runs append failed", exc_info=True)

    def _rank_candidates(
        self,
        due: list[tuple[str, dict[str, object]]],
        *,
        current_screen: str,
        recent_counts: dict[tuple[str, str], int],
        now: float,
    ) -> list[tuple[tuple[int, int, int, float, float], str, dict[str, object], dict[str, object]]]:
        """Compute the full ranking tuple + metadata for every due candidate.

        Returned tuples are ``(sort_key, raw_payload, parsed_data, meta)`` where
        ``sort_key`` follows ADR 0001 §"Final sort key":
        ``(-effective_priority, unreachable_flag, hops, run_at, created_at)``.
        Caller sorts ascending — smallest tuple runs first.

        Shared by ``pop_due`` and ``peek_top_due`` (cooperative preemption).
        """
        required_node_map = self._task_type_to_required_node()
        out: list[tuple[tuple[int, int, int, float, float], str, dict[str, object], dict[str, object]]] = []
        for raw, data in due:
            base = int(data.get("priority", 0))
            ttype = str(data.get("task_type", ""))
            pid = str(data.get("player_id", ""))
            required_node = required_node_map.get(ttype, "")

            if not required_node or not current_screen:
                unreachable_flag = 0
                hops_val = 0
                graph_debuff = 0
            else:
                hops_opt = _bfs_hops(current_screen, required_node)
                if hops_opt is None:
                    unreachable_flag = 1
                    hops_val = HOPS_SENTINEL
                    graph_debuff = UNREACHABLE_DEBUFF
                else:
                    unreachable_flag = 0
                    hops_val = hops_opt
                    graph_debuff = W_HOPS * min(hops_opt, HOPS_DEBUFF_CAP_HOPS)

            recent_count = recent_counts.get((ttype, pid), 0)
            recent_debuff = min(recent_count, RECENT_RUNS_CAP) * W_RECENT
            effective_priority = base - graph_debuff - recent_debuff

            sort_key: tuple[int, int, int, float, float] = (
                -effective_priority,
                unreachable_flag,
                hops_val,
                float(data.get("run_at", now)),
                float(data.get("created_at", 0.0)),
            )
            meta = {
                "base_priority": base,
                "effective_priority": effective_priority,
                "graph_debuff": graph_debuff,
                "recent_debuff": recent_debuff,
                "hops": hops_val,
                "unreachable_flag": unreachable_flag,
                "required_node": required_node,
                "recent_count": recent_count,
            }
            out.append((sort_key, raw, data, meta))
        return out

    @staticmethod
    def _log_pop_winner(
        *, instance_id: str, data: dict[str, object], meta: dict[str, object]
    ) -> None:
        logger.info(
            "queue.pop_due winner instance=%s task_type=%s player=%s "
            "base=%s effective=%s graph_debuff=%s recent_debuff=%s "
            "hops=%s reachable=%s required_node=%r recent_count=%s "
            "run_at=%s created_at=%s task_id=%s",
            instance_id,
            data.get("task_type"),
            data.get("player_id"),
            meta["base_priority"],
            meta["effective_priority"],
            meta["graph_debuff"],
            meta["recent_debuff"],
            meta["hops"],
            meta["unreachable_flag"] == 0,
            meta["required_node"],
            meta["recent_count"],
            data.get("run_at"),
            data.get("created_at"),
            data.get("task_id"),
        )
