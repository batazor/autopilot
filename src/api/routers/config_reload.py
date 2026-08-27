"""Config endpoints: cache reload + the OCR-language setting.

Worker subprocesses keep their own caches; cross-process reload should be
fanned out via Redis (not implemented here).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services import ocr_lang as ocr_lang_service
from config.reload import reload_config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.post("/reload")
def post_reload() -> dict[str, str]:
    reload_config()
    return {"status": "ok"}


class OcrLangBody(BaseModel):
    lang: str


@router.get("/ocr-lang")
def get_ocr_lang() -> dict[str, object]:
    return {
        "lang": ocr_lang_service.current_lang(),
        "available": ocr_lang_service.available_langs(),
    }


@router.post("/ocr-lang")
def post_ocr_lang(body: OcrLangBody) -> dict[str, object]:
    try:
        return ocr_lang_service.set_lang(body.lang)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
