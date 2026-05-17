"""Gift codes DB UI (module ``gift_codes``)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from modules.gift_codes.models import GiftCode, GiftCodeDB, RedeemStatus
from modules.gift_codes.redeemer import run_gift_code_redeemer
from modules.gift_codes.scraper import poll_once

from config.devices import DeviceRegistry, load_devices


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_codes(path: Path) -> tuple[GiftCodeDB, str | None]:
    if not path.is_file():
        return GiftCodeDB(), None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return GiftCodeDB.model_validate(raw), None
    except Exception as exc:
        return GiftCodeDB(), str(exc)


_YES_NO_STYLES: dict[str, tuple[str, str]] = {
    "yes": ("#163a2a", "#3fb950"),
    "no": ("#2d333b", "#adbac7"),
}

_STATUS_STYLES: dict[str, tuple[str, str]] = {
    RedeemStatus.PENDING.value: ("#3d3018", "#d4a72c"),
    RedeemStatus.SUCCESS.value: ("#163a2a", "#3fb950"),
    RedeemStatus.ALREADY_RECEIVED.value: ("#163a2a", "#3fb950"),
    RedeemStatus.CDK_EXPIRED.value: ("#2a2a2a", "#8b949e"),
    RedeemStatus.CDK_NOT_FOUND.value: ("#2a2a2a", "#8b949e"),
    RedeemStatus.STOVE_LEVEL_TOO_LOW.value: ("#3d1f24", "#f85149"),
    RedeemStatus.FAILED.value: ("#3d1f24", "#f85149"),
}

_ROW_EXPIRED_STYLE = "background-color: #2a2a2a; color: #6e7681"
_REDEEMED_STATUSES = frozenset(
    {RedeemStatus.SUCCESS.value, RedeemStatus.ALREADY_RECEIVED.value}
)

_DF_ROW_HEIGHT_PX = 34

_GIFT_CODES_BASE_COLS: tuple[str, ...] = (
    "code",
    "expires",
    "slot expired",
    "needs run",
    "API err",
    "API msg",
)


def _gift_codes_column_order(player_ids: list[str]) -> list[str]:
    return list(_GIFT_CODES_BASE_COLS) + [f"p:{pid}" for pid in player_ids]


def _status_token(cell: object) -> str:
    raw = str(cell or "").strip()
    if not raw or raw == "—":
        return ""
    return raw.split(" ", 1)[0].strip()


def _style_yes_no_cell(val: object) -> str:
    key = str(val or "").strip().lower()
    bg, fg = _YES_NO_STYLES.get(key, ("#2a2a2a", "#8b949e"))
    return f"background-color: {bg}; color: {fg}; font-weight: 600"


def _style_status_cell(val: object) -> str:
    token = _status_token(val)
    if not token:
        return "color: #8b949e"
    bg, fg = _STATUS_STYLES.get(token, ("#2a2a2a", "#8b949e"))
    return f"background-color: {bg}; color: {fg}; font-weight: 600"


def _style_gift_codes_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    styler = df.style
    for col in ("slot expired", "needs run"):
        if col in df.columns:
            styler = styler.map(_style_yes_no_cell, subset=[col])

    player_cols = [c for c in df.columns if str(c).startswith("p:")]
    if player_cols:
        styler = styler.map(_style_status_cell, subset=player_cols)

    if "slot expired" in df.columns:

        def _row_expired(row: pd.Series) -> list[str]:
            if str(row.get("slot expired", "")).lower() == "yes":
                return [_ROW_EXPIRED_STYLE] * len(row)
            return [""] * len(row)

        styler = styler.apply(_row_expired, axis=1)

    return styler


def _gift_codes_column_config(
    player_ids: list[str],
    registry: DeviceRegistry,
) -> dict[str, object]:
    cfg: dict[str, object] = {
        "code": st.column_config.TextColumn(
            "Code",
            width="medium",
            pinned=True,
            help="Promo id from DB / scraper.",
        ),
        "expires": st.column_config.TextColumn(
            "Expires", width="small", help="Calendar expiry (ISO date)."
        ),
        "slot expired": st.column_config.TextColumn(
            "Expired",
            width="small",
            alignment="center",
            help="Calendar expiry or API CDK_EXPIRED / CDK_NOT_FOUND",
        ),
        "needs run": st.column_config.TextColumn(
            "Needs run",
            width="small",
            alignment="center",
            help="Any player still PENDING (or retryable failure)",
        ),
        "API err": st.column_config.TextColumn(
            "API err", width="small", help="Last Century `err_code`."
        ),
        "API msg": st.column_config.TextColumn(
            "API msg",
            width="large",
            help="Last API `msg` (truncated in YAML if long).",
        ),
    }
    for pid in player_ids:
        gamer = registry.get_gamer(pid)
        nick = (gamer.nickname or "").strip() if gamer else ""
        label = f"{nick} · {pid}" if nick else pid
        cfg[f"p:{pid}"] = st.column_config.TextColumn(
            label,
            width="medium",
            alignment="center",
            help=f"Redeem status for player `{pid}`",
        )
    return cfg


def _build_row(
    code: GiftCode,
    player_ids: list[str],
    registry: DeviceRegistry,
) -> dict[str, object]:
    api_err = str(code.last_api_err_code) if code.last_api_err_code is not None else "—"
    row: dict[str, object] = {
        "code": code.name,
        "expires": code.expires.isoformat() if code.expires else "—",
        "slot expired": "yes" if code.is_effectively_expired() else "no",
        "needs run": "yes"
        if (
            not code.is_effectively_expired()
            and any(code.needs_redemption(pid) for pid in player_ids)
        )
        else "no",
        "API err": api_err,
        "API msg": code.last_api_msg or "—",
    }
    for pid in player_ids:
        status = code.user_for.get(pid, RedeemStatus.PENDING)
        gamer = registry.get_gamer(pid)
        nick = (gamer.nickname or "").strip() if gamer else ""
        suffix = f" · {nick}" if nick else ""
        row[f"p:{pid}"] = f"{status.value}{suffix}"
    return row


def _count_pending(rows: list[dict[str, object]], player_ids: list[str]) -> int:
    n = 0
    for row in rows:
        for pid in player_ids:
            if _status_token(row.get(f"p:{pid}")) == RedeemStatus.PENDING.value:
                n += 1
    return n


def _render_gift_codes_table(
    rows: list[dict[str, object]],
    *,
    player_ids: list[str],
    registry: DeviceRegistry,
    title: str,
) -> None:
    if not rows:
        return

    pending = _count_pending(rows, player_ids)
    needs_run = sum(1 for r in rows if str(r.get("needs run", "")).lower() == "yes")
    redeemed = sum(
        1
        for r in rows
        for pid in player_ids
        if _status_token(r.get(f"p:{pid}")) in _REDEEMED_STATUSES
    )

    st.subheader(title)
    m1, m2, m3 = st.columns(3)
    m1.metric("Codes", len(rows))
    m2.metric("Needs run", needs_run)
    m3.metric("Player slots pending", pending, help="PENDING across all players in table")

    if redeemed:
        st.caption(f"{redeemed} player slot(s) already redeemed (SUCCESS / ALREADY_RECEIVED).")

    col_order = _gift_codes_column_order(player_ids)
    df = pd.DataFrame(rows).reindex(columns=col_order).fillna("—")
    st.dataframe(
        _style_gift_codes_table(df),
        column_config=_gift_codes_column_config(player_ids, registry),  # ty: ignore[invalid-argument-type]
        column_order=col_order,
        hide_index=True,
        width="stretch",
        row_height=_DF_ROW_HEIGHT_PX,
        height=min(52 + (_DF_ROW_HEIGHT_PX + 2) * max(len(rows), 1), 520),
    )


st.title("DB · Gift codes")
st.caption(
    "Century Game promo codes (`POST /api/gift_code`). "
    "`userFor` statuses are written by the redeemer; "
    "**Expired** is calendar `expires` or API `CDK_EXPIRED` / `CDK_NOT_FOUND`."
)

repo = _repo_root()
codes_path = repo / "db" / "giftCodes.yaml"
devices_path = repo / "db" / "devices.yaml"

c1, c2 = st.columns(2)
with c1:
    st.markdown(f"**Codes file:** `{codes_path.relative_to(repo).as_posix()}`")
with c2:
    st.markdown(f"**Devices:** `{devices_path.relative_to(repo).as_posix()}`")

btn_cols = st.columns([1, 1, 1, 4])
with btn_cols[0]:
    run_scrape = st.button("Scrape now", help="Scrape wosrewards.com and append new codes to YAML")
with btn_cols[1]:
    run_redeem = st.button("Redeem now", help="Redeem all PENDING codes for all players in devices.yaml")
with btn_cols[2]:
    if st.button("Reload"):
        st.rerun()

if run_scrape:
    with st.spinner("Scraping wosrewards.com…"):
        new = asyncio.run(poll_once(codes_path))
    if new:
        st.success(f"Found {len(new)} new code(s): {', '.join(new)}")
    else:
        st.info("No new codes found.")
    st.rerun()

if run_redeem:
    if not codes_path.is_file():
        st.error(f"Missing `{codes_path.relative_to(repo)}`")
    elif not devices_path.is_file():
        st.error(f"Missing `{devices_path.relative_to(repo)}`")
    else:
        pb = st.progress(0.0, text="Preparing…")

        def _on_progress(done: int, total: int, label: str) -> None:
            if total <= 0:
                pb.progress(1.0, text="Nothing to redeem")
                return
            ratio = min(1.0, max(0.0, done / total))
            pb.progress(ratio, text=f"Redeeming {done}/{total} · {label}")

        asyncio.run(
            run_gift_code_redeemer(codes_path, devices_path, progress_cb=_on_progress)
        )
        pb.progress(1.0, text="Done")
        st.success("Done.")
        st.rerun()

db, db_error = _load_codes(codes_path)
registry = load_devices(devices_path)
player_ids = list(dict.fromkeys(registry.all_player_ids()))
for c in db.codes:
    for pid in c.user_for:
        if pid not in player_ids:
            player_ids.append(pid)

if not codes_path.is_file():
    rel = codes_path.relative_to(repo).as_posix()
    st.warning(f"Missing `{rel}` — create it or run the scraper once.")
elif db_error:
    rel = codes_path.relative_to(repo).as_posix()
    st.error(f"Could not parse `{rel}`: {db_error}")
elif not db.codes:
    st.info("No codes in YAML yet.")
else:
    q = st.text_input(
        "Filter (code / player id / status / API msg)",
        value="",
        key="db_gift_codes_filter",
    ).strip().lower()

    active_rows: list[dict[str, object]] = []
    expired_rows: list[dict[str, object]] = []

    for code in db.codes:
        row = _build_row(code, player_ids, registry)
        hay = " ".join(str(v) for v in row.values()).lower()
        if q and q not in hay:
            continue
        if code.is_effectively_expired():
            expired_rows.append(row)
        else:
            active_rows.append(row)

    if not active_rows and not expired_rows:
        st.info("No codes matched the filter.")
    else:
        _render_gift_codes_table(
            active_rows,
            player_ids=player_ids,
            registry=registry,
            title=f"Active codes ({len(active_rows)})",
        )
        if expired_rows:
            with st.expander(
                f"Expired / dead codes ({len(expired_rows)})",
                expanded=False,
            ):
                _render_gift_codes_table(
                    expired_rows,
                    player_ids=player_ids,
                    registry=registry,
                    title=f"Expired ({len(expired_rows)})",
                )

    with st.expander("Status legend", expanded=False):
        st.markdown(
            "- `PENDING` — not redeemed yet (or failed last time)\n"
            "- `SUCCESS` / `ALREADY_RECEIVED` — done for that player\n"
            "- `CDK_EXPIRED` / `CDK_NOT_FOUND` — from API; copied to every player\n"
            "- `STOVE_LEVEL_TOO_LOW` — player below required furnace level\n"
            "- `FAILED` — login, captcha, or network error\n"
            "- **API err / API msg** — last `err_code` and `msg` from Century API"
        )
