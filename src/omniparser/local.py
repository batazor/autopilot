"""Local OmniParser backend shared by the sidecar and one-shot subprocess CLI."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from config.env_loader import load_env_once

load_env_once()

logger = logging.getLogger(__name__)

_models_lock = threading.Lock()
_models: dict[str, Any] | None = None
_icon_model: Any | None = None
_omniparser_root: Path | None = None


def resolve_omniparser_root() -> Path:
    global _omniparser_root  # noqa: PLW0603
    if _omniparser_root is not None:
        return _omniparser_root
    raw = (os.environ.get("OMNIPARSER_ROOT") or "").strip()
    if not raw:
        msg = "Set OMNIPARSER_ROOT to a microsoft/OmniParser checkout with weights/"
        raise RuntimeError(msg)
    root = Path(raw).expanduser().resolve()
    if not (root / "util" / "utils.py").is_file():
        msg = f"OMNIPARSER_ROOT does not look like OmniParser: {root}"
        raise RuntimeError(msg)
    _omniparser_root = root
    return root


def health_status() -> dict[str, Any]:
    root = (os.environ.get("OMNIPARSER_ROOT") or "").strip()
    error = ""
    ok = False
    if not root:
        error = "OMNIPARSER_ROOT is not set"
    else:
        root_path = Path(root).expanduser()
        if not (root_path / "util" / "utils.py").is_file():
            error = f"OMNIPARSER_ROOT does not look like OmniParser: {root}"
        elif not (root_path / "weights" / "icon_detect" / "model.pt").is_file():
            error = f"Missing YOLO weights at {root_path / 'weights' / 'icon_detect' / 'model.pt'}"
        else:
            ok = True
    return {
        "ok": ok,
        "models_loaded": _models is not None,
        "icon_model_loaded": _icon_model is not None,
        "omniparser_root": root,
        "error": error,
    }


def _resolve_device() -> str | None:
    raw = (os.environ.get("OMNIPARSER_DEVICE") or "").strip()
    return raw or None


def load_models() -> dict[str, Any]:
    global _models  # noqa: PLW0603
    with _models_lock:
        if _models is not None:
            return _models
        root = resolve_omniparser_root()
        root_s = str(root)
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        os.chdir(root)
        try:
            from util.utils import (  # type: ignore[import-not-found]  # ty: ignore[unresolved-import]
                check_ocr_box,
                get_som_labeled_img,
                get_yolo_model,
            )
        except ImportError as exc:
            missing = str(getattr(exc, "name", "") or "").strip() or getattr(exc, "msg", "") or ""
            hint = (
                "Full OmniParser needs heavy Python deps from the repo optional group `omniparser` "
                "(easyocr, torch, transformers, paddleocr, …). From repo root run: uv sync --extra omniparser. "
                "For a lighter path without that stack, set OMNIPARSER_LOCAL_BACKEND=icon_detect (YOLO bboxes only)."
            )
            msg = hint if not missing else f"{hint} (import error module: {missing})"
            raise RuntimeError(msg) from exc

        weights = root / "weights"
        yolo_path = weights / "icon_detect" / "model.pt"
        caption_path = weights / "icon_caption_florence"
        if not yolo_path.is_file():
            msg = f"Missing YOLO weights at {yolo_path} - see OmniParser README"
            raise RuntimeError(msg)
        if not caption_path.is_dir():
            msg = f"Missing caption weights at {caption_path}"
            raise RuntimeError(msg)
        logger.info("Loading OmniParser models from %s ...", root)
        t0 = time.time()
        yolo = get_yolo_model(model_path=str(yolo_path))
        caption = _get_florence2_caption_model_processor(caption_path)
        _models = {
            "yolo": yolo,
            "caption": caption,
            "check_ocr_box": check_ocr_box,
            "get_som_labeled_img": get_som_labeled_img,
        }
        logger.info("OmniParser models loaded in %.1fs", time.time() - t0)
        return _models


def _get_florence2_caption_model_processor(model_path: Path) -> dict[str, Any]:
    import torch  # ty: ignore[unresolved-import]
    from transformers import AutoModelForCausalLM, AutoProcessor  # ty: ignore[unresolved-import]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(
        "microsoft/Florence-2-base",
        trust_remote_code=True,
    )
    dtype = torch.float32 if device == "cpu" else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        str(model_path),
        torch_dtype=dtype,
        trust_remote_code=True,
        attn_implementation="eager",
    )
    return {"model": model.to(device), "processor": processor}


def _normalize_xyxy(
    box: list[float] | tuple[float, ...],
    *,
    width: int,
    height: int,
) -> list[float]:
    x1, y1, x2, y2 = [float(box[i]) for i in range(4)]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
        max(0.0, min(1.0, x2 / width)),
        max(0.0, min(1.0, y2 / height)),
    ]


def _iou(a: list[float], b: list[float]) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _predict_icon_elements(
    image: Image.Image,
    model: Any,
    *,
    box_threshold: float,
    imgsz: int | None,
) -> list[dict[str, Any]]:
    device = _resolve_device()
    kwargs: dict[str, Any] = {
        "source": image.convert("RGB"),
        "conf": float(box_threshold),
        "verbose": False,
    }
    if imgsz is None:
        width, height = image.size
        kwargs["imgsz"] = (height, width)
    else:
        kwargs["imgsz"] = int(imgsz)
    if device:
        kwargs["device"] = device
    result = model.predict(**kwargs)[0]
    elements: list[dict[str, Any]] = []
    boxes = getattr(result, "boxes", None)
    if boxes is None or not len(boxes):
        return elements
    width, height = image.size
    for box, conf in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), strict=False):
        elements.append(
            {
                "type": "icon",
                "bbox": _normalize_xyxy(box, width=width, height=height),
                "interactivity": True,
                "content": f"icon {float(conf):.2f}",
            }
        )
    return elements


def parse_image(
    image: Image.Image,
    *,
    box_threshold: float,
    iou_threshold: float,
    use_paddleocr: bool,
    imgsz: int | None,
) -> list[dict[str, Any]]:
    models = load_models()
    check_ocr = models["check_ocr_box"]
    width, height = image.size
    box_overlay_ratio = max(width, height) / 3200.0
    draw_bbox_config = {
        "text_scale": 0.8 * box_overlay_ratio,
        "text_thickness": max(int(2 * box_overlay_ratio), 1),
        "text_padding": max(int(3 * box_overlay_ratio), 1),
        "thickness": max(int(3 * box_overlay_ratio), 1),
    }
    ocr_bbox_rslt, _goal = check_ocr(
        image,
        display_img=False,
        output_bb_format="xyxy",
        goal_filtering=None,
        easyocr_args={"paragraph": False, "text_threshold": 0.9},
        use_paddleocr=use_paddleocr,
    )
    text, ocr_bbox = ocr_bbox_rslt
    text = text or []
    ocr_bbox = ocr_bbox or []
    if not ocr_bbox:
        return parse_icon_detect_image(
            image,
            box_threshold=box_threshold,
            imgsz=imgsz,
        )
    _encoded, _coords, filtered_boxes = models["get_som_labeled_img"](
        image,
        models["yolo"],
        BOX_TRESHOLD=box_threshold,
        output_coord_in_ratio=True,
        ocr_bbox=ocr_bbox,
        draw_bbox_config=draw_bbox_config,
        caption_model_processor=models["caption"],
        ocr_text=text,
        iou_threshold=iou_threshold,
        imgsz=imgsz,
    )
    elements: list[dict[str, Any]] = []
    for box in filtered_boxes:
        if not isinstance(box, dict):
            continue
        bbox = box.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
            continue
        elements.append(
            {
                "type": str(box.get("type") or "icon"),
                "bbox": [float(bbox[i]) for i in range(4)],
                "interactivity": bool(box.get("interactivity", False)),
                "content": str(box.get("content") or ""),
            }
        )
    return elements


def parse_icon_detect_image(
    image: Image.Image,
    *,
    box_threshold: float,
    imgsz: int | None,
) -> list[dict[str, Any]]:
    """Fast one-shot path: use OmniParser's YOLO icon detector only."""
    global _icon_model  # noqa: PLW0603
    root = resolve_omniparser_root()
    model_path = root / "weights" / "icon_detect" / "model.pt"
    if not model_path.is_file():
        msg = f"Missing YOLO weights at {model_path} - see OmniParser README"
        raise RuntimeError(msg)
    if _icon_model is None:
        from ultralytics import YOLO  # ty: ignore[unresolved-import]

        _icon_model = YOLO(str(model_path))

    return _predict_icon_elements(
        image,
        _icon_model,
        box_threshold=box_threshold,
        imgsz=imgsz,
    )
