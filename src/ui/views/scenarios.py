"""Scenario YAML listing and per-player assignment in Redis."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import streamlit as st
import yaml
from streamlit_nested_table import nested_table, table_column

from config.devices import player_ids_for_device_candidates
from config.loader import Settings, load_settings
from config.paths import repo_root as default_repo_root
from scenarios.cron_specs import (
    iter_cron_yaml_files_for_repo,
    iter_plain_scenario_yaml_files_for_repo,
    resolve_cron_priority,
    resolve_cron_task_type,
)
from scenarios.registry import scenario_source_label
from ui.module_scope import render_module_scope_selector
from ui.redis_client import (
    format_scenario_redis_purge_result,
    get_player_scenario,
    purge_scenarios_from_redis,
    require_redis_connection,
    set_player_scenario,
)


def _set_scenario_enabled(path: Path, enabled: bool) -> None:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        msg = "expected YAML mapping"
        raise ValueError(msg)
    raw["enabled"] = enabled
    path.write_text(
        yaml.dump(
            raw,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )
    )


def _all_player_ids(settings: Settings) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for inst in settings.instances:
        for pid in player_ids_for_device_candidates(
            inst.bluestacks_window_title,
            inst.instance_id,
        ):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def _scenario_rel_top_folder(rel: str) -> str:
    """Top folder for pills — ``module:<id>`` or first path segment."""

    parts = Path(rel).parts
    if parts and parts[0] == "modules" and len(parts) >= 2:
        return f"module:{parts[1]}"
    return parts[0] if len(parts) >= 2 else "(root)"


@dataclass
class _FolderNode:
    subfolders: dict[str, _FolderNode] = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)


def _scenario_link_url(scenario_key: str, page: str) -> str:
    """Full URL to a sibling page with ``scenario=<scenario key>``."""
    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    parts = [p for p in u.path.strip("/").split("/") if p]
    if parts:
        parts[-1] = page
        new_path = "/" + "/".join(parts)
    else:
        new_path = "/" + page
    query = urlencode({"scenario": scenario_key})
    return urlunparse((u.scheme, u.netloc, new_path, "", query, ""))


def _debug_scenario_link_url(scenario_key: str) -> str:
    return _scenario_link_url(scenario_key, "debug_scenarios")


def _edit_scenario_link_url(scenario_key: str) -> str:
    return _scenario_link_url(scenario_key, "edit_scenarios")


def _scenario_ids_from_meta(
    scenario_meta: list[tuple[Path, str, str, str, dict]],
) -> set[str]:
    return {sid for _path, _rel, sid, _name, _raw in scenario_meta}


def _sync_select_all_checkbox(*, filtered_ids: set[str], selected_ids: set[str]) -> None:
    """Keep the toolbar «select all» checkbox aligned with row selection."""
    if not filtered_ids:
        st.session_state["scenarios_select_all"] = False
        return
    st.session_state["scenarios_select_all"] = filtered_ids <= selected_ids


@dataclass(frozen=True)
class _BulkEnableResult:
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]
    missing: tuple[str, ...]


def _bump_scenarios_editor_nonce() -> None:
    st.session_state["scenarios_editor_nonce"] = (
        int(st.session_state.get("scenarios_editor_nonce", 0)) + 1
    )


def _on_scenarios_select_all() -> None:
    filtered: set[str] = st.session_state.get("scenarios_bulk_filtered_ids") or set()
    if st.session_state.get("scenarios_select_all"):
        st.session_state["scenarios_selected_ids"] = set(filtered)
    else:
        st.session_state["scenarios_selected_ids"] = set()
    _bump_scenarios_editor_nonce()


def _apply_bulk_enabled_to_ids(
    *,
    selected_ids: set[str],
    path_by_id: dict[str, Path],
    repo_root: Path,
    enabled: bool,
    on_progress: Callable[[float, str], None] | None = None,
) -> _BulkEnableResult:
    """Write ``enabled`` into scenario YAML for each selected id (real disk I/O)."""
    ordered = sorted(selected_ids)
    total = len(ordered)
    changed: list[str] = []
    unchanged: list[str] = []
    missing: list[str] = []
    for i, sid_key in enumerate(ordered):
        path = path_by_id.get(sid_key)
        if path is None:
            missing.append(sid_key)
            if on_progress is not None and total:
                on_progress((i + 1) / total, f"skip missing id={sid_key}")
            continue
        rel = scenario_source_label(path, repo_root)
        if on_progress is not None and total:
            on_progress(i / total, rel)
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            msg = f"{rel}: expected YAML mapping"
            raise ValueError(msg)
        was = bool(raw.get("enabled", False))
        if was == enabled:
            unchanged.append(rel)
        else:
            _set_scenario_enabled(path, enabled)
            changed.append(rel)
        if on_progress is not None and total:
            on_progress((i + 1) / total, rel)
    return _BulkEnableResult(
        changed=tuple(changed),
        unchanged=tuple(unchanged),
        missing=tuple(missing),
    )


def _purge_disabled_in_redis(
    client: object,
    settings: Settings,
    scenario_ids: set[str],
) -> str:
    if not scenario_ids:
        return ""
    purge = purge_scenarios_from_redis(
        client,  # type: ignore[arg-type]
        scenario_ids=scenario_ids,
        player_ids=_all_player_ids(settings),
        instance_ids=[inst.instance_id for inst in settings.instances],
    )
    return format_scenario_redis_purge_result(purge)


def _format_bulk_result_message(result: _BulkEnableResult, *, enabled: bool) -> str:
    label = "enabled" if enabled else "disabled"
    parts: list[str] = []
    if result.changed:
        parts.append(
            f"**{len(result.changed)}** file(s) → `enabled: {str(enabled).lower()}` "
            f"({label}): " + ", ".join(f"`{p}`" for p in result.changed[:12])
        )
        if len(result.changed) > 12:
            parts[-1] += f" … +{len(result.changed) - 12} more"
    if result.unchanged:
        parts.append(
            f"**{len(result.unchanged)}** already {label} (no write): "
            + ", ".join(f"`{p}`" for p in result.unchanged[:6])
        )
        if len(result.unchanged) > 6:
            parts[-1] += f" … +{len(result.unchanged) - 6} more"
    if result.missing:
        parts.append(f"**{len(result.missing)}** id(s) not found on disk: {', '.join(result.missing)}")
    return " · ".join(parts) if parts else "Nothing to update."


def _scenario_file_row(sid: str, name: str, raw: dict) -> dict:
    steps = raw.get("steps")
    n_steps = len(steps) if isinstance(steps, list) else 0
    return {
        "id": sid,
        "name": name,
        "edit": _edit_scenario_link_url(sid),
        "debug": _debug_scenario_link_url(sid),
        "edit_text": "Open",
        "debug_text": "Run",
        "enabled": bool(raw.get("enabled", False)),
        "steps": n_steps,
        "selectable": True,
    }


def _build_folder_tree_from_meta(
    scenario_meta: list[tuple[Path, str, str, str, dict]],
) -> _FolderNode:
    """Group display rows by relative path (folder nesting)."""
    root = _FolderNode()
    for _path, rel, sid, name, raw in scenario_meta:
        row = _scenario_file_row(sid, name, raw)
        parts = Path(rel).parts
        node = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node.files.append(row)
            else:
                node.subfolders.setdefault(part, _FolderNode())
                node = node.subfolders[part]
    return root


def _folder_node_to_nested_rows(node: _FolderNode, prefix: tuple[str, ...]) -> list[dict]:
    """Convert folder tree to nested-table rows (folders → ``subRows``)."""
    out: list[dict] = []
    for name in sorted(node.subfolders.keys()):
        child = node.subfolders[name]
        folder_parts = prefix + (name,)
        folder_id = "folder:" + "/".join(folder_parts)
        children = _folder_node_to_nested_rows(child, folder_parts)
        out.append(
            {
                "id": folder_id,
                "name": f"{name}/",
                "edit": "",
                "debug": "",
                "edit_text": "",
                "debug_text": "",
                "enabled": None,
                "steps": None,
                "selectable": False,
                "subRows": children,
            }
        )
    out.extend(
        sorted(
            node.files,
            key=lambda r: (str(r.get("id") or ""), str(r.get("name") or "")),
        )
    )
    return out


def _nested_scenario_table_columns() -> list:
    return [
        table_column("id", "ID", width=140),
        table_column("name", "Name"),
        table_column("edit", "Edit", width=72, cell_type="link", link_text_key="edit_text"),
        table_column("debug", "Debug", width=72, cell_type="link", link_text_key="debug_text"),
        table_column("enabled", "Enabled", width=88, align="center", cell_type="bool"),
        table_column("steps", "Steps", width=72, align="right"),
    ]


st.title("Scenarios")

_nav = st.columns([1, 1, 5])
with _nav[0]:
    st.page_link(
        "views/routes.py",
        label="Routes",
        help="Screen transition graph and tap routing between game screens.",
        width="stretch",
    )
with _nav[1]:
    st.page_link(
        "views/edit_scenarios.py",
        label="Editor",
        help="Structured form-based editor for DSL scenarios.",
        width="stretch",
    )
with _nav[2]:
    st.page_link(
        "views/debug_scenarios.py",
        label="Debug runner",
        help="Force a selected scenario to run next on an instance.",
        width="stretch",
    )

settings = load_settings()
client = require_redis_connection()
repo_root = default_repo_root()

with st.sidebar:
    module_scope = render_module_scope_selector(in_sidebar=True)

files = iter_plain_scenario_yaml_files_for_repo(repo_root, module_scope)
cron_files = iter_cron_yaml_files_for_repo(repo_root, module_scope)

if not files and not cron_files:
    st.warning("No scenario YAML for this module scope (excluding drafts/).")
    st.stop()

scenario_meta: list[tuple[Path, str, str, str, dict]] = []
for path in files:
    rel = scenario_source_label(path, repo_root)
    try:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            st.error(f"{rel}: expected YAML mapping")
            continue
        sid = str(raw.get("id", path.stem))
        name = str(raw.get("name", path.stem))
        scenario_meta.append((path, rel, sid, name, raw))
    except (yaml.YAMLError, OSError) as exc:
        st.error(f"{rel}: {exc}")

cron_meta: list[tuple[Path, str, str, dict]] = []
for path in cron_files:
    try:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            st.error(f"{scenario_source_label(path, repo_root)}: expected YAML mapping")
            continue
        # Cron specs are identified by `name` (human), fallback to filename.
        name = str(raw.get("name", path.stem)).strip() or path.stem
        cid = name  # show as id in UI; scheduler normalizes internally
        cron_meta.append((path, cid, name, raw))
    except (yaml.YAMLError, OSError) as exc:
        st.error(f"{scenario_source_label(path, repo_root)}: {exc}")

if not scenario_meta and not cron_meta:
    st.warning("No valid YAML files.")
    st.stop()

_folder_counts: Counter[str] = Counter()
for _path, rel, *_rest in scenario_meta:
    _folder_counts[_scenario_rel_top_folder(rel)] += 1
for path, *_rest in cron_meta:
    rel_c = scenario_source_label(path, repo_root)
    _folder_counts[_scenario_rel_top_folder(rel_c)] += 1

_pills_labels = [
    f"{name} · {cnt}"
    for name, cnt in sorted(
        _folder_counts.items(),
        key=lambda kv: (kv[0] == "(root)", kv[0].lower()),
    )
]
_folder_label_to_name: dict[str, str] = {
    f"{name} · {cnt}": name for name, cnt in _folder_counts.items()
}

with st.sidebar:
    st.caption("Folder filter")
    _folder_pick = st.pills(
        "Scenario folders",
        options=_pills_labels,
        selection_mode="multi",
        default=[],
        label_visibility="collapsed",
        key="scenarios_folder_pills",
        help="Limit **Scenario files** and **Cron jobs** lists. Empty = all folders.",
    )
_selected_folders: set[str] = {
    _folder_label_to_name[lab] for lab in (_folder_pick or []) if lab in _folder_label_to_name
}

path_by_file: dict[str, Path] = {}
path_by_id: dict[str, Path] = {}
for path, rel, sid, _name, _raw in scenario_meta:
    path_by_file[rel] = path
    path_by_id[sid] = path

tab_files, tab_cron, tab_assign = st.tabs(["Scenario files", "Cron jobs", "Player assignment"])

with tab_files:
    _bulk_msg = st.session_state.pop("scenarios_bulk_message", None)
    if isinstance(_bulk_msg, tuple) and len(_bulk_msg) == 2:
        level, text = _bulk_msg
        if level == "success":
            st.success(text)
        elif level == "warning":
            st.warning(text)
        elif level == "error":
            st.error(text)
        else:
            st.info(text)

    qp0 = st.query_params
    _filter_default = ""
    _qv = qp0.get("q")
    if _qv is not None:
        _filter_default = _qv[0] if isinstance(_qv, list) else str(_qv)
    else:
        _sv = qp0.get("scenario")
        if _sv is not None:
            _filter_default = _sv[0] if isinstance(_sv, list) else str(_sv)

    file_filter = st.text_input(
        "Filter by id / name / path",
        value=_filter_default,
        key="scenarios_tab_files_filter",
    ).strip().lower()

    scenario_meta_filtered: list[tuple[Path, str, str, str, dict]] = []
    for tup in scenario_meta:
        _path, rel, sid, name, _raw = tup
        if _selected_folders and _scenario_rel_top_folder(rel) not in _selected_folders:
            continue
        hay = f"{sid}\n{name}\n{rel}".lower()
        if not file_filter or file_filter in hay:
            scenario_meta_filtered.append(tup)

    st.caption(
        "Expand folders to browse scenarios. Use checkboxes (or row click) to select, then **Enable** / **Disable**."
    )

    filtered_ids = _scenario_ids_from_meta(scenario_meta_filtered)
    st.session_state.setdefault("scenarios_selected_ids", set())
    st.session_state.setdefault("scenarios_editor_nonce", 0)
    selected_ids: set[str] = set(st.session_state["scenarios_selected_ids"])
    selected_ids &= filtered_ids
    st.session_state["scenarios_selected_ids"] = selected_ids
    st.session_state["scenarios_bulk_filtered_ids"] = filtered_ids

    if not scenario_meta_filtered:
        st.warning("No scenarios match the filter.")
    else:
        tree = _build_folder_tree_from_meta(scenario_meta_filtered)
        nested_rows = _folder_node_to_nested_rows(tree, ())
        table_nonce = int(st.session_state.get("scenarios_editor_nonce", 0))
        selection = nested_table(
            nested_rows,
            _nested_scenario_table_columns(),
            sub_rows_key="subRows",
            height=520,
            default_expanded=False,
            striped=True,
            multi_select=True,
            selected_ids=sorted(selected_ids),
            key=f"scenarios_nested_table_{table_nonce}",
        )
        if isinstance(selection, dict):
            raw_ids = selection.get("selectedIds")
            if isinstance(raw_ids, list):
                st.session_state["scenarios_selected_ids"] = {
                    str(x) for x in raw_ids if str(x) in filtered_ids
                }
    selected_ids = set(st.session_state["scenarios_selected_ids"])
    _sync_select_all_checkbox(filtered_ids=filtered_ids, selected_ids=selected_ids)

    _bulk = st.columns([1.2, 1, 1, 4])
    with _bulk[0]:
        st.checkbox(
            "Select all",
            key="scenarios_select_all",
            on_change=_on_scenarios_select_all,
            disabled=not filtered_ids,
        )
    with _bulk[1]:
        enable_selected = st.button(
            "Enable",
            key="scenarios_enable_selected",
            width="stretch",
            help="Write `enabled: true` to selected module scenario YAML files",
        )
    with _bulk[2]:
        disable_selected = st.button(
            "Disable",
            key="scenarios_disable_selected",
            width="stretch",
            help="Write `enabled: false` to selected module scenario YAML files",
        )
    with _bulk[3]:
        n_sel = len(selected_ids)
        n_vis = len(filtered_ids)
        st.caption(
            f"{n_sel} selected · {n_vis} visible · **Disable** writes YAML and purges "
            "Redis (queue, player override, push TTL)"
            if n_vis
            else "No scenarios match the filter."
        )

    if enable_selected or disable_selected:
        want_enabled = bool(enable_selected)
        apply_ids = set(st.session_state.get("scenarios_selected_ids") or set())
        if not apply_ids:
            st.session_state["scenarios_bulk_message"] = (
                "warning",
                "Nothing selected — check rows in the table or use **Select all**.",
            )
            st.rerun()
        progress = st.progress(0.0, text="Reading and writing scenario YAML…")
        try:

            def _on_progress(fraction: float, label: str) -> None:
                progress.progress(
                    min(1.0, max(0.0, fraction)),
                    text=f"Updating `{label}`…",
                )

            result = _apply_bulk_enabled_to_ids(
                selected_ids=apply_ids,
                path_by_id=path_by_id,
                repo_root=repo_root,
                enabled=want_enabled,
                on_progress=_on_progress,
            )
            redis_note = ""
            if not want_enabled:
                progress.progress(0.92, text="Purging Redis queue / overrides…")
                redis_note = _purge_disabled_in_redis(client, settings, apply_ids)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            st.session_state["scenarios_bulk_message"] = ("error", f"Failed to update YAML: {exc}")
            st.rerun()
        except Exception as exc:
            st.session_state["scenarios_bulk_message"] = (
                "error",
                f"YAML updated but Redis purge failed: {exc}",
            )
            st.rerun()
        progress.progress(1.0, text="Done")
        _bump_scenarios_editor_nonce()
        yaml_msg = _format_bulk_result_message(result, enabled=want_enabled)
        if redis_note:
            yaml_msg = f"{yaml_msg} · {redis_note}" if yaml_msg else redis_note
        if result.changed:
            st.session_state["scenarios_bulk_message"] = ("success", yaml_msg)
        elif result.unchanged and not result.missing:
            if not want_enabled and redis_note:
                st.session_state["scenarios_bulk_message"] = ("success", yaml_msg)
            else:
                st.session_state["scenarios_bulk_message"] = (
                    "info",
                    f"All **{len(result.unchanged)}** selected file(s) already had "
                    f"`enabled: {str(want_enabled).lower()}` — no disk writes."
                    + (f" · {redis_note}" if redis_note else ""),
                )
        else:
            st.session_state["scenarios_bulk_message"] = (
                "warning",
                yaml_msg,
            )
        st.rerun()

    with st.expander("Raw YAML", expanded=False):
        rels = [rel for _, rel, _, _, _ in scenario_meta]
        pick = st.selectbox("Scenario file", rels, key="scenarios_yaml_pick")
        if pick and pick in path_by_file:
            st.code(path_by_file[pick].read_text(), language="yaml")

with tab_cron:
    if not cron_meta:
        st.info("No module scenario YAML files with a root `cron` field.")
    else:
        import json
        import re
        import time

        cron_meta_filtered = [
            (path, cid, name, raw)
            for path, cid, name, raw in cron_meta
            if (
                not _selected_folders
                or _scenario_rel_top_folder(scenario_source_label(path, repo_root))
                in _selected_folders
            )
        ]

        cron_path_by_id: dict[str, Path] = {}
        cron_rows: list[dict] = []
        for path, cid, name, raw in cron_meta_filtered:
            cron_path_by_id[cid] = path
            # Resolve via the same helpers the scheduler uses so the table
            # mirrors what would actually run — most cron YAMLs rely on the
            # stem fallback for ``task`` and the unified default for
            # ``priority`` (no explicit fields).
            resolved_task = resolve_cron_task_type(raw, path)
            resolved_priority = resolve_cron_priority(raw.get("priority"))
            cron_rows.append(
                {
                    "name": name,
                    "enabled": bool(raw.get("enabled", True)),
                    "cron": str(raw.get("cron", "")),
                    "task": resolved_task,
                    "task_explicit": isinstance(raw.get("task") or raw.get("task_type"), str)
                    and bool(str(raw.get("task") or raw.get("task_type") or "").strip()),
                    "priority": resolved_priority,
                    "priority_explicit": raw.get("priority") is not None
                    and not isinstance(raw.get("priority"), bool),
                    "file": scenario_source_label(path, repo_root),
                }
            )

        def _slug(s: str) -> str:
            return re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip()).strip("._-") or "cron"

        def _push_cron_task_now(*, task_type: str, priority: int, name: str) -> int:
            """Enqueue the cron job immediately for all players on all instances.

            Returns the number of queue items enqueued.
            """
            now = time.time()
            spec = _slug(name)
            n = 0
            for inst in settings.instances:
                for pid in player_ids_for_device_candidates(
                    inst.bluestacks_window_title,
                    inst.instance_id,
                ):
                    payload = json.dumps(
                        {
                            "task_id": f"ui:cronpush:{spec}:{pid}:{int(now)}",
                            "player_id": pid,
                            "task_type": task_type,
                            "priority": int(priority),
                            "run_at": float(now),
                            "instance_id": inst.instance_id,
                        }
                    )
                    # Same structure as scheduler.queue.RedisQueue.schedule (ZADD score = run_at)
                    client.zadd(f"wos:queue:{inst.instance_id}", {payload: float(now)})
                    n += 1
            return n

        if not cron_rows:
            st.warning(
                "No cron specs in the selected folder(s). "
                "Clear **Folder** pills in the sidebar to see all."
            )
        else:
            # Render an explicit table so we can have per-row action buttons (Push).
            # `st.data_editor` cannot embed buttons inside rows.
            st.markdown("**Cron specs**")
            hdr = st.columns([3.2, 1.0, 1.6, 2.0, 1.0, 2.0, 1.1], vertical_alignment="center")
            hdr[0].markdown("**Name**")
            hdr[1].markdown("**Enabled**")
            hdr[2].markdown("**Cron**")
            hdr[3].markdown("**Task**")
            hdr[4].markdown("**Prio**")
            hdr[5].markdown("**File**")
            hdr[6].markdown("**Push**")

            for row in cron_rows:
                nm = str(row.get("name") or "")
                rk = _slug(nm)
                cols = st.columns([3.2, 1.0, 1.6, 2.0, 1.0, 2.0, 1.1], vertical_alignment="center")
                cols[0].write(nm)
                cols[1].checkbox(
                    "enabled",
                    value=bool(row.get("enabled", True)),
                    key=f"cron_enabled_{rk}",
                    label_visibility="collapsed",
                )
                cols[2].code(str(row.get("cron") or ""), language=None)
                # Task / priority columns reflect the *effective* values the
                # scheduler will use — annotate fallbacks with " (stem)" /
                # " (default)" so the operator can tell where the value came
                # from without opening the YAML.
                task_type = str(row.get("task") or "").strip()
                task_explicit = bool(row.get("task_explicit"))
                task_label = task_type if task_explicit else f"{task_type} (stem)"
                cols[3].code(task_label, language=None)
                priority_value = resolve_cron_priority(row.get("priority"))
                priority_explicit = bool(row.get("priority_explicit"))
                priority_label = (
                    str(priority_value)
                    if priority_explicit
                    else f"{priority_value} (default)"
                )
                cols[4].write(priority_label)
                cols[5].write(str(row.get("file") or ""))

                if cols[6].button("Push", key=f"cron_push_{rk}", width="stretch"):
                    if not task_type:
                        st.error(f"`{nm}` has no resolvable `task` or file stem.")
                    else:
                        n = _push_cron_task_now(
                            task_type=task_type, priority=priority_value, name=nm
                        )
                        st.success(
                            f"Enqueued **{n}** queue item(s) for `{task_type}` "
                            f"at priority `{priority_value}`."
                        )

            b = st.columns([1, 1, 4])
            with b[0]:
                save_cron = st.button("Save YAML", type="primary", key="cron_save_yaml")
            with b[1]:
                st.caption(" ")

            if save_cron:
                changed = False
                disabled_tasks: set[str] = set()
                for row in cron_rows:
                    nm = str(row.get("name") or "")
                    rk = _slug(nm)
                    want = bool(st.session_state.get(f"cron_enabled_{rk}", True))
                    was = bool(row.get("enabled", True))
                    if want != was:
                        _set_scenario_enabled(cron_path_by_id[nm], want)
                        changed = True
                        if not want:
                            task_type = str(row.get("task") or "").strip()
                            if task_type:
                                disabled_tasks.add(task_type)
                if changed:
                    msg = "Updated `enabled` in cron YAML file(s)."
                    if disabled_tasks:
                        msg += " " + _purge_disabled_in_redis(client, settings, disabled_tasks)
                    st.success(msg)
                else:
                    st.info("No changes to save.")
                st.rerun()

            with st.expander("Raw YAML (cron)", expanded=False):
                picks = [cid for _, cid, _, _ in cron_meta_filtered]
                pick = st.selectbox("Cron spec", picks, key="cron_yaml_pick")
                if pick and pick in cron_path_by_id:
                    st.code(cron_path_by_id[pick].read_text(), language="yaml")

with tab_assign:
    all_players = _all_player_ids(settings)
    if not all_players:
        st.info("No players in **db/devices.yaml** for configured instances.")
        st.stop()

    pid = st.selectbox("Player ID", all_players)

    loaded_ids = ["(all scenarios — clear Redis override)"] + [m[2] for m in scenario_meta]
    current = get_player_scenario(client, pid)
    idx = 0
    if current and current in loaded_ids:
        idx = loaded_ids.index(current)

    choice = st.selectbox("Active scenario", loaded_ids, index=idx)

    if st.button("Apply"):
        if choice.startswith("(all"):
            set_player_scenario(client, pid, None)
            st.success("Cleared — scheduler uses all YAML scenarios.")
        else:
            set_player_scenario(client, pid, choice)
            st.success(f"Set `wos:player:{pid}:scenario` → {choice}")

    st.subheader("Current overrides")
    rows_nt: list[dict[str, Any]] = []
    for p in all_players:
        sc = get_player_scenario(client, p)
        rows_nt.append(
            {
                "id": f"scenarios_override_{p}",
                "player_id": p,
                "scenario_redis": sc or "(none)",
            }
        )

    nested_table(
        rows_nt,
        [
            table_column("player_id", "player_id", width=200),
            table_column("scenario_redis", "scenario_redis", width=480),
        ],
        height=min(48 + max(len(rows_nt), 1) * 34, 420),
        striped=True,
        compact=True,
        hide_expand=True,
        key="scenarios_player_overrides_nt",
    )
