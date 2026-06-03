"""Dreamscape Memory map-onboarding routes.

Backs the dashboard flow that builds the solver's scene maps: persist a guide
image, OCR its numbered markers, parse the pasted item-name list, and save a
scene into the module scene database. See ``api.services.dreamscape_onboarding``.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.services.dreamscape_onboarding import (
    DetectMarkersResult,
    ListMapsResult,
    ParseNamesResult,
    SaveMapResult,
    SceneDetail,
    detect_markers_on_image,
    get_scene,
    list_scenes,
    parse_name_list,
    save_scene,
    save_scene_image,
)

router = APIRouter(prefix="/api/dreamscape", tags=["dreamscape-onboarding"])


class ParseNamesBody(BaseModel):
    text: str


class SceneRectBody(BaseModel):
    left: float
    top: float
    width: float
    height: float


class ScenePointBody(BaseModel):
    n: int
    name: str
    xPct: float
    yPct: float


class SaveSceneBody(BaseModel):
    title: str
    source_image: str
    scene_rect: SceneRectBody | None = None
    points: list[ScenePointBody]
    activate: bool = False


async def _read_image(file: UploadFile) -> bytes:
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="file must be an image")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty file")
    return data


@router.post("/detect-markers")
async def post_detect_markers(
    file: Annotated[UploadFile, File(description="numbered guide image")],
    expected: Annotated[int | None, Form()] = None,
    psm: Annotated[int | None, Form()] = None,
) -> DetectMarkersResult:
    data = await _read_image(file)
    try:
        return await run_in_threadpool(
            detect_markers_on_image, data, expected=expected, psm=psm
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/parse-names")
def post_parse_names(body: ParseNamesBody) -> ParseNamesResult:
    return parse_name_list(body.text)


@router.post("/scenes/{slug}/image")
async def post_scene_image(
    slug: str,
    file: Annotated[UploadFile, File(description="guide image to persist")],
) -> dict[str, object]:
    data = await _read_image(file)
    try:
        return await run_in_threadpool(save_scene_image, slug, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/scenes/{slug}")
def post_save_scene(slug: str, body: SaveSceneBody) -> SaveMapResult:
    try:
        return save_scene(
            slug,
            title=body.title,
            source_image=body.source_image,
            scene_rect=body.scene_rect.model_dump() if body.scene_rect else None,
            points=[p.model_dump() for p in body.points],
            activate=body.activate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/scenes")
def get_scenes() -> ListMapsResult:
    return list_scenes()


@router.get("/scenes/{slug}")
def get_scene_detail(slug: str) -> SceneDetail:
    try:
        return get_scene(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
