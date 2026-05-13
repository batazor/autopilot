"""Scenario YAML listing and per-player assignment in Redis."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

import streamlit as st
import yaml

from config.devices import player_ids_for_device_candidates
from config.loader import Settings, load_settings
from config.reference_naming import event_icon_abs_path
from scenarios.cron_specs import (
    iter_cron_yaml_files,
    iter_plain_scenario_yaml_files,
    resolve_cron_priority,
    resolve_cron_task_type,
)
from ui.redis_client import get_player_scenario, require_redis_connection, set_player_scenario


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


def _rows_to_records(edited: object, fallback: list[dict]) -> list[dict]:
    if edited is None:
        return fallback
    if hasattr(edited, "to_dict"):
        return edited.to_dict(orient="records")  # type: ignore[no-any-return]
    return list(edited)


def _list_scenario_yaml_files(scenarios_dir: Path) -> list[Path]:
    """DSL scenario YAMLs for the main tab (skips ``drafts/`` and root ``cron`` schedules)."""
    return iter_plain_scenario_yaml_files(scenarios_dir)


def _scenario_rel_top_folder(rel: str) -> str:
    """First path segment under ``scenarios/``, or ``(root)`` for YAML at repo root."""
    parts = Path(rel).parts
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


def _build_folder_tree_from_meta(
    scenario_meta: list[tuple[Path, str, str, str, dict]],
) -> _FolderNode:
    """Group display rows by relative path under ``scenarios/`` (folder nesting)."""
    root = _FolderNode()
    for _path, rel, sid, name, raw in scenario_meta:
        steps = raw.get("steps")
        n_steps = len(steps) if isinstance(steps, list) else 0
        row = {
            "id": sid,
            "name": name,
            "edit": _edit_scenario_link_url(sid),
            "debug": _debug_scenario_link_url(sid),
            "enabled": bool(raw.get("enabled", False)),
            "steps": n_steps,
        }
        parts = Path(rel).parts
        node = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node.files.append(row)
            else:
                node.subfolders.setdefault(part, _FolderNode())
                node = node.subfolders[part]
    return root


def _editor_key(parts: tuple[str, ...]) -> str:
    if not parts:
        return "scenarios_editor__root"
    safe = "__".join(re.sub(r"[^a-zA-Z0-9]+", "_", p).strip("_") for p in parts)
    return f"scenarios_editor__{safe}"


def _render_scenario_folder_tree(
    node: _FolderNode,
    parts: tuple[str, ...],
    *,
    merged_edits: list[dict],
    column_config: dict,
    disabled: tuple[str, ...],
) -> None:
    key = _editor_key(parts)
    if node.files:
        rows = sorted(
            node.files,
            key=lambda r: (str(r.get("id") or ""), str(r.get("name") or "")),
        )
        edited = st.data_editor(
            rows,
            column_config=column_config,
            disabled=disabled,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            key=key,
        )
        merged_edits.extend(_rows_to_records(edited, rows))
    for name in sorted(node.subfolders.keys()):
        with st.expander(f"{name}/", expanded=False):
            _render_scenario_folder_tree(
                node.subfolders[name],
                parts + (name,),
                merged_edits=merged_edits,
                column_config=column_config,
                disabled=disabled,
            )


st.title("Scenarios")

_nav = st.columns([1, 1, 5])
with _nav[0]:
    st.page_link(
        "views/fsm.py",
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

scenarios_dir = Path(__file__).resolve().parents[2] / "scenarios"
files = _list_scenario_yaml_files(scenarios_dir)
cron_files = iter_cron_yaml_files(scenarios_dir)

if not files and not cron_files:
    st.warning(f"No scenario YAML under {scenarios_dir} (excluding drafts/)")
    st.stop()

scenario_meta: list[tuple[Path, str, str, str, dict]] = []
for path in files:
    rel = path.relative_to(scenarios_dir).as_posix()
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
            st.error(f"{path.relative_to(scenarios_dir)}: expected YAML mapping")
            continue
        # Cron specs are identified by `name` (human), fallback to filename.
        name = str(raw.get("name", path.stem)).strip() or path.stem
        cid = name  # show as id in UI; scheduler normalizes internally
        cron_meta.append((path, cid, name, raw))
    except (yaml.YAMLError, OSError) as exc:
        st.error(f"{path.relative_to(scenarios_dir)}: {exc}")

if not scenario_meta and not cron_meta:
    st.warning("No valid YAML files.")
    st.stop()

_folder_counts: Counter[str] = Counter()
for _path, rel, *_rest in scenario_meta:
    _folder_counts[_scenario_rel_top_folder(rel)] += 1
for path, *_rest in cron_meta:
    rel_c = path.relative_to(scenarios_dir).as_posix()
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
    st.caption("Folder (under scenarios/)")
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
table_rows: list[dict] = []
_repo_root = scenarios_dir.parent
for path, rel, sid, name, raw in scenario_meta:
    path_by_file[rel] = path
    path_by_id[sid] = path
    steps = raw.get("steps")
    n_steps = len(steps) if isinstance(steps, list) else 0
    icon_path = event_icon_abs_path(_repo_root, str(raw.get("icon") or ""))
    table_rows.append(
        {
            "id": sid,
            "icon": str(icon_path) if icon_path is not None else None,
            "name": name,
            "enabled": bool(raw.get("enabled", False)),
            "steps": n_steps,
        }
    )

tab_files, tab_cron, tab_assign = st.tabs(["Scenario files", "Cron jobs", "Player assignment"])

with tab_files:
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

    st.caption("Files are grouped by subfolders under `scenarios/` — expand a folder to edit scenarios inside it.")

    _scenario_column_config = {
        "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
        "icon": st.column_config.ImageColumn("Icon", help="Event icon from scenario `icon:` slug", width="small"),
        "name": st.column_config.TextColumn("Name", disabled=True),
        "edit": st.column_config.LinkColumn(
            "Edit",
            display_text="Open",
            help="Open this scenario in the structured editor",
            width="small",
        ),
        "debug": st.column_config.LinkColumn(
            "Debug",
            display_text="Run",
            help="Open this scenario in the debug runner",
            width="small",
        ),
        "enabled": st.column_config.CheckboxColumn("Enabled", default=False),
        "steps": st.column_config.NumberColumn("Steps", disabled=True, format="%d", width="small"),
    }
    _scenario_disabled = ("id", "icon", "name", "edit", "debug", "steps")

    merged_edits: list[dict] = []
    if not scenario_meta_filtered:
        st.warning("No scenarios match the filter.")
    else:
        tree = _build_folder_tree_from_meta(scenario_meta_filtered)
        _render_scenario_folder_tree(
            tree,
            (),
            merged_edits=merged_edits,
            column_config=_scenario_column_config,
            disabled=_scenario_disabled,
        )

    btns = st.columns([1, 5])
    with btns[0]:
        save_yaml = st.button("Save YAML", type="primary", key="scenarios_save_yaml")

    if save_yaml:
        orig_by_id = {str(r["id"]): r for r in table_rows}
        records = merged_edits
        changed = False
        for row in records:
            sid_key = str(row.get("id") or "")
            if sid_key not in orig_by_id or sid_key not in path_by_id:
                continue
            want = bool(row.get("enabled", False))
            was = bool(orig_by_id[sid_key]["enabled"])
            if want != was:
                _set_scenario_enabled(path_by_id[sid_key], want)
                changed = True
        if changed:
            st.success("Updated `enabled` in scenario YAML file(s).")
        else:
            st.info("No changes to save.")
        st.rerun()

    with st.expander("Raw YAML", expanded=False):
        rels = [rel for _, rel, _, _, _ in scenario_meta]
        pick = st.selectbox("Scenario file", rels, key="scenarios_yaml_pick")
        if pick and pick in path_by_file:
            st.code(path_by_file[pick].read_text(), language="yaml")

with tab_cron:
    if not cron_meta:
        st.info("No YAML files with a root `cron` field under `scenarios/`.")
    else:
        import json
        import re
        import time

        cron_meta_filtered = [
            (path, cid, name, raw)
            for path, cid, name, raw in cron_meta
            if (
                not _selected_folders
                or _scenario_rel_top_folder(path.relative_to(scenarios_dir).as_posix())
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
                    "file": path.relative_to(scenarios_dir).as_posix(),
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
                for row in cron_rows:
                    nm = str(row.get("name") or "")
                    rk = _slug(nm)
                    want = bool(st.session_state.get(f"cron_enabled_{rk}", True))
                    was = bool(row.get("enabled", True))
                    if want != was:
                        _set_scenario_enabled(cron_path_by_id[nm], want)
                        changed = True
                if changed:
                    st.success("Updated `enabled` in cron YAML file(s).")
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
    rows: list[dict[str, str]] = []
    for p in all_players:
        sc = get_player_scenario(client, p)
        rows.append({"player_id": p, "scenario_redis": sc or "(none)"})

    st.dataframe(rows, hide_index=True, width="stretch")
