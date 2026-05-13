"""Wiki: browse analyze/overlay YAML as a human-readable story."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from analysis.overlay_manifest import default_analyze_yaml_path
from analysis.overlay_rules import optional_ttl_seconds, overlay_rule_screen_allowlist
from config.devices import get_device_registry
from ui.redis_client import get_redis


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _resolve_includes(manifest_path: Path, include: list[object]) -> list[Path]:
    out: list[Path] = []
    for item in include:
        s = str(item or "").strip()
        if not s:
            continue
        p = Path(s)
        if not p.is_absolute():
            p = manifest_path.parent / p
        out.append(p)
    return out


_SOURCE_KEY = "_wiki_source"


def _load_analyze_manifest(path: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Returns (loaded_files, merged_overlay_rules); each rule carries its source path."""
    if not path.is_file():
        return ([], [])

    raw = _load_yaml_dict(path)
    overlay_merged: list[dict[str, Any]] = []

    def _tag(rules: list[Any], src: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            tagged = dict(r)
            tagged[_SOURCE_KEY] = src
            out.append(tagged)
        return out

    ov = raw.get("overlay")
    if isinstance(ov, list):
        overlay_merged.extend(_tag(ov, path))

    loaded: list[Path] = [path]

    inc = raw.get("include")
    if isinstance(inc, list) and inc:
        for inc_path in _resolve_includes(path, inc):
            if not inc_path.is_file():
                continue
            loaded.append(inc_path)
            doc = _load_yaml_dict(inc_path)
            ov2 = doc.get("overlay")
            if isinstance(ov2, list):
                overlay_merged.extend(_tag(ov2, inc_path))

    return (loaded, overlay_merged)


def _source_chip_label(path: Path, repo_root: Path, manifest: Path) -> str:
    """Short, human-readable chip label for a YAML source.

    - manifest itself → ``manifest``
    - ``analyze_pages/analyze_common.yaml`` → ``common``
    - ``analyze_pages/events/7-day.yaml`` → ``events/7-day``
    """
    if path == manifest:
        return "manifest"
    try:
        rel = path.relative_to(repo_root / "analyze" / "analyze_pages")
    except ValueError:
        return path.stem
    parts = list(rel.parts)
    parts[-1] = Path(parts[-1]).stem
    leaf = parts[-1]
    if leaf.startswith("analyze_"):
        leaf = leaf[len("analyze_"):]
    parts[-1] = leaf
    return "/".join(parts)


def _scenario_names_from_key(rule: dict[str, Any], key: str) -> list[str]:
    pu = rule.get(key)
    if not isinstance(pu, list):
        return []

    out: list[str] = []
    for item in pu:
        if not isinstance(item, dict):
            continue
        task = item.get("task")
        src = task if isinstance(task, dict) else item
        nm = str(src.get("name") or src.get("type") or "").strip()
        if nm:
            out.append(nm)
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _render_rule(rule: dict[str, Any]) -> None:
    name = str(rule.get("name") or "").strip() or "(unnamed)"
    action = str(rule.get("action") or "").strip() or "(no action)"
    st.markdown(f"**`{name}`** · `{action}`")
    src = rule.get(_SOURCE_KEY)
    if isinstance(src, Path):
        try:
            st.caption(f"source: `{src.relative_to(_repo_root()).as_posix()}`")
        except ValueError:
            st.caption(f"source: `{src.as_posix()}`")

    scenarios = _scenario_names_from_key(rule, "pushScenario")
    if scenarios:
        st.markdown("**pushScenario**")
        for s in scenarios:
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            with c1:
                st.markdown(f"- `{s}`")
            with c2:
                st.page_link(
                    "views/scenarios.py",
                    label="Open",
                    query_params={"q": s},
                    width="stretch",
                )


def _humanize_seconds(s: float) -> str:
    """Compact ``5m 12s`` / ``1h 03m`` / ``42s`` representation."""
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, ss = divmod(s, 60)
        return f"{m}m {ss:02d}s"
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


def _render_ttl_table(rules: list[dict[str, Any]]) -> None:
    """Per-rule TTL state for the selected player.

    Overlay rule cooldowns belong to the player whose state the overlay was
    evaluating against — two accounts on the same emulator have independent
    timers. The worker keys snapshots by active_player at evaluation time
    and writes them to ``wos:player:<pid>:overlay_ttl``; this table reads
    that hash.
    """
    try:
        all_players = sorted(set(get_device_registry().all_player_ids()))
    except Exception:
        all_players = []
    if not all_players:
        st.caption(
            "No players in `db/devices.yaml` — TTL is recorded per-player, "
            "so the table needs at least one configured account to read."
        )
        return
    sel_pid = st.selectbox(
        "Player",
        options=all_players,
        index=0,
        key="wiki_analyze_ttl_player",
        help="Overlay TTL state is recorded per-player. The same emulator "
        "may host several accounts; each gets its own cooldown clock.",
    )
    client = None
    try:
        client = get_redis()
    except Exception:
        st.caption("Redis unreachable — cannot show live TTL state.")
        return
    try:
        raw_ttl = (
            client.hgetall(f"wos:player:{sel_pid}:overlay_ttl") if client else {}
        )
    except Exception:
        raw_ttl = {}
    last_eval_at: dict[str, float] = {}
    for k, v in (raw_ttl or {}).items():
        ks = k.decode() if isinstance(k, bytes) else str(k)
        vs = v.decode() if isinstance(v, bytes) else str(v)
        try:
            last_eval_at[ks] = float(vs)
        except (TypeError, ValueError):
            continue

    now = time.time()
    rows: list[dict[str, Any]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        if not nm:
            continue
        ttl_s = optional_ttl_seconds(r)
        last = last_eval_at.get(nm)
        if last is None:
            elapsed_label = "never"
            if ttl_s is None:
                state = "ready"
                next_label = "—"
            else:
                state = "ready"
                next_label = "now"
        else:
            elapsed = max(0.0, now - last)
            elapsed_label = _humanize_seconds(elapsed) + " ago"
            if ttl_s is None:
                state = "ready"
                next_label = "—"
            elif elapsed >= ttl_s:
                state = "ready"
                next_label = "now"
            else:
                state = "throttled"
                next_label = "in " + _humanize_seconds(ttl_s - elapsed)
        rows.append(
            {
                "Rule": nm,
                "Action": str(r.get("action") or "").strip() or "—",
                "Screens": ", ".join(overlay_rule_screen_allowlist(r)) or "global",
                "TTL": _humanize_seconds(ttl_s) if ttl_s is not None else "—",
                "Last eval": elapsed_label,
                "Next eval": next_label,
                "State": state,
            }
        )
    if not rows:
        st.caption("No overlay rules to display.")
        return

    # Filter chips: state + name search shared with the main browser below.
    fc1, fc2 = st.columns([1, 2], vertical_alignment="bottom")
    with fc1:
        state_filter = st.pills(
            "State",
            options=["all", "ready", "throttled"],
            selection_mode="single",
            default="all",
            key="wiki_analyze_ttl_state_filter",
        )
    with fc2:
        ttl_query = st.text_input(
            "Rule name contains",
            value="",
            key="wiki_analyze_ttl_filter",
        ).strip().lower()

    if state_filter and state_filter != "all":
        rows = [r for r in rows if r["State"] == state_filter]
    if ttl_query:
        rows = [r for r in rows if ttl_query in r["Rule"].lower()]
    rows.sort(key=lambda r: (r["State"] != "throttled", r["Rule"]))

    st.dataframe(rows, hide_index=True, width="stretch")


st.title("Wiki · Analyze")
st.caption("Browse `analyze/analyze.yaml` overlay rules as a readable story.")

repo_root = _repo_root()
analyze_path = default_analyze_yaml_path(repo_root)

loaded_files, overlay_rules = _load_analyze_manifest(analyze_path)
if not overlay_rules:
    st.warning(f"No `overlay` rules loaded from `{analyze_path}`.")
    st.stop()

with st.expander("⏱ Overlay TTL state", expanded=False):
    _render_ttl_table(overlay_rules)

# Chip filter: one chip per source file. Manifest pinned first, then
# include order. Rule count rendered next to each label so the user can see
# weight before clicking.
rule_counts: dict[Path, int] = {}
for r in overlay_rules:
    src = r.get(_SOURCE_KEY)
    if isinstance(src, Path):
        rule_counts[src] = rule_counts.get(src, 0) + 1

chip_label_to_path: dict[str, Path] = {}
chip_options: list[str] = []
for src in loaded_files:
    base = _source_chip_label(src, repo_root, analyze_path)
    label = f"{base} · {rule_counts.get(src, 0)}"
    chip_label_to_path[label] = src
    chip_options.append(label)

with st.sidebar:
    st.caption("Sources")
    selected_chips = st.pills(
        "Source files",
        options=chip_options,
        selection_mode="multi",
        default=[],
        label_visibility="collapsed",
        key="wiki_analyze_sources",
    )

selected_paths: set[Path] = {
    chip_label_to_path[c] for c in (selected_chips or []) if c in chip_label_to_path
}

q = st.text_input("Filter (name/action contains)", value="", key="wiki_analyze_filter").strip().lower()

filtered: list[dict[str, Any]] = []
for r in overlay_rules:
    if not isinstance(r, dict):
        continue
    if selected_paths and r.get(_SOURCE_KEY) not in selected_paths:
        continue
    hay = "\n".join(
        [
            str(r.get("name") or ""),
            str(r.get("action") or ""),
        ]
    ).lower()
    if q and q not in hay:
        continue
    filtered.append(r)

groups: dict[str, list[dict[str, Any]]] = {}
for r in filtered:
    allow = overlay_rule_screen_allowlist(r)
    if not allow:
        key = "global"
    elif len(allow) == 1:
        key = allow[0].lower() if allow[0].lower() == "none" else allow[0]
    else:
        key = ", ".join(allow)
    groups.setdefault(key, []).append(r)

st.subheader(f"Rules: {len(filtered)} · Screen groups: {len(groups)}")

for node in sorted(groups.keys(), key=lambda s: (s == "global", s == "none", s.lower())):
    with st.expander(f"`{node}` · {len(groups[node])}", expanded=(node not in {"global", "none"})):
        for idx, rule in enumerate(groups[node], start=1):
            nm = str(rule.get("name") or "").strip() or f"rule_{idx}"
            act = str(rule.get("action") or "").strip() or "action"
            label = f"{nm} · `{act}`"
            with st.expander(label, expanded=False):
                _render_rule(rule)

