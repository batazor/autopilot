"""DB: promo gift codes from ``db/giftCodes.yaml`` (Century API redemption)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from config.devices import DeviceRegistry, load_devices
from gift.models import GiftCode, GiftCodeDB, RedeemStatus
from gift.redeemer import run_gift_code_redeemer
from gift.scraper import poll_once


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_codes(path: Path) -> tuple[GiftCodeDB, str | None]:
    if not path.is_file():
        return GiftCodeDB(), None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return GiftCodeDB.model_validate(raw), None
    except Exception as exc:
        return GiftCodeDB(), str(exc)


_YES_NO_COLS = ("slot expired", "needs run")
_ROW_EXPIRED_BG = "background-color: #e0e0e0"
_CELL_YES_BG = "background-color: #c8e6c9"
_CELL_NO_BG = "background-color: #ffcdd2"
_REDEEMED_STATUSES = (RedeemStatus.SUCCESS.value, RedeemStatus.ALREADY_RECEIVED.value)


def _style_gift_codes_table(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    cols = list(df.columns)
    player_cols = [c for c in cols if c.startswith("p:")]

    def row_styles(row: pd.Series) -> list[str]:
        n = len(cols)
        if str(row.get("slot expired", "")).lower() == "yes":
            return [_ROW_EXPIRED_BG] * n
        out = [""] * n
        for name in _YES_NO_COLS:
            if name not in cols:
                continue
            j = cols.index(name)
            val = str(row.get(name, "")).lower()
            out[j] = _CELL_YES_BG if val == "yes" else _CELL_NO_BG
        for name in player_cols:
            val = str(row.get(name, ""))
            if val.startswith(_REDEEMED_STATUSES):
                out[cols.index(name)] = _CELL_YES_BG
        return out

    return df.style.apply(row_styles, axis=1)


def _display_dataframe(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build an Arrow-friendly display dataframe for Streamlit."""
    return pd.DataFrame(rows).fillna("—").astype(str)


st.title("DB · Gift codes")
st.caption(
    "Century Game promo codes (`POST /api/gift_code`). "
    "`userFor` + `lastApiErrCode` / `lastApiMsg` are written by the redeemer; "
    "`slot expired` is calendar `expires` or API `CDK_EXPIRED` / `CDK_NOT_FOUND`."
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
        nick = registry.get_gamer(pid)
        row[f"p:{pid}"] = f"{status.value} ({nick.nickname})" if nick else status.value
    return row


active_rows: list[dict[str, object]] = []
expired_rows: list[dict[str, object]] = []

if db.codes:
    q = st.text_input(
        "Filter (code name / player id / status)",
        value="",
        key="db_gift_codes_filter",
    ).strip().lower()
else:
    q = ""

for code in db.codes:
    row = _build_row(code, player_ids, registry)
    hay = " ".join(str(v) for v in row.values()).lower()
    if q and q not in hay:
        continue
    if code.is_effectively_expired():
        expired_rows.append(row)
    else:
        active_rows.append(row)

if active_rows:
    df = _display_dataframe(active_rows)
    st.subheader(f"Active codes: {len(active_rows)}")
    st.dataframe(_style_gift_codes_table(df), width="stretch", hide_index=True)

if expired_rows:
    with st.expander(f"Expired / dead codes: {len(expired_rows)}", expanded=False):
        df_exp = _display_dataframe(expired_rows)
        st.dataframe(_style_gift_codes_table(df_exp), width="stretch", hide_index=True)

st.divider()
st.markdown("**Status legend**")
st.markdown(
    "- `PENDING` — not redeemed yet (or failed last time)\n"
    "- `SUCCESS` / `ALREADY_RECEIVED` — done for that player\n"
    "- `CDK_EXPIRED` / `CDK_NOT_FOUND` — from API; copied to every player in `devices.yaml`\n"
    "- `FAILED` — login, captcha, or network error\n"
    "- **API err / API msg** — last `err_code` and `msg` from a successful HTTP redeem response"
)
