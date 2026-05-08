from __future__ import annotations

import time
from typing import Any

import streamlit as st


def render_dsl_step_audit(ctx: dict[str, object]) -> None:
    """Last DSL `match` / `ocr` / `color_check` snapshot copied into approval `context`."""
    mr = str(ctx.get("dsl_last_match_region") or "").strip()
    ms = str(ctx.get("dsl_last_match_score") or "").strip()
    mt = str(ctx.get("dsl_last_match_threshold") or "").strip()
    mm = str(ctx.get("dsl_last_match_matched") or "").strip()
    md = str(ctx.get("dsl_last_match_detail") or "").strip()
    ma = str(ctx.get("dsl_last_match_at") or "").strip()

    ox_r = str(ctx.get("dsl_last_ocr_region") or "").strip()
    ox_store = str(ctx.get("dsl_last_ocr_store") or "").strip()
    ox_status = str(ctx.get("dsl_last_ocr_status") or "").strip()
    ox_thr = str(ctx.get("dsl_last_ocr_threshold") or "").strip()
    ox_conf = str(ctx.get("dsl_last_ocr_confidence") or "").strip()
    ox_raw = str(ctx.get("dsl_last_ocr_raw_text") or "").strip()
    ox_val = str(ctx.get("dsl_last_ocr_value") or "").strip()
    ox_at = str(ctx.get("dsl_last_ocr_at") or "").strip()

    cl_r = str(ctx.get("dsl_last_color_region") or "").strip()
    cl_status = str(ctx.get("dsl_last_color_status") or "").strip()
    cl_want = str(ctx.get("dsl_last_color_want") or "").strip().lower()
    cl_dom = str(ctx.get("dsl_last_color_dominant") or "").strip().lower()
    cl_share = str(ctx.get("dsl_last_color_share") or "").strip()
    cl_min = str(ctx.get("dsl_last_color_threshold") or "").strip()
    cl_at = str(ctx.get("dsl_last_color_at") or "").strip()

    if not mr and not ox_r and not ox_status and not cl_r and not cl_status:
        return

    def _age_line(ts: str) -> str:
        if not ts:
            return ""
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
        except (TypeError, ValueError, OSError):
            return ts

    with st.expander("DSL · last guards (`match` / `ocr` / `color_check`)", expanded=True):
        st.caption(
            "Redis audit from `DslScenarioTask` (fields `dsl_last_*` on the instance state hash)."
        )

        if mr:
            st.markdown("**Last YAML `match:`**")
            passed = "yes" if mm == "1" else ("no" if mm == "0" else "—")
            lines = [
                f"- Region: `{mr}`",
                f"- Score / threshold: `{ms or '—'}` / `{mt or '—'}` · passed: **{passed}**",
            ]
            if md:
                lines.append(f"- Detail: `{md}`")
            if ma:
                lines.append(f"- At: `{_age_line(ma)}`")
            st.markdown("\n".join(lines))
        else:
            st.caption("No `dsl_last_match_*` yet (no `match:` step ran on this instance).")

        if ox_r or ox_status:
            st.markdown("**Last YAML `ocr:`**")
            raw_disp = ox_raw.replace("\n", " ").strip()
            if len(raw_disp) > 180:
                raw_disp = raw_disp[:177] + "…"
            olines = [
                f"- Region → Redis field: `{ox_r or '—'}` → `{ox_store or '—'}`",
                f"- Status: **`{ox_status or '—'}`**",
                f"- Confidence / threshold: `{ox_conf or '—'}` / `{ox_thr or '—'}`",
                f"- Stored decoded value: `{ox_val or '—'}`",
                f"- Raw OCR text: `{raw_disp or '—'}`",
            ]
            if ox_at:
                olines.append(f"- At: `{_age_line(ox_at)}`")
            st.markdown("\n".join(olines))
        else:
            st.caption("No `dsl_last_ocr_*` yet (no `ocr:` step ran on this instance).")

        if cl_r or cl_status:
            st.markdown("**Last YAML `color_check:`**")
            want_disp = cl_want or "—"
            dom_disp = cl_dom or "—"
            share_disp = cl_share or "—"
            min_disp = cl_min or "—"
            passed = "yes" if cl_status == "ok" else ("no" if cl_status else "—")
            if passed == "yes":
                passed_html = "<span style='color:#16a34a;font-weight:650;'>yes</span>"
            elif passed == "no":
                passed_html = "<span style='color:#dc2626;font-weight:650;'>no</span>"
            else:
                passed_html = "<span style='color:#9aa0a6;font-weight:650;'>—</span>"

            st.markdown(
                "\n".join(
                    [
                        f"- Region: `{cl_r or '—'}`",
                        f"- Want: `{want_disp}` · dominant: `{dom_disp}`",
                        f"- Share / threshold: `{share_disp}` / `{min_disp}` · passed: {passed_html}",
                        f"- Status: **`{cl_status or '—'}`**",
                    ]
                ),
                unsafe_allow_html=True,
            )
            if cl_at:
                st.markdown(f"- At: `{_age_line(cl_at)}`")
        else:
            st.caption("No `dsl_last_color_*` yet (no `color_check:` step ran on this instance).")


def render_payload_json(payload: dict[str, Any]) -> None:
    with st.expander("Payload", expanded=True):
        import json

        st.code(json.dumps(payload, indent=2, ensure_ascii=False), language="json")

