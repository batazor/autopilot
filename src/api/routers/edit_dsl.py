"""Module DSL YAML editor routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.services import edit_dsl_api as svc

router = APIRouter(prefix="/api/edit-dsl", tags=["edit-dsl"])


class SaveBody(BaseModel):
    yaml: str | None = None
    document: dict[str, Any] | None = None


class ValidateBody(BaseModel):
    yaml: str | None = None
    document: dict[str, Any] | None = None


class CreateBody(BaseModel):
    module: str = Field(min_length=1)
    file_key: str = Field(min_length=1)
    template_rel: str = ""


@router.get("/catalog")
def get_catalog(scope: str = Query(default="all")) -> dict[str, object]:
    return svc.list_catalog(module_scope=scope)


@router.get("/meta")
def get_meta() -> dict[str, object]:
    return svc.editor_meta()


@router.get("/file")
def get_file(rel: str = Query(...)) -> dict[str, object]:
    try:
        return svc.get_file(rel)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/file")
def put_file(rel: str = Query(...), body: SaveBody = ...) -> dict[str, object]:
    if body.document is None and body.yaml is None:
        raise HTTPException(status_code=400, detail="yaml or document required")
    try:
        return svc.save_file(rel, yaml_text=body.yaml, document=body.document)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/validate")
def post_validate(body: ValidateBody) -> dict[str, object]:
    if body.document is None and body.yaml is None:
        raise HTTPException(status_code=400, detail="yaml or document required")
    return svc.validate_yaml(yaml_text=body.yaml, document=body.document)


@router.get("/name-collisions")
def get_name_collisions(rel: str = Query(...), name: str = Query(default="")) -> dict[str, object]:
    return {"collisions": svc.name_collisions(rel, name)}


@router.get("/event-icon")
def get_event_icon(slug: str = Query(...)) -> FileResponse:
    path = svc.event_icon_path(slug)
    if path is None:
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(path)


@router.post("/create")
def post_create(body: CreateBody) -> dict[str, object]:
    try:
        return svc.create_file(
            module=body.module,
            file_key=body.file_key,
            template_rel=body.template_rel,
        )
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
