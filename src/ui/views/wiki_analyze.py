"""Wiki · Analyze — overlay rules browser and area.json audit."""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from analysis.overlay_rules import optional_ttl_seconds, overlay_rule_screen_allowlist
from config.devices import get_device_registry
from config.module_registry import normalize_module_scope
from config.paths import repo_root
from scenarios.registry import iter_module_analyze_manifests
from ui.module_scope import render_module_scope_selector
from ui.overlay_analyze_audit import OverlayAuditIssue, area_doc_for_module_scope, audit_overlay_rules
from ui.redis_client import get_redis

_SOURCE_KEY = "_wiki_source"


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


def _load_analyze_manifest(path: Path) -> tuple[list[Path], list[dict[str, Any]]]:
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


def _load_overlay_rules_for_scope(
    repo_root_path: Path, module_scope: str
) -> tuple[list[Path], list[dict[str, Any]]]:
    scope = normalize_module_scope(module_scope)
    loaded_files: list[Path] = []
    overlay_rules: list[dict[str, Any]] = []
    for manifest in iter_module_analyze_manifests(repo_root_path, scope):
        lf, rules = _load_analyze_manifest(manifest)
        loaded_files.extend(lf)
        overlay_rules.extend(rules)
    return loaded_files, overlay_rules


def _source_chip_label(path: Path, repo_root_path: Path) -> str:
    try:
        rel = path.relative_to(repo_root_path / "modules")
        parts = list(rel.parts)
        if parts and parts[-1] == "analyze.yaml":
            parts = parts[:-2]
        else:
            parts[-1] = Path(parts[-1]).stem
        return "/".join(parts) if parts else path.stem
    except ValueError:
        return path.stem


def _source_rel(path: Path, repo_root_path: Path) -> str:
    try:
        return path.relative_to(repo_root_path).as_posix()
    except ValueError:
        return path.as_posix()


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


def _humanize_seconds(s: float) -> str:
    s = max(0, int(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, ss = divmod(s, 60)
        return f"{m}m {ss:02d}s"
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"


def _filter_rules(
    rules: list[dict[str, Any]],
    *,
    selected_paths: set[Path],
    query: str,
) -> list[dict[str, Any]]:
    q = query.strip().lower()
    out: list[dict[str, Any]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        if selected_paths and r.get(_SOURCE_KEY) not in selected_paths:
            continue
        hay = "\n".join(
            [str(r.get("name") or ""), str(r.get("action") or "")]
        ).lower()
        if q and q not in hay:
            continue
        out.append(r)
    return out


def _screen_group_key(rule: dict[str, Any]) -> str:
    allow = overlay_rule_screen_allowlist(rule)
    if not allow:
        return "global"
    if len(allow) == 1:
        return allow[0].lower() if allow[0].lower() == "none" else allow[0]
    return ", ".join(allow)


def _render_rule_detail(rule: dict[str, Any], repo_root_path: Path) -> None:
    name = str(rule.get("name") or "").strip() or "(unnamed)"
    action = str(rule.get("action") or "").strip() or "(no action)"
    region = str(rule.get("region") or "").strip()
    screens = overlay_rule_screen_allowlist(rule)
    ttl = optional_ttl_seconds(rule)

    st.markdown(f"**`{name}`** · `{action}`")
    meta: list[str] = []
    if region:
        meta.append(f"region `{region}`")
    if screens:
        meta.append(f"screens `{', '.join(screens)}`")
    if ttl is not None:
        meta.append(f"ttl {_humanize_seconds(ttl)}")
    if meta:
        st.caption(" · ".join(meta))

    src = rule.get(_SOURCE_KEY)
    if isinstance(src, Path):
        st.caption(f"source: `{_source_rel(src, repo_root_path)}`")

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


def _render_audit_tab(
    issues: list[OverlayAuditIssue],
    *,
    filtered_rules: list[dict[str, Any]],
    repo_root_path: Path,
) -> None:
    issue_by_rule: dict[str, list[OverlayAuditIssue]] = {}
    for iss in issues:
        issue_by_rule.setdefault(iss.rule_name, []).append(iss)

    st.caption(
        "Checks mirror startup validation: missing `area.json` regions, red-dot "
        "capability, unknown `pushScenario` targets, and `exist` instead of `findIcon`."
    )

    sev_filter = st.pills(
        "Severity",
        options=["all", "error", "warning"],
        selection_mode="single",
        default="all",
        key="wiki_analyze_audit_severity",
    )
    audit_q = st.text_input(
        "Filter rules or messages",
        value="",
        key="wiki_analyze_audit_filter",
    ).strip().lower()

    rows: list[dict[str, str]] = []
    for rule in filtered_rules:
        nm = str(rule.get("name") or "").strip() or "(unnamed)"
        rule_issues = issue_by_rule.get(nm, [])
        if not rule_issues:
            rows.append(
                {
                    "Severity": "ok",
                    "Rule": nm,
                    "Action": str(rule.get("action") or "").strip() or "—",
                    "Region": str(rule.get("region") or "").strip() or "—",
                    "Source": _source_rel(src, repo_root_path)
                    if isinstance((src := rule.get(_SOURCE_KEY)), Path)
                    else "—",
                    "Message": "—",
                }
            )
            continue
        for iss in rule_issues:
            if sev_filter and sev_filter != "all" and iss.severity != sev_filter:
                continue
            src_rel = "—"
            src = rule.get(_SOURCE_KEY)
            if isinstance(src, Path):
                src_rel = _source_rel(src, repo_root_path)
            row = {
                "Severity": iss.severity,
                "Rule": nm,
                "Action": str(rule.get("action") or "").strip() or "—",
                "Region": str(rule.get("region") or "").strip() or "—",
                "Source": src_rel,
                "Message": iss.message,
            }
            if audit_q:
                hay = " ".join(row.values()).lower()
                if audit_q not in hay:
                    continue
            rows.append(row)

    if not rows:
        st.info("No audit rows match the filters.")
        return

    err_n = sum(1 for r in rows if r["Severity"] == "error")
    warn_n = sum(1 for r in rows if r["Severity"] == "warning")
    ok_n = sum(1 for r in rows if r["Severity"] == "ok")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows", len(rows))
    m2.metric("Errors", err_n)
    m3.metric("Warnings", warn_n)
    m4.metric("OK", ok_n)
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_rules_tab(
    filtered: list[dict[str, Any]],
    *,
    repo_root_path: Path,
) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in filtered:
        groups.setdefault(_screen_group_key(r), []).append(r)

    st.subheader(f"Rules: {len(filtered)} · Screen groups: {len(groups)}")
    for node in sorted(groups.keys(), key=lambda s: (s == "global", s == "none", s.lower())):
        with st.expander(f"`{node}` · {len(groups[node])}", expanded=(node not in {"global", "none"})):
            for idx, rule in enumerate(groups[node], start=1):
                nm = str(rule.get("name") or "").strip() or f"rule_{idx}"
                act = str(rule.get("action") or "").strip() or "action"
                with st.expander(f"{nm} · `{act}`", expanded=False):
                    _render_rule_detail(rule, repo_root_path)


def _render_ttl_tab(rules: list[dict[str, Any]]) -> None:
    try:
        all_players = sorted(set(get_device_registry().all_player_ids()))
    except Exception:
        all_players = []
    if not all_players:
        st.caption(
            "No players in `db/devices.yaml` — TTL is recorded per-player; "
            "configure at least one account to read live state."
        )
        return

    sel_pid = st.selectbox(
        "Player",
        options=all_players,
        index=0,
        key="wiki_analyze_ttl_player",
        help="Overlay TTL is stored per player in `wos:player:<id>:overlay_ttl`.",
    )
    try:
        client = get_redis()
    except Exception:
        st.caption("Redis unreachable — cannot show live TTL state.")
        return

    try:
        raw_ttl = client.hgetall(f"wos:player:{sel_pid}:overlay_ttl") if client else {}
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
            state = "ready"
            next_label = "now" if ttl_s is not None else "—"
        else:
            elapsed = max(0.0, now - last)
            elapsed_label = _humanize_seconds(elapsed) + " ago"
            if ttl_s is None or elapsed >= ttl_s:
                state = "ready"
                next_label = "now" if ttl_s is not None else "—"
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
        st.caption("No overlay rules with a `name` for TTL tracking.")
        return

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


def _build_source_chips(
    loaded_files: list[Path],
    overlay_rules: list[dict[str, Any]],
    repo_root_path: Path,
) -> tuple[list[str], dict[str, Path]]:
    rule_counts: dict[Path, int] = {}
    for r in overlay_rules:
        src = r.get(_SOURCE_KEY)
        if isinstance(src, Path):
            rule_counts[src] = rule_counts.get(src, 0) + 1

    chip_label_to_path: dict[str, Path] = {}
    chip_options: list[str] = []
    for src in loaded_files:
        base = _source_chip_label(src, repo_root_path)
        label = f"{base} · {rule_counts.get(src, 0)}"
        chip_label_to_path[label] = src
        chip_options.append(label)
    return chip_options, chip_label_to_path


# --- Page ---------------------------------------------------------------------

st.title("Wiki · Analyze")
st.caption(
    "Overlay rules from `modules/*/analyze/analyze.yaml`, validated against "
    "the active module scope's `area.json` / `area.yaml`."
)

root = repo_root()

with st.sidebar:
    module_scope = render_module_scope_selector(in_sidebar=True)
    loaded_files, overlay_rules = _load_overlay_rules_for_scope(root, module_scope)

    if not overlay_rules:
        st.warning("No `overlay` rules for this module scope.")
        st.stop()

    chip_options, chip_label_to_path = _build_source_chips(loaded_files, overlay_rules, root)
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
name_filter = st.text_input(
    "Filter (name/action contains)",
    value="",
    key="wiki_analyze_filter",
).strip().lower()

filtered = _filter_rules(
    overlay_rules, selected_paths=selected_paths, query=name_filter
)

area_doc = area_doc_for_module_scope(root, module_scope)
audit_issues = audit_overlay_rules(area_doc, filtered, repo_root_path=root)
issue_counts = Counter(i.severity for i in audit_issues)
rules_with_errors = {i.rule_name for i in audit_issues if i.severity == "error"}

m1, m2, m3, m4 = st.columns(4)
m1.metric("Rules", len(filtered))
m2.metric("Manifests", len(loaded_files))
m3.metric("Audit errors", issue_counts.get("error", 0))
m4.metric("Rules w/ error", len(rules_with_errors))

tab_audit, tab_rules, tab_ttl = st.tabs(["Audit", "Rules", "TTL"])

with tab_audit:
    _render_audit_tab(
        audit_issues,
        filtered_rules=filtered,
        repo_root_path=root,
    )

with tab_rules:
    _render_rules_tab(filtered, repo_root_path=root)

with tab_ttl:
    _render_ttl_tab(filtered)
