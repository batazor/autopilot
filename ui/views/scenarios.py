"""Scenario YAML listing and per-player assignment in Redis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

import streamlit as st
import yaml

from config.devices import player_ids_for_device
from config.loader import Settings, load_settings
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
        for pid in player_ids_for_device(inst.bluestacks_window_title):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    for pk in settings.players:
        if pk not in seen:
            seen.add(pk)
            out.append(pk)
    return out


def _rows_to_records(edited: object, fallback: list[dict]) -> list[dict]:
    if edited is None:
        return fallback
    if hasattr(edited, "to_dict"):
        return edited.to_dict(orient="records")  # type: ignore[no-any-return]
    return list(edited)


def _list_scenario_yaml_files(scenarios_dir: Path) -> list[Path]:
    """All runnable scenario YAMLs under ``scenarios/`` (recursive).

    Mirrors :meth:`scenarios.loader.ScenarioLoader.reload` — skips ``drafts/``
    and ``by_cron/`` (cron specs have their own tab).
    """
    if not scenarios_dir.is_dir():
        return []
    out: list[Path] = []
    for p in scenarios_dir.rglob("*.yaml"):
        if "drafts" in {x.lower() for x in p.parts}:
            continue
        rel = p.relative_to(scenarios_dir)
        if rel.parts and rel.parts[0].lower() == "by_cron":
            continue
        out.append(p)
    return sorted(out)


@dataclass
class _FolderNode:
    subfolders: dict[str, _FolderNode] = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)


def _wiki_story_link_url(repo_rel: str) -> str:
    """Full URL to Wiki · Scenarios with ``scenario=<repo-relative path>`` (``LinkColumn``).

    Path segment matches ``st.Page(..., \"views/wiki_scenarios.py\")`` → ``/wiki_scenarios``.
    """
    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    parts = [p for p in u.path.strip("/").split("/") if p]
    if parts:
        parts[-1] = "wiki_scenarios"
        wiki_path = "/" + "/".join(parts)
    else:
        wiki_path = "/wiki_scenarios"
    query = urlencode({"scenario": repo_rel})
    return urlunparse((u.scheme, u.netloc, wiki_path, "", query, ""))


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
            "wiki": _wiki_story_link_url(f"scenarios/{rel}"),
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

_nav = st.columns([1, 1, 6])
with _nav[0]:
    st.page_link(
        "views/fsm.py",
        label="Routes",
        help="Screen transition graph and tap routing between game screens.",
        width="stretch",
    )
with _nav[1]:
    st.page_link(
        "views/wiki_scenarios.py",
        label="Wiki · Scenarios",
        help="Browse scenarios as a readable story (steps, taps, regions).",
        width="stretch",
    )

settings = load_settings()
client = require_redis_connection()

scenarios_dir = Path(__file__).resolve().parents[2] / "scenarios"
files = _list_scenario_yaml_files(scenarios_dir)
cron_dir = scenarios_dir / "by_cron"
cron_files = sorted(cron_dir.glob("*.yaml")) if cron_dir.is_dir() else []

if not files and not cron_files:
    st.warning(f"No scenario YAML under {scenarios_dir} (excluding drafts/ and by_cron/)")
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

path_by_file: dict[str, Path] = {}
path_by_id: dict[str, Path] = {}
table_rows: list[dict] = []
for path, rel, sid, name, raw in scenario_meta:
    path_by_file[rel] = path
    path_by_id[sid] = path
    steps = raw.get("steps")
    n_steps = len(steps) if isinstance(steps, list) else 0
    table_rows.append(
        {
            "id": sid,
            "name": name,
            "enabled": bool(raw.get("enabled", False)),
            "steps": n_steps,
        }
    )

tab_files, tab_cron, tab_assign = st.tabs(["Scenario files", "Cron jobs", "Player assignment"])

with tab_files:
    st.caption("Files are grouped by subfolders under `scenarios/` — expand a folder to edit scenarios inside it.")

    _scenario_column_config = {
        "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
        "name": st.column_config.TextColumn("Name", disabled=True),
        "wiki": st.column_config.LinkColumn(
            "Wiki",
            display_text="Wiki",
            help="Readable story (steps, taps, regions)",
            width="small",
        ),
        "enabled": st.column_config.CheckboxColumn("Enabled", default=False),
        "steps": st.column_config.NumberColumn("Steps", disabled=True, format="%d", width="small"),
    }
    _scenario_disabled = ("id", "name", "wiki", "steps")

    merged_edits: list[dict] = []
    tree = _build_folder_tree_from_meta(scenario_meta)
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
        st.info("No cron YAML specs under `scenarios/by_cron/`.")
    else:
        import json
        import re
        import time

        cron_path_by_id: dict[str, Path] = {}
        cron_rows: list[dict] = []
        for path, cid, name, raw in cron_meta:
            cron_path_by_id[cid] = path
            cron_rows.append(
                {
                    "name": name,
                    "enabled": bool(raw.get("enabled", True)),
                    "cron": str(raw.get("cron", "")),
                    "task": str(raw.get("task", raw.get("task_type", ""))),
                    "priority": "" if raw.get("priority") is None else str(raw.get("priority")),
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
                for pid in player_ids_for_device(inst.bluestacks_window_title):
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
            cols[3].code(str(row.get("task") or ""), language=None)
            cols[4].write(str(row.get("priority") or ""))
            cols[5].write(str(row.get("file") or ""))

            task_type = str(row.get("task") or "").strip()
            try:
                pr = int(str(row.get("priority") or "1"))
            except ValueError:
                pr = 1
            if cols[6].button("Push", key=f"cron_push_{rk}", width="stretch"):
                if not task_type:
                    st.error(f"`{nm}` has empty `task`.")
                else:
                    n = _push_cron_task_now(task_type=task_type, priority=pr, name=nm)
                    st.success(f"Enqueued **{n}** queue item(s) for `{task_type}`.")

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
            picks = [cid for _, cid, _, _ in cron_meta]
            pick = st.selectbox("Cron spec", picks, key="cron_yaml_pick")
            if pick and pick in cron_path_by_id:
                st.code(cron_path_by_id[pick].read_text(), language="yaml")

with tab_assign:
    all_players = _all_player_ids(settings)
    if not all_players:
        st.info("No players in settings.yaml")
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
