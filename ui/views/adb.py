"""ADB page: device list, ADB binary settings, plus editors for
``config/settings.yaml`` (redis/ocr/instances) and ``db/devices.yaml``
(players per device).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from capture.adb_screencap import DEFAULT_ADB_BIN, resolve_adb_executable
from config.devices import invalidate_device_registry
from ui.settings_state import (
    ensure_ui_settings_session_defaults,
    get_ui_adb_bin,
)

ensure_ui_settings_session_defaults()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS_PATH = _REPO_ROOT / "config" / "settings.yaml"
_DEVICES_PATH = _REPO_ROOT / "db" / "devices.yaml"


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        f.write(content)
        tmp = f.name
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# adb devices parsing
# ---------------------------------------------------------------------------


def _parse_adb_devices(output: str) -> list[dict[str, str]]:
    """Parse ``adb devices -l`` into rows. Skips header and empty lines."""
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split(maxsplit=1)
        serial = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        # Tokens after serial: "device product:foo model:bar device:baz transport_id:1"
        tokens = rest.split()
        state = tokens[0] if tokens else ""
        attrs: dict[str, str] = {}
        for tok in tokens[1:]:
            if ":" in tok:
                k, v = tok.split(":", 1)
                attrs[k] = v
        rows.append(
            {
                "serial": serial,
                "state": state,
                "model": attrs.get("model", ""),
                "product": attrs.get("product", ""),
                "device": attrs.get("device", ""),
                "transport_id": attrs.get("transport_id", ""),
            }
        )
    return rows


def _run_adb(args: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Run ADB with the resolved binary; returns (rc, stdout, stderr) or (-1, "", err)."""
    resolved = resolve_adb_executable(get_ui_adb_bin())
    if resolved is None:
        return -1, "", f"adb binary not found: `{get_ui_adb_bin()}`"
    try:
        proc = subprocess.run(
            [resolved, *args], capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"`adb {' '.join(args)}` timed out after {timeout:.0f}s"
    except FileNotFoundError:
        return -1, "", f"could not execute `{resolved}`"
    return (
        proc.returncode,
        proc.stdout.decode(errors="replace").strip(),
        proc.stderr.decode(errors="replace").strip(),
    )


def _device_names_in_yaml(devices_path: Path) -> set[str]:
    raw = _load_yaml(devices_path)
    out: set[str] = set()
    for d in raw.get("devices", []) or []:
        if isinstance(d, dict):
            name = str(d.get("name", "") or "").strip()
            if name:
                out.add(name)
    return out


def _append_device_stubs(devices_path: Path, serials: list[str]) -> tuple[int, list[str]]:
    """Append stub device entries (empty gamer lists). Returns (added_count, skipped_serials)."""
    raw = _load_yaml(devices_path)
    devices = [d for d in (raw.get("devices", []) or []) if isinstance(d, dict)]
    existing = {str(d.get("name", "") or "").strip() for d in devices}
    skipped: list[str] = []
    added = 0
    stub_profile: dict[str, Any] = {"email": "", "gamer": []}
    for serial in serials:
        s = (serial or "").strip()
        if not s:
            continue
        if s in existing:
            skipped.append(s)
            continue
        devices.append({"name": s, "profiles": [dict(stub_profile)]})
        existing.add(s)
        added += 1
    if added == 0:
        return 0, skipped
    raw["devices"] = devices
    _atomic_write_yaml(devices_path, raw)
    invalidate_device_registry()
    return added, skipped


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("ADB")

# --- ADB binary --------------------------------------------------------------

st.subheader("ADB binary")

col1, col2 = st.columns([3, 2])
with col1:
    st.text_input(
        "ADB binary",
        key="wos_settings_adb_bin",
        help="Homebrew (Apple Silicon): /opt/homebrew/bin/adb. Otherwise use `which adb`.",
    )
with col2:
    st.text_input(
        "Default serial (optional)",
        key="wos_settings_adb_serial",
        placeholder="emulator-5554",
        help="Used by labeling/annotator when several devices are attached.",
    )

resolved_bin = resolve_adb_executable(get_ui_adb_bin())
if resolved_bin:
    st.caption(f"Resolved: `{resolved_bin}`")
else:
    st.error(
        f"Binary not found: `{get_ui_adb_bin()}`. "
        "Try `/opt/homebrew/bin/adb` or `~/Library/Android/sdk/platform-tools/adb`."
    )

with st.expander("Default candidate"):
    st.code(DEFAULT_ADB_BIN, language="text")


# --- Connected devices -------------------------------------------------------

st.divider()
st.subheader("Connected devices")

if "adb_devices_yaml_toast" in st.session_state:
    st.success(st.session_state.pop("adb_devices_yaml_toast"))

c_refresh, c_start, c_kill = st.columns([1, 1, 1])
with c_refresh:
    refresh_clicked = st.button("Refresh", type="primary", width="stretch")
with c_start:
    start_clicked = st.button(
        "adb start-server",
        width="stretch",
        help="Starts the local ADB server if it is not running.",
    )
with c_kill:
    kill_clicked = st.button(
        "adb kill-server",
        width="stretch",
        help="Stops the local ADB server. Useful when devices appear stuck/offline.",
    )

if start_clicked:
    rc, out, err = _run_adb(["start-server"], timeout=10.0)
    if rc == 0:
        st.success("ADB server started.")
    else:
        st.error(err or out or f"start-server exit {rc}")

if kill_clicked:
    rc, out, err = _run_adb(["kill-server"], timeout=10.0)
    if rc == 0:
        st.success("ADB server killed.")
    else:
        st.error(err or out or f"kill-server exit {rc}")

# Always render the device list (refresh button just forces a rerun)
_ = refresh_clicked
rc, out, err = _run_adb(["devices", "-l"], timeout=8.0)
if rc != 0:
    st.error(err or f"adb devices -l exit {rc}")
else:
    rows = _parse_adb_devices(out)
    if not rows:
        st.warning(
            "No devices found. Start the emulator (BlueStacks/MuMu) or enable USB debugging."
        )
    else:
        st.success(f"{len(rows)} device(s) connected")
        yaml_names = _device_names_in_yaml(_DEVICES_PATH)
        adb_ready = [r for r in rows if r.get("state") == "device"]
        missing = [r for r in adb_ready if r["serial"] not in yaml_names]

        rows_display = [
            {
                **r,
                "in_devices_yaml": "yes" if r["serial"] in yaml_names else "no",
            }
            for r in rows
        ]
        st.dataframe(
            pd.DataFrame(rows_display),
            width="stretch",
            hide_index=True,
        )

        with st.expander(
            "Add ADB devices to db/devices.yaml",
            expanded=bool(missing),
        ):
            st.caption(
                "Only serials in **device** state can be added. "
                "Creates a stub with an empty `gamer` list; set player IDs in the table below."
            )

            if not adb_ready:
                st.info(
                    "No devices in `device` state. "
                    "Authorize USB debugging or wait until the emulator is fully booted."
                )
            elif not missing:
                st.info(
                    "Every connected ADB serial in `device` state already has a `name` "
                    f"in `{_DEVICES_PATH.relative_to(_REPO_ROOT)}`."
                )
            else:
                labels = {
                    r["serial"]: (
                        f"{r['serial']} — "
                        f"{(r.get('model') or r.get('product') or '').strip() or 'no model'}"
                    )
                    for r in missing
                }
                chosen = st.multiselect(
                    "Serials to add (not in devices.yaml)",
                    options=list(labels.keys()),
                    format_func=lambda sid: labels.get(sid, sid),
                    key="adb_register_devices_multiselect",
                )
                b_sel, b_all = st.columns(2)
                with b_sel:
                    if st.button(
                        "Add selected",
                        type="primary",
                        width="stretch",
                        key="adb_register_devices_selected",
                    ):
                        if not chosen:
                            st.warning("Select at least one serial.")
                        else:
                            n_added, _skipped = _append_device_stubs(_DEVICES_PATH, chosen)
                            st.session_state["adb_devices_yaml_toast"] = (
                                f"Added {n_added} stub device(s) to "
                                f"`{_DEVICES_PATH.relative_to(_REPO_ROOT)}`."
                            )
                            st.rerun()
                with b_all:
                    if st.button(
                        "Add all missing",
                        width="stretch",
                        key="adb_register_devices_all",
                    ):
                        serials = [r["serial"] for r in missing]
                        n_added, _skipped = _append_device_stubs(_DEVICES_PATH, serials)
                        st.session_state["adb_devices_yaml_toast"] = (
                            f"Added {n_added} stub device(s) to "
                            f"`{_DEVICES_PATH.relative_to(_REPO_ROOT)}`."
                        )
                        st.rerun()

        # Per-device quick test
        with st.expander("Per-device test (`get-state` + `getprop ro.product.model`)"):
            for r in rows:
                serial = r["serial"]
                cols = st.columns([2, 1, 4])
                cols[0].code(serial, language="text")
                if cols[1].button("Test", key=f"adb_test_{serial}"):
                    rc1, st_out, st_err = _run_adb(["-s", serial, "get-state"], timeout=5.0)
                    rc2, model_out, _ = _run_adb(
                        ["-s", serial, "shell", "getprop", "ro.product.model"], timeout=5.0
                    )
                    if rc1 == 0:
                        msg = f"state=`{st_out}`"
                        if rc2 == 0 and model_out:
                            msg += f"  model=`{model_out}`"
                        cols[2].success(msg)
                    else:
                        cols[2].error(st_err or f"exit {rc1}")
    with st.expander("Raw `adb devices -l` output"):
        st.code(out or "(no output)", language="text")


# --- config/settings.yaml ----------------------------------------------------

st.divider()
st.subheader("config/settings.yaml")

settings_raw = _load_yaml(_SETTINGS_PATH)
redis_block = settings_raw.get("redis", {}) or {}
ocr_block = settings_raw.get("ocr", {}) or {}
instances_block = settings_raw.get("instances", []) or []

with st.form("settings_yaml_form", clear_on_submit=False):
    st.markdown("**Redis**")
    redis_url = st.text_input(
        "redis.url",
        value=str(redis_block.get("url", "redis://localhost:6379/0")),
        help="Redis connection string used by the worker and scheduler.",
    )

    st.markdown("**OCR**")
    ocr_url = st.text_input(
        "ocr.url",
        value=str(ocr_block.get("url", "http://localhost:8000")),
        help="OCR microservice base URL.",
    )

    st.markdown("**Instances**")
    st.caption(
        "One row per BlueStacks/MuMu emulator. "
        "`bluestacks_window_title` must match the ADB serial from the device list above."
    )
    inst_df = pd.DataFrame(
        [
            {
                "instance_id": str(inst.get("instance_id", "")),
                "bluestacks_window_title": str(inst.get("bluestacks_window_title", "")),
            }
            for inst in instances_block
        ]
    )
    if inst_df.empty:
        inst_df = pd.DataFrame(columns=["instance_id", "bluestacks_window_title"])
    edited_inst = st.data_editor(
        inst_df,
        num_rows="dynamic",
        width="stretch",
        key="settings_instances_editor",
        column_config={
            "instance_id": st.column_config.TextColumn(
                "instance_id", help="Short alias used internally (e.g. `bs1`)."
            ),
            "bluestacks_window_title": st.column_config.TextColumn(
                "adb_serial",
                help="ADB serial (`adb -s <serial>`). e.g. `emulator-5554`.",
            ),
        },
    )

    submitted_settings = st.form_submit_button("Save settings.yaml", type="primary")
    if submitted_settings:
        new_doc = dict(settings_raw)  # preserves all unrelated keys (tasks, scheduler, worker)
        new_redis = dict(redis_block)
        new_redis["url"] = redis_url.strip()
        new_doc["redis"] = new_redis

        new_ocr = dict(ocr_block)
        new_ocr["url"] = ocr_url.strip()
        new_doc["ocr"] = new_ocr

        # Build instances preserving any extra fields (capture_window_title etc.)
        existing_by_id = {
            str(inst.get("instance_id", "")): inst for inst in instances_block
        }
        new_instances: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        errors: list[str] = []
        for _, row in edited_inst.iterrows():
            iid = str(row.get("instance_id", "") or "").strip()
            serial = str(row.get("bluestacks_window_title", "") or "").strip()
            if not iid and not serial:
                continue  # skip empty rows
            if not iid:
                errors.append(f"Row with serial `{serial}` is missing `instance_id`.")
                continue
            if not serial:
                errors.append(f"Instance `{iid}` is missing `adb_serial`.")
                continue
            if iid in seen_ids:
                errors.append(f"Duplicate instance_id `{iid}`.")
                continue
            seen_ids.add(iid)
            base = dict(existing_by_id.get(iid, {}))
            base.pop("google_account", None)  # drop dead field on save
            base["instance_id"] = iid
            base["bluestacks_window_title"] = serial
            new_instances.append(base)

        if errors:
            for e in errors:
                st.error(e)
        else:
            new_doc["instances"] = new_instances
            try:
                _atomic_write_yaml(_SETTINGS_PATH, new_doc)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Failed to save: {exc}")
            else:
                st.success(
                    f"Saved {_SETTINGS_PATH.relative_to(_REPO_ROOT)} "
                    f"({len(new_instances)} instance(s)). Restart the bot to apply."
                )


# --- db/devices.yaml ---------------------------------------------------------

st.divider()
st.subheader("db/devices.yaml")
st.caption(
    "Players (gamer accounts) registered under each device. "
    "`device_name` should match an `adb_serial` from the instances above "
    "(or its `instance_id`, since both forms are accepted)."
)

devices_raw = _load_yaml(_DEVICES_PATH)
devices_block = devices_raw.get("devices", []) or []

# Flatten devices → rows
flat_rows: list[dict[str, Any]] = []
for d in devices_block:
    if not isinstance(d, dict):
        continue
    name = str(d.get("name", "") or "")
    profiles = d.get("profiles") or []
    if not profiles:
        flat_rows.append(
            {"device_name": name, "email": "", "player_id": "", "nickname": "", "level": 0}
        )
        continue
    for p in profiles:
        if not isinstance(p, dict):
            continue
        email = str(p.get("email", "") or "")
        gamers = p.get("gamer") or []
        if not gamers:
            flat_rows.append(
                {
                    "device_name": name,
                    "email": email,
                    "player_id": "",
                    "nickname": "",
                    "level": 0,
                }
            )
            continue
        for g in gamers:
            if not isinstance(g, dict):
                continue
            flat_rows.append(
                {
                    "device_name": name,
                    "email": email,
                    "player_id": str(g.get("id", "") or ""),
                    "nickname": str(g.get("nickname", "") or ""),
                    "level": int(g.get("level", 0) or 0),
                }
            )

devices_df = pd.DataFrame(
    flat_rows or [],
    columns=["device_name", "email", "player_id", "nickname", "level"],
)

with st.form("devices_yaml_form", clear_on_submit=False):
    edited_devs = st.data_editor(
        devices_df,
        num_rows="dynamic",
        width="stretch",
        key="devices_editor",
        column_config={
            "device_name": st.column_config.TextColumn(
                "device_name",
                help="ADB serial or instance_id; matches an instance from settings.yaml.",
            ),
            "email": st.column_config.TextColumn(
                "email", help="Google account that owns this slot (may be empty)."
            ),
            "player_id": st.column_config.TextColumn(
                "player_id", help="In-game player ID (numeric)."
            ),
            "nickname": st.column_config.TextColumn("nickname"),
            "level": st.column_config.NumberColumn(
                "level", min_value=0, step=1, help="Used by `player_level_min` scenarios."
            ),
        },
    )

    submitted_devices = st.form_submit_button("Save devices.yaml", type="primary")
    if submitted_devices:
        # Group: device_name → email → list[gamer]
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        device_order: list[str] = []
        errors: list[str] = []

        for _, row in edited_devs.iterrows():
            name = str(row.get("device_name", "") or "").strip()
            email = str(row.get("email", "") or "").strip()
            pid_raw = str(row.get("player_id", "") or "").strip()
            nick = str(row.get("nickname", "") or "").strip()
            level_raw = row.get("level", 0)

            if not name and not pid_raw:
                continue  # blank row
            if not name:
                errors.append(f"Row with player `{pid_raw}` is missing `device_name`.")
                continue

            if name not in grouped:
                device_order.append(name)

            # Empty profile / placeholder
            if not pid_raw:
                grouped[name].setdefault(email, [])
                continue

            if not re.fullmatch(r"\d+", pid_raw):
                errors.append(f"player_id `{pid_raw}` is not numeric (device `{name}`).")
                continue
            try:
                level = int(level_raw or 0)
            except (TypeError, ValueError):
                errors.append(f"level `{level_raw}` for player `{pid_raw}` is not an integer.")
                continue

            entry: dict[str, Any] = {"id": int(pid_raw), "nickname": nick}
            if level > 0:
                entry["level"] = level
            grouped[name][email].append(entry)

        if errors:
            for e in errors:
                st.error(e)
        else:
            new_devices: list[dict[str, Any]] = []
            for name in device_order:
                profiles = []
                for email, gamers in grouped[name].items():
                    profiles.append({"email": email, "gamer": gamers})
                new_devices.append({"name": name, "profiles": profiles})

            new_doc = dict(devices_raw)
            new_doc["devices"] = new_devices
            try:
                _atomic_write_yaml(_DEVICES_PATH, new_doc)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Failed to save: {exc}")
            else:
                invalidate_device_registry()
                total_players = sum(
                    len(g) for emails in grouped.values() for g in emails.values()
                )
                st.success(
                    f"Saved {_DEVICES_PATH.relative_to(_REPO_ROOT)} "
                    f"({len(new_devices)} device(s), {total_players} player(s))."
                )
