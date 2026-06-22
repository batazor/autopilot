"""Labeling / reference annotator HTTP routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.services import labeling as labeling_svc
from api.services import labeling_bundle as bundle_svc
from api.services.game_resolver import request_game, set_current_request_game

router = APIRouter(
    prefix="/api/labeling",
    tags=["labeling"],
)


def _pin_game(game: str) -> None:
    # Labeling services read the active game from a contextvar. Sync FastAPI
    # handlers may run in a worker context, so pin it inside the handler itself
    # instead of relying only on router-level dependencies.
    set_current_request_game(game)


class SaveRegionsBody(BaseModel):
    regions: list[dict[str, Any]] = Field(default_factory=list)
    version: str | None = None
    screen_id: str | None = None


@router.get("/scopes")
def get_labeling_scopes(game: str = Depends(request_game)) -> dict[str, list[dict[str, Any]]]:
    _pin_game(game)
    return {"scopes": labeling_svc.list_labeling_scopes()}


@router.get("/screen-ids")
def get_labeling_screen_ids(
    scope: str = Query(default="core"),
    current: str = Query(default=""),
    game: str = Depends(request_game),
) -> dict[str, list[str]]:
    _pin_game(game)
    return {
        "screen_ids": labeling_svc.list_screen_id_options(
            scope=scope,
            current_screen_id=current,
        ),
    }


class CaptureBody(BaseModel):
    instance_id: str


class RefreshBody(BaseModel):
    instance_id: str
    ref: str


class PromoteBody(BaseModel):
    ref: str
    basename: str
    instance_id: str
    regions: list[dict[str, Any]] | None = None
    screen_id: str | None = None


class RenameBody(BaseModel):
    ref: str
    basename: str
    instance_id: str


class AddVersionBody(BaseModel):
    ref: str
    version_id: str
    cond: str


class VersionCondBody(BaseModel):
    ref: str
    cond: str


class BindVersionOcrBody(BaseModel):
    ref: str
    ocr: str | None = None


class RefOnlyBody(BaseModel):
    ref: str


@router.get("/references")
def list_references(
    scope: str = Query(default="core"),
    limit: int = Query(default=300, ge=1, le=1000),
    game: str = Depends(request_game),
) -> dict[str, list[dict[str, Any]]]:
    _pin_game(game)
    return {"references": labeling_svc.list_reference_paths(scope=scope, limit=limit)}


@router.get("/stale-crops")
def get_stale_crops(
    scope: str = Query(default="core"),
    limit: int = Query(default=100, ge=1, le=500),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    return labeling_svc.list_stale_crops(scope=scope, limit=limit)


@router.get("/references/{ref_path:path}/image")
def get_reference_image(ref_path: str, game: str = Depends(request_game)) -> Response:
    _pin_game(game)
    try:
        png = labeling_svc.read_reference_bytes(ref_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png")


@router.get("/references/{ref_path:path}/bundle")
def get_reference_bundle(
    ref_path: str,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> Response:
    _pin_game(game)
    try:
        filename, data = bundle_svc.export_screen_bundle(ref_path, scope=scope)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/references/{ref_path:path}")
def get_reference_document(
    ref_path: str,
    version: str | None = Query(default=None),
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.get_labeling_document(ref_path, version=version, scope=scope)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/references/{ref_path:path}")
def put_reference_regions(
    ref_path: str,
    body: SaveRegionsBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.save_labeling_regions(
            ref_path,
            body.regions,
            version=body.version,
            screen_id=body.screen_id,
            scope=scope,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/import-png")
async def post_import_png(
    instance_id: str = Form(...),
    scope: str = Form(default="core"),
    file: UploadFile = File(...),  # noqa: B008
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        raw = await file.read()
        if file.content_type and not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="file must be an image")
        return labeling_svc.import_dropped_png(
            raw,
            instance_id,
            scope=scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import-bundle")
async def post_import_bundle(
    scope: str = Form(default="core"),
    file: UploadFile = File(...),  # noqa: B008
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        raw = await file.read()
        return bundle_svc.import_screen_bundle(raw, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ApplyBundleBody(BaseModel):
    staged_ref: str
    target_ref: str
    regions: list[dict[str, Any]] = Field(default_factory=list)
    screen_id: str | None = None
    use_incoming_image: bool = False


@router.post("/import-bundle/apply")
def post_import_bundle_apply(
    body: ApplyBundleBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return bundle_svc.apply_imported_bundle(
            scope=scope,
            staged_ref=body.staged_ref,
            target_ref=body.target_ref,
            regions=body.regions,
            screen_id=body.screen_id,
            use_incoming_image=body.use_incoming_image,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/capture")
def post_capture(
    body: CaptureBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.capture_new_screenshot(body.instance_id, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/refresh")
def post_refresh(
    body: RefreshBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.refresh_reference(body.ref, body.instance_id, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/capture")
def delete_capture(
    ref: str = Query(..., min_length=1),
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.discard_pending_capture(ref, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/references/{ref_path:path}")
def delete_reference(
    ref_path: str,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.delete_reference(ref_path, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/crops")
def post_export_crops(
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.export_region_crops(scope=scope)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/promote")
def post_promote(
    body: PromoteBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.promote_reference(
            body.ref,
            body.basename,
            body.instance_id,
            regions=body.regions,
            screen_id=body.screen_id,
            scope=scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/rename")
def post_rename(
    body: RenameBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.rename_reference(
            body.ref, body.basename, body.instance_id, scope=scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/versions/suggest")
def get_suggest_version_id(
    ref: str = Query(..., min_length=1),
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, str]:
    _pin_game(game)
    return labeling_svc.suggest_next_version_id(ref, scope=scope)


@router.post("/versions")
def post_add_version(
    body: AddVersionBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.add_version(
            body.ref, body.version_id, body.cond, scope=scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/versions/{version_id}")
def patch_version_cond(
    version_id: str,
    body: VersionCondBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.update_version_cond(
            body.ref, version_id, body.cond, scope=scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/versions/{version_id}/ocr")
def put_version_ocr(
    version_id: str,
    body: BindVersionOcrBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.bind_version_ocr(
            body.ref, version_id, body.ocr, scope=scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/versions/{version_id}")
def delete_version(
    version_id: str,
    ref: str = Query(..., min_length=1),
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.delete_version(ref, version_id, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/versions/{version_id}/sync-regions")
def post_sync_version_regions(
    version_id: str,
    body: RefOnlyBody,
    scope: str = Query(default="core"),
    game: str = Depends(request_game),
) -> dict[str, Any]:
    _pin_game(game)
    try:
        return labeling_svc.sync_version_regions_from_default(
            body.ref, version_id, scope=scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
