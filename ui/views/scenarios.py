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

settings = load_settings()
client = require_redis_connection()

scenarios_dir = Path(__file__).resolve().parents[2] / "scenarios"
files = sorted(scenarios_dir.glob("*.yaml"))

if not files:
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

if not scenario_meta:
    st.warning("No valid scenario YAML files.")
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

st.subheader("Scenario files")

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
    use_container_width=True,
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

st.divider()
st.subheader("Assign scenario to player")

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

st.dataframe(rows, hide_index=True, use_container_width=True)
