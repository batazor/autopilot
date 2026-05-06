"""Scenario YAML listing and per-player assignment in Redis."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import yaml

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
        for pid in inst.player_ids:
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


st.title("Scenarios")

st.page_link(
    "views/fsm.py",
    label="FSM",
    help="Open the screen transition graph (FSM).",
    width="content",
)

settings = load_settings()
client = require_redis_connection()

scenarios_dir = Path(__file__).resolve().parents[2] / "scenarios"
files = sorted(scenarios_dir.glob("*.yaml"))
cron_dir = scenarios_dir / "by_cron"
cron_files = sorted(cron_dir.glob("*.yaml")) if cron_dir.is_dir() else []

if not files and not cron_files:
    st.warning(f"No YAML files in {scenarios_dir}")
    st.stop()

scenario_meta: list[tuple[Path, str, str, dict]] = []
for path in files:
    try:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            st.error(f"{path.name}: expected YAML mapping")
            continue
        sid = str(raw.get("id", path.stem))
        name = str(raw.get("name", path.stem))
        scenario_meta.append((path, sid, name, raw))
    except (yaml.YAMLError, OSError) as exc:
        st.error(f"{path.name}: {exc}")

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

path_by_id: dict[str, Path] = {}
table_rows: list[dict] = []
for path, sid, name, raw in scenario_meta:
    path_by_id[sid] = path
    steps = raw.get("steps")
    n_steps = len(steps) if isinstance(steps, list) else 0
    pr = raw.get("priority")
    table_rows.append(
        {
            "id": sid,
            "name": name,
            "enabled": bool(raw.get("enabled", False)),
            "priority": "" if pr is None else str(pr),
            "steps": n_steps,
        }
    )

tab_files, tab_cron, tab_assign = st.tabs(["Scenario files", "Cron jobs", "Player assignment"])

with tab_files:
    edited = st.data_editor(
        table_rows,
        column_config={
            "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
            "name": st.column_config.TextColumn("Name", disabled=True),
            "enabled": st.column_config.CheckboxColumn("Enabled", default=False),
            "priority": st.column_config.TextColumn("Priority", disabled=True, width="small"),
            "steps": st.column_config.NumberColumn("Steps", disabled=True, format="%d", width="small"),
        },
        disabled=("id", "name", "priority", "steps"),
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        key="scenarios_table",
    )

    btns = st.columns([1, 5])
    with btns[0]:
        save_yaml = st.button("Save YAML", type="primary", key="scenarios_save_yaml")

    if save_yaml:
        orig_by_id = {r["id"]: r for r in table_rows}
        records = _rows_to_records(edited, table_rows)
        changed = False
        for row in records:
            sid = row["id"]
            want = bool(row.get("enabled", False))
            was = bool(orig_by_id[sid]["enabled"])
            if want != was:
                _set_scenario_enabled(path_by_id[sid], want)
                changed = True
        if changed:
            st.success("Updated `enabled` in scenario YAML file(s).")
        else:
            st.info("No changes to save.")
        st.rerun()

    with st.expander("Raw YAML", expanded=False):
        ids = [sid for _, sid, _, _ in scenario_meta]
        pick = st.selectbox("Scenario", ids, key="scenarios_yaml_pick")
        if pick and pick in path_by_id:
            st.code(path_by_id[pick].read_text(), language="yaml")

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
                for pid in inst.player_ids:
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
                    client.zadd("wos:queue", {payload: float(now)})
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
            if cols[6].button("Push", key=f"cron_push_{rk}", use_container_width=True):
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

    loaded_ids = ["(all scenarios — clear Redis override)"] + [m[1] for m in scenario_meta]
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
