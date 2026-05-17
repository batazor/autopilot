"""ADB page: device list, ADB binary settings, plus editors for
``src/config/settings.yaml`` (redis/ocr/worker) and ``db/devices.yaml``
(players per device).
"""
from __future__ import annotations

import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml

from adb.screencap import DEFAULT_ADB_BIN, resolve_adb_executable
from config.devices import invalidate_device_registry
from config.paths import repo_root
from ui.adb_query import (
    canonical_serial as _canonical_serial,
)
from ui.adb_query import (
    dedupe_emulator_aliases as _dedupe_emulator_aliases,
)
from ui.adb_query import (
    parse_adb_devices as _parse_adb_devices,
)
from ui.adb_query import (
    port_scan_connect as _port_scan_connect,
)
from ui.adb_query import (
    run_adb as _run_adb,
)
from ui.settings_state import (
    ensure_ui_settings_session_defaults,
    get_ui_adb_bin,
)

ensure_ui_settings_session_defaults()

_REPO_ROOT = repo_root()
_SETTINGS_PATH = _REPO_ROOT / "src" / "config" / "settings.yaml"
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
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _known_device_serials(devices_path: Path) -> set[str]:
    """Canonical serials already registered in ``db/devices.yaml``.

    Each device entry contributes both ``name`` (friendly alias) and
    ``adb_serial`` (raw serial) — canonicalised so ``emulator-N`` and
    ``127.0.0.1:<N+1>`` collapse and a refresh doesn't add a network alias
    when the SDK-style alias is already on file (or vice versa).
    """
    known: set[str] = set()
    devices_raw = _load_yaml(devices_path)
    for d in devices_raw.get("devices", []) or []:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name", "") or "").strip()
        adb_serial = str(d.get("adb_serial", "") or "").strip()
        for s in (name, adb_serial):
            if s:
                known.add(_canonical_serial(s))
    return known


def _attach_or_register_serials(
    devices_path: Path, serials: list[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Pair live ADB serials with existing entries, then add the rest.

    Returns ``(attached, added)`` where each list contains ``(device_name, serial)``.

    Pass 1 — fill in ``adb_serial`` on existing entries that are missing it
    (e.g. user renamed ``name: 127.0.0.1:5615`` → ``bs2`` and the link to the
    serial was lost). Pairing is greedy in declaration order: the i-th
    unattached serial goes to the i-th entry with empty ``adb_serial``.

    Pass 2 — anything still unattached becomes a fresh ``bs<N>`` entry.
    """
    raw = _load_yaml(devices_path)
    devices = [d for d in (raw.get("devices", []) or []) if isinstance(d, dict)]

    claimed: set[str] = set()
    for d in devices:
        for f in ("name", "adb_serial"):
            v = str(d.get(f, "") or "").strip()
            if v:
                claimed.add(_canonical_serial(v))

    seen_canon: set[str] = set()
    unattached_serials: list[str] = []
    for s in serials:
        s_str = (s or "").strip()
        if not s_str:
            continue
        cs = _canonical_serial(s_str)
        if cs in claimed or cs in seen_canon:
            continue
        seen_canon.add(cs)
        unattached_serials.append(s_str)

    incomplete_idx: list[int] = []
    for i, d in enumerate(devices):
        if str(d.get("adb_serial", "") or "").strip():
            continue
        if not str(d.get("name", "") or "").strip():
            continue
        incomplete_idx.append(i)

    attached: list[tuple[str, str]] = []
    n_pair = min(len(unattached_serials), len(incomplete_idx))
    for i in range(n_pair):
        idx = incomplete_idx[i]
        serial = unattached_serials[i]
        entry = devices[idx]
        # Rebuild dict so YAML serializes ``name`` → ``adb_serial`` → rest.
        new_entry: dict[str, Any] = {"name": entry["name"], "adb_serial": serial}
        for k, v in entry.items():
            if k not in ("name", "adb_serial"):
                new_entry[k] = v
        devices[idx] = new_entry
        attached.append((str(new_entry["name"]), serial))

    remaining = unattached_serials[n_pair:]
    existing_names = {str(d.get("name", "") or "").strip() for d in devices}

    def _next_bs_name() -> str:
        n = 1
        while f"bs{n}" in existing_names:
            n += 1
        return f"bs{n}"

    added: list[tuple[str, str]] = []
    for s in remaining:
        new_name = _next_bs_name()
        existing_names.add(new_name)
        # Fresh dict / list per device — sharing them makes PyYAML emit
        # ``&id001`` / ``*id001`` aliases.
        devices.append(
            {"name": new_name, "adb_serial": s, "profiles": [{"email": "", "gamer": []}]}
        )
        added.append((new_name, s))

    if attached or added:
        raw["devices"] = devices
        _atomic_write_yaml(devices_path, raw)
        invalidate_device_registry()
    return attached, added


def _append_device_stubs(devices_path: Path, serials: list[str]) -> tuple[int, list[str]]:
    """Append stub device entries with a generated ``bs<N>`` name + ``adb_serial``.

    Returns ``(added_count, skipped_serials)``. Skips serials that match any
    existing ``name`` or ``adb_serial`` so a second call with the same scan
    output is a no-op. ``bs<N>`` is chosen as the smallest unused index.
    """
    raw = _load_yaml(devices_path)
    devices = [d for d in (raw.get("devices", []) or []) if isinstance(d, dict)]
    existing_names = {str(d.get("name", "") or "").strip() for d in devices}
    existing_serials = {str(d.get("adb_serial", "") or "").strip() for d in devices}

    def _next_bs_name() -> str:
        n = 1
        while f"bs{n}" in existing_names:
            n += 1
        return f"bs{n}"

    skipped: list[str] = []
    added = 0
    for serial in serials:
        s = (serial or "").strip()
        if not s:
            continue
        # Either form already configured? skip.
        if s in existing_names or s in existing_serials:
            skipped.append(s)
            continue
        new_name = _next_bs_name()
        existing_names.add(new_name)
        existing_serials.add(s)
        # Fresh dict / list per device — sharing them makes PyYAML emit
        # ``&id001`` / ``*id001`` aliases, and writes to one device's gamer
        # list would silently leak into every other device.
        devices.append(
            {"name": new_name, "adb_serial": s, "profiles": [{"email": "", "gamer": []}]}
        )
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
    refresh_clicked = st.button(
        "Refresh",
        type="primary",
        width="stretch",
        help=(
            "Scans the configured port range with `adb connect 127.0.0.1:<port>` "
            "(picks up emulators that ADB doesn't know yet), then re-lists devices."
        ),
    )
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

# Port range used by Refresh: ``adb connect`` is run across these so emulators
# (BlueStacks / MuMu / LDPlayer / SDK) that ADB doesn't know yet get picked up
# without manual `adb connect`.
if "adb_detect_toast" in st.session_state:
    st.info(st.session_state.pop("adb_detect_toast"))

st.session_state.setdefault("adb_detect_start", 5555)
st.session_state.setdefault("adb_detect_end", 5700)
with st.expander("Refresh: port-scan range", expanded=False):
    d1, d2 = st.columns(2)
    with d1:
        st.number_input(
            "Start port",
            min_value=1,
            max_value=65535,
            step=1,
            key="adb_detect_start",
        )
    with d2:
        st.number_input(
            "End port",
            min_value=1,
            max_value=65535,
            step=1,
            key="adb_detect_end",
        )

if refresh_clicked:
    detect_start = int(st.session_state["adb_detect_start"])
    detect_end = int(st.session_state["adb_detect_end"])
    if detect_end < detect_start:
        st.error("End port must be >= start port.")
    else:
        newly, already = _port_scan_connect(detect_start, detect_end)
        # Sync ``db/devices.yaml`` with what's actually on adb. Run
        # ``adb devices -l`` immediately (instead of waiting for the post-rerun
        # render) so attach + append happens in the same click.
        attached_pairs: list[tuple[str, str]] = []
        added_pairs: list[tuple[str, str]] = []
        rc_dev, out_dev, _err_dev = _run_adb(["devices", "-l"], timeout=8.0)
        if rc_dev == 0:
            ready_rows = _dedupe_emulator_aliases(_parse_adb_devices(out_dev))
            ready_serials = [
                r["serial"] for r in ready_rows if r.get("state") == "device"
            ]
            attached_pairs, added_pairs = _attach_or_register_serials(
                _DEVICES_PATH, ready_serials
            )

        if newly:
            msg = "Connected: " + ", ".join(str(p) for p in newly)
            if already:
                msg += f" (already: {len(already)})"
        elif already:
            msg = f"All {len(already)} known port(s) still connected."
        else:
            msg = "Scan complete."
        if attached_pairs:
            pairs_s = ", ".join(f"{n}={s}" for n, s in attached_pairs)
            msg += f". Attached adb_serial: {pairs_s}"
        if added_pairs:
            pairs_s = ", ".join(f"{n}={s}" for n, s in added_pairs)
            msg += f". Added: {pairs_s}"
        st.session_state["adb_detect_toast"] = msg
        st.rerun()

# Always render the device list. (Refresh handler above runs the port scan and
# st.rerun's; this `adb devices -l` is the post-scan listing.)
rc, out, err = _run_adb(["devices", "-l"], timeout=8.0)
if rc != 0:
    st.error(err or f"adb devices -l exit {rc}")
else:
    rows = _dedupe_emulator_aliases(_parse_adb_devices(out))
    if not rows:
        st.warning(
            "No devices found. Start the emulator (BlueStacks/MuMu) or enable USB debugging."
        )
    else:
        st.success(f"{len(rows)} device(s) connected")
        # Canonical-aware membership — recognise ``emulator-N`` ↔
        # ``127.0.0.1:<N+1>`` plus the ``adb_serial`` field on each device.
        known_canonical = _known_device_serials(_DEVICES_PATH)
        adb_ready = [r for r in rows if r.get("state") == "device"]
        missing = [
            r for r in adb_ready
            if _canonical_serial(r["serial"]) not in known_canonical
        ]

        rows_display = [
            {
                **r,
                "in_devices_yaml": (
                    "yes" if _canonical_serial(r["serial"]) in known_canonical else "no"
                ),
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

st.caption(
    "Instances are derived from **`db/devices.yaml`** below — each device entry "
    "is one instance (`instance_id` = `name`, ADB serial = `adb_serial` or "
    "fall back to `name`)."
)

with st.form("settings_yaml_form", clear_on_submit=False):
    st.markdown("**Redis**")
    redis_url = st.text_input(
        "redis.url",
        value=str(redis_block.get("url", "redis://localhost:6379/0")),
        help="Redis connection string used by the worker and scheduler.",
    )

    st.markdown("**OCR**")
    ocr_lang = st.text_input(
        "ocr.lang",
        value=str(ocr_block.get("lang", "eng")),
        help="Tesseract language code. Use `eng` for eng.traineddata.",
    )
    tesseract_cmd = st.text_input(
        "ocr.tesseract_cmd",
        value=str(ocr_block.get("tesseract_cmd", "tesseract")),
        help="Path/name of the local tesseract executable.",
    )
    tessdata_dir = st.text_input(
        "ocr.tessdata_dir",
        value=str(ocr_block.get("tessdata_dir", "")),
        help="Optional tessdata directory containing eng.traineddata.",
    )

    submitted_settings = st.form_submit_button("Save settings.yaml", type="primary")
    if submitted_settings:
        new_doc = dict(settings_raw)  # preserves all unrelated keys (tasks, scheduler, worker)
        new_redis = dict(redis_block)
        new_redis["url"] = redis_url.strip()
        new_doc["redis"] = new_redis

        new_ocr = dict(ocr_block)
        new_ocr.pop("url", None)
        new_ocr["lang"] = ocr_lang.strip() or "eng"
        new_ocr["tesseract_cmd"] = tesseract_cmd.strip() or "tesseract"
        new_ocr["tessdata_dir"] = tessdata_dir.strip()
        new_doc["ocr"] = new_ocr

        try:
            _atomic_write_yaml(_SETTINGS_PATH, new_doc)
        except Exception as exc:
            st.error(f"Failed to save: {exc}")
        else:
            st.success(
                f"Saved {_SETTINGS_PATH.relative_to(_REPO_ROOT)}. "
                "Restart the bot to apply."
            )


# --- db/devices.yaml ---------------------------------------------------------

st.divider()
st.subheader("db/devices.yaml")
st.caption(
    "Source of truth for instances and players. "
    "`device_name` is the friendly alias / instance_id (`bs1`, `bs2`). "
    "`adb_serial` is the raw ADB serial (`adb -s <serial>`); leave empty to "
    "reuse `device_name` as the serial."
)

devices_raw = _load_yaml(_DEVICES_PATH)
devices_block = devices_raw.get("devices", []) or []

# Flatten devices → rows. ``adb_serial`` is a device-level field, but the editor
# is flat (one row per gamer slot), so it's repeated on every row for the same
# device. On save we collapse: each ``device_name`` keeps the ``adb_serial`` from
# its first row.
flat_rows: list[dict[str, Any]] = []
for d in devices_block:
    if not isinstance(d, dict):
        continue
    name = str(d.get("name", "") or "")
    adb_serial = str(d.get("adb_serial", "") or "")
    profiles = d.get("profiles") or []
    if not profiles:
        flat_rows.append(
            {
                "device_name": name,
                "adb_serial": adb_serial,
                "email": "",
                "player_id": "",
                "nickname": "",
                "level": 0,
            }
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
                    "adb_serial": adb_serial,
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
                    "adb_serial": adb_serial,
                    "email": email,
                    "player_id": str(g.get("id", "") or ""),
                    "nickname": str(g.get("nickname", "") or ""),
                    "level": int(g.get("level", 0) or 0),
                }
            )

devices_df = pd.DataFrame(
    flat_rows or [],
    columns=["device_name", "adb_serial", "email", "player_id", "nickname", "level"],
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
                help="Friendly alias / instance_id (e.g. `bs1`). Used in UI and logs.",
            ),
            "adb_serial": st.column_config.TextColumn(
                "adb_serial",
                help="Raw ADB serial (`adb -s …`). Leave empty to reuse `device_name`.",
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
        device_serial: dict[str, str] = {}
        errors: list[str] = []

        for _, row in edited_devs.iterrows():
            name = str(row.get("device_name", "") or "").strip()
            adb_serial = str(row.get("adb_serial", "") or "").strip()
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
            # adb_serial is device-level; keep the first non-empty value seen.
            if adb_serial and not device_serial.get(name):
                device_serial[name] = adb_serial

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
                entry: dict[str, Any] = {"name": name}
                ser = device_serial.get(name, "").strip()
                if ser:
                    entry["adb_serial"] = ser
                entry["profiles"] = profiles
                new_devices.append(entry)

            new_doc = dict(devices_raw)
            new_doc["devices"] = new_devices
            try:
                _atomic_write_yaml(_DEVICES_PATH, new_doc)
            except Exception as exc:
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
