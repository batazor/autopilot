"""Optional FastAPI sidecar wrapping microsoft/OmniParser.

Run from repo root with a local OmniParser clone + weights::

    export OMNIPARSER_ROOT=/path/to/OmniParser   # git clone + weights per upstream README
    uv run uvicorn omniparser.service:app --host 127.0.0.1 --port 8765

Upstream: https://github.com/microsoft/OmniParser
"""
from __future__ import annotations

import base64
import io
import logging

from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from config.env_loader import load_env_once
from omniparser.local import health_status, parse_image

load_env_once()

logger = logging.getLogger(__name__)

app = FastAPI(title="WOS OmniParser Service")


class ParseRequest(BaseModel):
    image_b64: str
    box_threshold: float = 0.05
    iou_threshold: float = 0.1
    use_paddleocr: bool = True
    imgsz: int | None = Field(default=None, ge=320, le=1920)


@app.get("/health")
def health() -> dict[str, object]:
    return health_status()


@app.post("/parse")
def parse(req: ParseRequest) -> dict[str, object]:
    try:
        raw = base64.b64decode(req.image_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid image_b64: {exc}") from exc
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"cannot decode image: {exc}") from exc
    try:
        elements = parse_image(
            image,
            box_threshold=req.box_threshold,
            iou_threshold=req.iou_threshold,
            use_paddleocr=req.use_paddleocr,
            imgsz=req.imgsz,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("OmniParser parse failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    w, h = image.size
    return {"width": w, "height": h, "elements": elements, "count": len(elements)}
