"""Sync HTTP client for the optional OmniParser sidecar."""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx

from config.env_loader import load_env_once
from config.loader import Settings, load_settings
from omniparser.types import ParsedUiElement

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OmniparserParseResult:
    elements: tuple[ParsedUiElement, ...]
    width: int
    height: int


def resolve_omniparser_mode() -> str:
    load_env_once()
    mode = (os.environ.get("OMNIPARSER_MODE") or "http").strip().lower()
    return mode if mode in {"http", "local"} else "http"


def resolve_omniparser_local_backend() -> str:
    load_env_once()
    backend = (os.environ.get("OMNIPARSER_LOCAL_BACKEND") or "icon_detect").strip().lower()
    return backend if backend in {"icon_detect", "full"} else "icon_detect"


def resolve_omniparser_url(settings: Settings | None = None) -> str:
    env = (os.environ.get("OMNIPARSER_URL") or "").strip()
    if env:
        return env.rstrip("/")
    cfg = settings if settings is not None else load_settings()
    return (cfg.omniparser.url or "").strip().rstrip("/")


def resolve_omniparser_timeout_seconds(settings: Settings | None = None) -> int:
    raw = (os.environ.get("OMNIPARSER_TIMEOUT_SECONDS") or "").strip()
    if raw.isdigit():
        return max(5, int(raw))
    cfg = settings if settings is not None else load_settings()
    return max(5, int(cfg.omniparser.timeout_seconds))


def _result_from_data(data: dict[str, Any], fallback_size: tuple[int, int]) -> OmniparserParseResult:
    width = int(data.get("width") or fallback_size[0])
    height = int(data.get("height") or fallback_size[1])
    raw_elements = data.get("elements")
    if not isinstance(raw_elements, list):
        msg = "OmniParser response missing elements[]"
        raise ValueError(msg)
    elements: list[ParsedUiElement] = []
    for item_raw in raw_elements:
        if not isinstance(item_raw, dict):
            continue
        item: dict[str, Any] = cast("dict[str, Any]", item_raw)
        bbox_raw = item.get("bbox")
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
            continue
        try:
            bbox: tuple[float, float, float, float] = (
                float(bbox_raw[0]),
                float(bbox_raw[1]),
                float(bbox_raw[2]),
                float(bbox_raw[3]),
            )
        except (TypeError, ValueError):
            continue
        el_type_raw = str(item.get("type") or "icon").strip().lower()
        el_type: Literal["icon", "text"] = (
            cast('Literal["icon", "text"]', el_type_raw)
            if el_type_raw in ("icon", "text")
            else "icon"
        )
        elements.append(
            ParsedUiElement(
                type=el_type,
                bbox=bbox,
                interactivity=bool(item.get("interactivity", False)),
                content=str(item.get("content") or "").strip(),
            )
        )
    return OmniparserParseResult(elements=tuple(elements), width=width, height=height)


def _parse_screenshot_local(
    image: Image.Image,
    *,
    timeout_seconds: int | None,
    box_threshold: float,
    iou_threshold: float,
    use_paddleocr: bool,
    imgsz: int | None,
) -> OmniparserParseResult:
    timeout = float(timeout_seconds if timeout_seconds is not None else resolve_omniparser_timeout_seconds())
    rgb = image.convert("RGB")
    with TemporaryDirectory() as tmp_dir:
        image_path = os.path.join(tmp_dir, "screenshot.png")
        output_path = os.path.join(tmp_dir, "omniparser.json")
        rgb.save(image_path, format="PNG")
        cmd = [
            sys.executable,
            "-m",
            "omniparser.oneshot",
            "--image",
            image_path,
            "--output",
            output_path,
            "--box-threshold",
            str(float(box_threshold)),
            "--iou-threshold",
            str(float(iou_threshold)),
            "--backend",
            resolve_omniparser_local_backend(),
        ]
        if imgsz is not None:
            cmd.extend(["--imgsz", str(int(imgsz))])
        if not use_paddleocr:
            cmd.append("--no-paddleocr")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            msg = f"OmniParser local subprocess failed ({proc.returncode}): {detail}"
            raise RuntimeError(msg)
        with open(output_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            msg = "invalid OmniParser local response"
            raise ValueError(msg)
    logger.info("OmniParser local: %d elements", len(data.get("elements") or []))
    return _result_from_data(data, rgb.size)


def parse_screenshot(
    image: Image.Image,
    *,
    url: str | None = None,
    timeout_seconds: int | None = None,
    box_threshold: float = 0.05,
    iou_threshold: float = 0.1,
    use_paddleocr: bool = True,
    imgsz: int | None = None,
) -> OmniparserParseResult:
    """POST the image to ``{url}/parse`` and return structured elements."""

    if resolve_omniparser_mode() == "local":
        return _parse_screenshot_local(
            image,
            timeout_seconds=timeout_seconds,
            box_threshold=box_threshold,
            iou_threshold=iou_threshold,
            use_paddleocr=use_paddleocr,
            imgsz=imgsz,
        )

    base = (url or resolve_omniparser_url()).strip().rstrip("/")
    if not base:
        msg = (
            "OmniParser URL is not configured — set omniparser.url in config/settings.yaml "
            "or OMNIPARSER_URL in .env, then run the sidecar (see omniparser/service.py)."
        )
        raise ValueError(msg)
    timeout = float(timeout_seconds if timeout_seconds is not None else resolve_omniparser_timeout_seconds())
    buf = io.BytesIO()
    rgb = image.convert("RGB")
    rgb.save(buf, format="PNG")
    payload = {
        "image_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "box_threshold": float(box_threshold),
        "iou_threshold": float(iou_threshold),
        "use_paddleocr": bool(use_paddleocr),
    }
    if imgsz is not None:
        payload["imgsz"] = int(imgsz)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base}/parse", json=payload)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        msg = "invalid OmniParser response"
        raise ValueError(msg)
    result = _result_from_data(data, rgb.size)
    logger.info("OmniParser: %d elements from %s", len(result.elements), base)
    return result


def check_omniparser_health(*, url: str | None = None, timeout_seconds: float = 3.0) -> dict[str, object]:
    if resolve_omniparser_mode() == "local":
        try:
            from omniparser.local import health_status

            out = health_status()
            out["mode"] = "local"
            return out
        except Exception as exc:
            return {"ok": False, "mode": "local", "error": str(exc)}

    base = (url or resolve_omniparser_url()).strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "url_not_configured"}
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(f"{base}/health")
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
