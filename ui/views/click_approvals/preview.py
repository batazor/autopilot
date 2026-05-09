from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np
import streamlit as st

from layout.area_lookup import screen_region_by_name
from layout.crop_paths import exported_crop_png
from ui.preview_display import png_bytes_fitted
from ui.reference_preview import load_rolling_instance_preview, references_root

from .common import load_area_doc
from .ctx import ClickApprovalsCtx


def _fmt_ratio(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "—"


def pct_bbox_to_px_rect(bb: dict[str, object], w: int, h: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    bw = float(bb.get("width") or 0.0)
    bh = float(bb.get("height") or 0.0)
    left = max(0, min(w - 1, int(x / 100.0 * w)))
    top = max(0, min(h - 1, int(y / 100.0 * h)))
    right = max(left + 1, min(w, int((x + bw) / 100.0 * w)))
    bottom = max(top + 1, min(h, int((y + bh) / 100.0 * h)))
    return left, top, right, bottom


def _ensure_fresh_reference_crop(
    *,
    ctx: ClickApprovalsCtx,
    ref_rel: str,
    region_name: str,
    bbox_pct: dict[str, object],
    crop_path: Any,
) -> None:
    try:
        area_mtime = float(ctx.area_path.stat().st_mtime) if ctx.area_path.is_file() else 0.0
        ref_path = ctx.repo_root / ref_rel
        ref_mtime = float(ref_path.stat().st_mtime) if ref_path.is_file() else 0.0
        crop_mtime = float(crop_path.stat().st_mtime) if crop_path.is_file() else 0.0
        if crop_path.is_file() and crop_mtime >= max(area_mtime, ref_mtime):
            return
        img = cv2.imread(str(ref_path))
        if img is None:
            return
        hr, wr = int(img.shape[0]), int(img.shape[1])
        x = float(bbox_pct.get("x") or 0.0)
        y = float(bbox_pct.get("y") or 0.0)
        bw = float(bbox_pct.get("width") or 0.0)
        bh = float(bbox_pct.get("height") or 0.0)
        L = max(0, min(wr - 1, int(round(x / 100.0 * wr))))
        T = max(0, min(hr - 1, int(round(y / 100.0 * hr))))
        R = max(L + 1, min(wr, int(round((x + bw) / 100.0 * wr))))
        B = max(T + 1, min(hr, int(round((y + bh) / 100.0 * hr))))
        crop = img[T:B, L:R]
        if crop.size <= 0:
            return
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(crop_path), crop)
    except Exception:
        return


def _load_payload_preview(
    payload: dict[str, Any] | None,
) -> tuple[bytes | None, str, float | None]:
    if not isinstance(payload, dict):
        return None, "", None
    rel_raw = str(payload.get("preview_png_rel") or "").replace("\\", "/").strip().lstrip("/")
    if not rel_raw:
        return None, "", None
    root = references_root()
    path = (root / rel_raw).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None, "", None
    if not path.is_file():
        return None, "", None
    try:
        return path.read_bytes(), path.relative_to(root).as_posix(), path.stat().st_mtime
    except OSError:
        return None, "", None


def _approval_region_name(payload: dict[str, Any], ctx0: dict[str, Any]) -> str:
    """Region label for the pending input; prefer explicit request data over task context."""
    try:
        reg_name = str(payload.get("region") or "").strip()
    except Exception:
        reg_name = ""
    if not reg_name:
        reg_name = str(ctx0.get("approval_region") or "").strip()
    if not reg_name:
        reg_name = str(ctx0.get("current_task_region") or "").strip()
    return reg_name


def render_preview_with_point(
    *,
    ctx: ClickApprovalsCtx,
    instance_id: str,
    x: int | None,
    y: int | None,
    payload: dict[str, Any] | None,
    where: Any,
) -> None:
    """Render approval snapshot/rolling PNG preview with optional target crosshair."""
    ui = where or st
    png, rel, mtime = _load_payload_preview(payload)
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)
    if png is None:
        ui.info(f"No rolling preview yet for `{instance_id}`.")
        return

    arr = np.frombuffer(png, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        ui.warning("Could not decode rolling PNG.")
        return

    h, w = int(bgr.shape[0]), int(bgr.shape[1])

    def _draw_focus_rect(
        img: np.ndarray,
        *,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        label: str = "",
    ) -> None:
        x0 = int(max(0, min(w - 1, x0)))
        y0 = int(max(0, min(h - 1, y0)))
        x1 = int(max(x0 + 1, min(w, x1)))
        y1 = int(max(y0 + 1, min(h, y1)))
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 3, lineType=cv2.LINE_AA)
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 220, 255), 2, lineType=cv2.LINE_AA)
        if not label:
            return
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        thickness = 1
        (tw, th), base = cv2.getTextSize(label, font, font_scale, thickness)
        pad = 5
        lab_h = th + base + pad * 2
        gap = 4
        place_above = y0 >= lab_h + gap
        if place_above:
            by1 = y0 - gap
            by0 = by1 - lab_h
        else:
            by0 = y1 + gap
            by1 = by0 + lab_h
        bx0 = int(x0)
        bx1 = bx0 + tw + pad * 2
        if bx1 > w:
            bx0 = max(0, w - (tw + pad * 2))
            bx1 = w
        by0 = max(0, by0)
        by1 = min(h, by1)
        if by1 - by0 < lab_h:
            by0 = max(0, by1 - lab_h)
        cv2.rectangle(img, (bx0, by0), (bx1, by1), (0, 0, 0), -1, lineType=cv2.LINE_AA)
        cv2.rectangle(img, (bx0, by0), (bx1, by1), (0, 220, 255), 1, lineType=cv2.LINE_AA)
        cv2.putText(
            img,
            label,
            (bx0 + pad, by1 - pad - base),
            font,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )

    ptype = str(payload.get("type") or "").strip().lower() if isinstance(payload, dict) else ""
    is_set_node = ptype == "set_node"
    if isinstance(payload, dict) and not is_set_node:
        if ptype == "swipe":
            try:
                x1 = int(payload.get("x1") or 0)
                y1 = int(payload.get("y1") or 0)
                x2 = int(payload.get("x2") or 0)
                y2 = int(payload.get("y2") or 0)
                ms = int(payload.get("ms") or 0)

                x1 = int(max(0, min(w - 1, x1)))
                y1 = int(max(0, min(h - 1, y1)))
                x2 = int(max(0, min(w - 1, x2)))
                y2 = int(max(0, min(h - 1, y2)))

                cv2.arrowedLine(bgr, (x1, y1), (x2, y2), (0, 0, 0), 6, tipLength=0.25)
                cv2.arrowedLine(bgr, (x1, y1), (x2, y2), (0, 220, 255), 3, tipLength=0.25)

                dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                label = f"swipe {dist:.0f}px"
                if ms > 0:
                    label += f" · {ms}ms"
                _draw_focus_rect(bgr, x0=x1, y0=y1, x1=x1 + 2, y1=y1 + 2, label=label)
            except Exception:
                pass

        reg = payload.get("region")
        if isinstance(reg, dict):
            try:
                rx = int(float(reg.get("x") or 0))
                ry = int(float(reg.get("y") or 0))
                rw = int(float(reg.get("w") or 0))
                rh = int(float(reg.get("h") or 0))
                if rw > 0 and rh > 0:
                    x0 = max(0, min(w - 1, rx))
                    y0 = max(0, min(h - 1, ry))
                    x1 = max(x0 + 1, min(w, rx + rw))
                    y1 = max(y0 + 1, min(h, ry + rh))
                    _draw_focus_rect(bgr, x0=x0, y0=y0, x1=x1, y1=y1)
            except Exception:
                pass

        ctx0 = payload.get("context")
        if isinstance(ctx0, dict):
            reg_name = _approval_region_name(payload, ctx0)
            if reg_name:
                # If overlay provided an actual match box (e.g. found within *_search),
                # prefer it over the static area.json bbox to avoid misleading overlays.
                try:
                    mtlx = int(float(ctx0.get("current_task_match_top_left_x") or 0))
                    mtly = int(float(ctx0.get("current_task_match_top_left_y") or 0))
                    tw = int(float(ctx0.get("current_task_template_w") or 0))
                    th = int(float(ctx0.get("current_task_template_h") or 0))
                except Exception:
                    mtlx = mtly = tw = th = 0

                if tw <= 0 or th <= 0:
                    # Fallback for DSL taps: use `dsl_last_match_*` when it matches this tap.
                    try:
                        if str(ctx0.get("dsl_last_match_region") or "").strip() == reg_name:
                            mtlx = int(float(ctx0.get("dsl_last_match_top_left_x") or 0))
                            mtly = int(float(ctx0.get("dsl_last_match_top_left_y") or 0))
                            tw = int(float(ctx0.get("dsl_last_match_template_w") or 0))
                            th = int(float(ctx0.get("dsl_last_match_template_h") or 0))
                    except Exception:
                        pass

                if tw > 0 and th > 0:
                    x0 = max(0, min(w - 1, mtlx))
                    y0 = max(0, min(h - 1, mtly))
                    x1 = max(x0 + 1, min(w, mtlx + tw))
                    y1 = max(y0 + 1, min(h, mtly + th))
                    _draw_focus_rect(bgr, x0=x0, y0=y0, x1=x1, y1=y1, label=f"{reg_name} (match)")
                else:
                    area_doc = load_area_doc(ctx.area_path)
                    pair = screen_region_by_name(area_doc, reg_name)
                    if pair is not None and isinstance(pair[1].get("bbox"), dict):
                        L, T, R, B = pct_bbox_to_px_rect(pair[1]["bbox"], w, h)
                        _draw_focus_rect(bgr, x0=L, y0=T, x1=R, y1=B, label=reg_name)

    if x is not None and y is not None:
        px = int(max(0, min(w - 1, x)))
        py = int(max(0, min(h - 1, y)))
        cv2.circle(bgr, (px, py), 10, (0, 0, 255), 2)
        cv2.circle(bgr, (px, py), 3, (0, 0, 255), -1)
        cv2.line(bgr, (px - 18, py), (px + 18, py), (0, 0, 255), 1)
        cv2.line(bgr, (px, py - 18), (px, py + 18), (0, 0, 255), 1)

    ok, enc = cv2.imencode(".png", bgr)
    if not ok:
        ui.warning("Could not encode preview image.")
        return
    out_png = enc.tobytes()
    fitted, native, _disp = png_bytes_fitted(out_png, ctx.preview_max_side)
    cap = f"{rel or instance_id} · {native[0]}×{native[1]}"
    if mtime is not None:
        cap = f"{cap} · {time.strftime('%H:%M:%S', time.localtime(mtime))}"
    if x is not None and y is not None:
        cap = f"{cap} · target=({x},{y})"
    ui.image(fitted, caption=cap, width="stretch")

    if not isinstance(payload, dict) or is_set_node:
        return
    ctx0 = payload.get("context")
    if not isinstance(ctx0, dict):
        return
    reg_name = _approval_region_name(payload, ctx0)
    if not reg_name:
        return
    area_doc = load_area_doc(ctx.area_path)
    pair = screen_region_by_name(area_doc, reg_name)
    if pair is None:
        return
    entry, reg = pair
    if not isinstance(reg.get("bbox"), dict):
        return
    ref_rel = str(entry.get("ocr") or "").strip()
    if not ref_rel:
        return

    # Prefer dynamic match box (from overlay) over static area bbox for "live crop".
    try:
        mtlx = int(float(ctx0.get("current_task_match_top_left_x") or 0))
        mtly = int(float(ctx0.get("current_task_match_top_left_y") or 0))
        tw = int(float(ctx0.get("current_task_template_w") or 0))
        th = int(float(ctx0.get("current_task_template_h") or 0))
    except Exception:
        mtlx = mtly = tw = th = 0

    if tw <= 0 or th <= 0:
        try:
            if str(ctx0.get("dsl_last_match_region") or "").strip() == reg_name:
                mtlx = int(float(ctx0.get("dsl_last_match_top_left_x") or 0))
                mtly = int(float(ctx0.get("dsl_last_match_top_left_y") or 0))
                tw = int(float(ctx0.get("dsl_last_match_template_w") or 0))
                th = int(float(ctx0.get("dsl_last_match_template_h") or 0))
        except Exception:
            pass

    if tw > 0 and th > 0:
        L = max(0, min(w - 1, mtlx))
        T = max(0, min(h - 1, mtly))
        R = max(L + 1, min(w, mtlx + tw))
        B = max(T + 1, min(h, mtly + th))
    else:
        L, T, R, B = pct_bbox_to_px_rect(reg["bbox"], w, h)
    pad = 6
    L = max(0, min(w - 1, int(L - pad)))
    T = max(0, min(h - 1, int(T - pad)))
    R = max(L + 1, min(w, int(R + pad)))
    B = max(T + 1, min(h, int(B + pad)))

    found_png: bytes | None = None
    try:
        frag = bgr[T:B, L:R].copy()
        ok2, enc2 = cv2.imencode(".png", frag)
        if ok2:
            found_png = enc2.tobytes()
    except Exception:
        found_png = None

    sought_png: bytes | None = None
    sought_name: str | None = None
    try:
        crop_path = exported_crop_png(ctx.repo_root, ref_rel, reg_name)
        _ensure_fresh_reference_crop(
            ctx=ctx,
            ref_rel=ref_rel,
            region_name=reg_name,
            bbox_pct=reg["bbox"],
            crop_path=crop_path,
        )
        if crop_path.is_file():
            tpl = cv2.imread(str(crop_path))
            if tpl is not None:
                ok3, enc3 = cv2.imencode(".png", tpl)
                if ok3:
                    sought_png = enc3.tobytes()
                    sought_name = crop_path.name
    except Exception:
        sought_png = None

    with ui.container(border=True):
        ui.markdown(f"**Region** `{reg_name}` · live crop vs template")
        tpl_bright = str(ctx0.get("current_task_template_bright_ratio") or "").strip()
        patch_bright = str(ctx0.get("current_task_patch_bright_ratio") or "").strip()
        if tpl_bright or patch_bright:
            ui.caption(
                "Bright detail ratio · "
                f"template `{_fmt_ratio(tpl_bright)}` · live `{_fmt_ratio(patch_bright)}`"
            )
        c_found, c_sought = ui.columns(2, gap="medium", vertical_alignment="top")
        cap_max = ctx.region_crop_max_side
        with c_found:
            ui.caption("Live (from screenshot)")
            if found_png is not None:
                fitted2, native2, _ = png_bytes_fitted(found_png, cap_max)
                ui.image(fitted2, caption=f"{native2[0]}×{native2[1]} px", width="stretch")
            else:
                ui.caption("—")
        with c_sought:
            ui.caption("Template (reference crop)")
            if sought_png is not None:
                fitted3, native3, _ = png_bytes_fitted(sought_png, cap_max)
                ui.image(
                    fitted3,
                    caption=f"{sought_name or reg_name} · {native3[0]}×{native3[1]} px",
                    width="stretch",
                )
            else:
                ui.caption("—")
