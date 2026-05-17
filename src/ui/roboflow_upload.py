"""Roboflow upload helper for the labeling UI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from config.env_loader import load_env_once
from layout.area_regions import is_auxiliary_overlay_region
from layout.area_versions import get_version_block


@dataclass(frozen=True)
class RoboflowUploadConfig:
    api_key: str
    workspace: str | None
    project: str
    batch_name: str
    split: str | None
    tag_names: list[str] | None
    is_prediction: bool
    num_retry_uploads: int | None


def default_roboflow_batch_name(today: date | None = None) -> str:
    day = today or datetime.now(tz=UTC).date()
    return f"screenshots-{day.isoformat()}"


def _truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _optional_int_env(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    return int(raw)


def _optional_csv_env(name: str) -> list[str] | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return values or None  # ty: ignore[invalid-return-type]


def load_roboflow_upload_config() -> tuple[RoboflowUploadConfig | None, list[str]]:
    load_env_once()
    values = {
        "ROBOFLOW_API_KEY": (os.environ.get("ROBOFLOW_API_KEY") or "").strip(),
        "ROBOFLOW_PROJECT": (os.environ.get("ROBOFLOW_PROJECT") or "").strip(),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        return None, missing

    batch_name = (os.environ.get("ROBOFLOW_BATCH_NAME") or "").strip()
    split = (os.environ.get("ROBOFLOW_SPLIT") or "").strip() or None
    return (
        RoboflowUploadConfig(
            api_key=values["ROBOFLOW_API_KEY"],
            workspace=(os.environ.get("ROBOFLOW_WORKSPACE") or "").strip() or None,
            project=values["ROBOFLOW_PROJECT"],
            batch_name=batch_name or default_roboflow_batch_name(),
            split=split,
            tag_names=_optional_csv_env("ROBOFLOW_TAG_NAMES"),
            is_prediction=_truthy_env("ROBOFLOW_IS_PREDICTION"),
            num_retry_uploads=_optional_int_env("ROBOFLOW_NUM_RETRY_UPLOADS"),
        ),
        [],
    )


def _regions_for_active_version(entry: dict[str, Any], active_version: str | None) -> list[dict[str, Any]]:
    base_regions = [r for r in entry.get("regions") or [] if isinstance(r, dict)]
    ver_block = get_version_block(entry, active_version)
    if ver_block is None:
        return base_regions

    removed = {
        str(name).strip()
        for name in ver_block.get("removed") or []
        if isinstance(name, str) and str(name).strip()
    }
    version_regions = [r for r in ver_block.get("regions") or [] if isinstance(r, dict)]
    version_by_name = {
        str(r.get("name", "") or "").strip(): r
        for r in version_regions
        if str(r.get("name", "") or "").strip()
    }

    merged: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for base_region in base_regions:
        name = str(base_region.get("name", "") or "").strip()
        if not name or name in removed:
            continue
        region = version_by_name.get(name, base_region)
        merged.append(region)
        emitted.add(name)

    for version_region in version_regions:
        name = str(version_region.get("name", "") or "").strip()
        if name and name not in emitted and name not in removed:
            merged.append(version_region)
            emitted.add(name)

    return merged


def build_coco_annotation(
    *,
    image_path: Path,
    image_rel: str,
    entry: dict[str, Any],
    active_version: str | None = None,
) -> dict[str, Any]:
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is not installed; run `uv sync`.") from exc

    with Image.open(image_path) as image:
        width, height = image.size

    regions = [
        region
        for region in _regions_for_active_version(entry, active_version)
        if not is_auxiliary_overlay_region(region) and isinstance(region.get("bbox"), dict)
    ]
    if not regions:
        raise ValueError("selected screenshot has no non-auxiliary bbox annotations")

    category_ids: dict[str, int] = {}
    annotations: list[dict[str, Any]] = []
    for idx, region in enumerate(regions, start=1):
        name = str(region.get("name", "") or "").strip() or f"region_{idx}"
        category_id = category_ids.setdefault(name, len(category_ids) + 1)
        bbox = region["bbox"]
        x = float(bbox.get("x", 0.0)) / 100.0 * width
        y = float(bbox.get("y", 0.0)) / 100.0 * height
        w = float(bbox.get("width", 0.0)) / 100.0 * width
        h = float(bbox.get("height", 0.0)) / 100.0 * height
        if w <= 0 or h <= 0:
            continue
        annotations.append(
            {
                "id": len(annotations) + 1,
                "image_id": 1,
                "category_id": category_id,
                "bbox": [x, y, w, h],
                "area": w * h,
                "iscrowd": 0,
                "segmentation": [],
            }
        )

    if not annotations:
        raise ValueError("selected screenshot has no valid bbox annotations")

    return {
        "images": [
            {
                "id": 1,
                "file_name": Path(image_rel).name,
                "width": width,
                "height": height,
            }
        ],
        "annotations": annotations,
        "categories": [
            {"id": category_id, "name": name, "supercategory": "ui"}
            for name, category_id in sorted(category_ids.items(), key=lambda item: item[1])
        ],
    }


def upload_screenshot_to_roboflow(
    path: Path,
    config: RoboflowUploadConfig,
    *,
    annotation: dict[str, Any],
) -> Any:
    if not path.is_file():
        raise FileNotFoundError(path)

    try:
        from roboflow import Roboflow
    except ImportError as exc:
        raise RuntimeError("roboflow package is not installed; run `uv sync`.") from exc

    rf = Roboflow(api_key=config.api_key)
    workspace = rf.workspace(config.workspace) if config.workspace else rf.workspace()
    project = workspace.project(config.project)

    upload_kwargs: dict[str, Any] = {
        "image_path": str(path),
        "batch_name": config.batch_name,
    }
    if config.split:
        upload_kwargs["split"] = config.split
    if config.tag_names:
        upload_kwargs["tag_names"] = config.tag_names
    if config.is_prediction:
        upload_kwargs["is_prediction"] = True
    if config.num_retry_uploads is not None:
        upload_kwargs["num_retry_uploads"] = config.num_retry_uploads

    with TemporaryDirectory() as tmp_dir:
        annotation_path = Path(tmp_dir) / "_annotations.coco.json"
        annotation_path.write_text(
            json.dumps(annotation, ensure_ascii=False),
            encoding="utf-8",
        )
        upload_kwargs["annotation_path"] = str(annotation_path)
        return project.single_upload(**upload_kwargs)
