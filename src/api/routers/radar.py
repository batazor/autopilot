"""Radar kingdom-map runs: list / manifest / tiles, scan control, SSE events.

Serves the ``/radar`` dashboard page. Run data is plain files under
``runs_root()`` (``RADAR_RUNS_DIR`` env or ``<repo>/runs``); live progress is
bridged from the ``radar:events_stream`` Redis stream over SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from api.deps import get_redis
from config.loader import load_settings
from modules.radar.config import (
    DEFAULT_TARGET,
    RADAR_TARGETS,
    default_config_path,
    normalize_target,
    runs_root,
)
from modules.radar.events import (
    STREAM,
    RadarEventPublisher,
    read_active,
    request_stop,
    set_active,
)
from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import MAP_PREVIEW_NAME
from modules.radar.tiles import MAP_FULL_NAME, TILES_DIR_NAME, TILES_META_NAME

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/radar", tags=["radar"])

RedisDep = Annotated[redis.Redis, Depends(get_redis)]

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TILES_BUILD_KEY_FMT = "radar:tiles_building:{run_id}"
_TILES_BUILD_TTL_S = 600
_SSE_STREAM_BLOCK_MS = 1000
_SSE_HEARTBEAT_INTERVAL_S = 25.0


def _run_dir(run_id: str) -> Path:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail=f"invalid run id: {run_id!r}")
    path = runs_root() / run_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return path


def _read_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / MANIFEST_NAME
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"run {run_dir.name} has no manifest")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"manifest unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="manifest is not a JSON object")
    return data


def _run_summary(run_dir: Path) -> dict[str, Any] | None:
    try:
        manifest = _read_manifest(run_dir)
    except HTTPException:
        return None
    frames = manifest.get("frames") or {}
    timestamps = [
        float(f["ts"]) for f in frames.values() if isinstance(f, dict) and f.get("ts")
    ]
    started_at = min(timestamps) if timestamps else run_dir.stat().st_mtime
    duration_s = round(max(timestamps) - min(timestamps), 1) if len(timestamps) > 1 else 0.0
    # Runs scanned before targets existed carry no target → treat as global_map.
    raw_target = str((manifest.get("config") or {}).get("target") or DEFAULT_TARGET)
    target = raw_target if raw_target in RADAR_TARGETS else DEFAULT_TARGET
    return {
        "run_id": run_dir.name,
        "target": target,
        "started_at": started_at,
        "frames_done": len(frames),
        "frames_total": int((manifest.get("grid") or {}).get("count") or len(frames)),
        "unstable_count": sum(
            1 for f in frames.values() if isinstance(f, dict) and f.get("unstable")
        ),
        "duration_s": duration_s,
        "has_tiles": (run_dir / TILES_META_NAME).is_file(),
        "has_map": (run_dir / MAP_FULL_NAME).is_file(),
    }


@router.get("/runs")
def list_runs(target: str | None = None) -> list[dict[str, Any]]:
    """Run summaries, newest first. ``?target=`` filters to one map view."""
    want = normalize_target(target) if target else None
    root = runs_root()
    if not root.is_dir():
        return []
    summaries = [
        summary
        for child in root.iterdir()
        if child.is_dir() and (summary := _run_summary(child)) is not None
        and (want is None or summary["target"] == want)
    ]
    summaries.sort(key=lambda s: s["started_at"], reverse=True)
    return summaries


@router.get("/active")
def get_active_scan(client: RedisDep) -> dict[str, Any]:
    return {"active": read_active(client)}


@router.get("/instances")
def list_instances() -> list[dict[str, str]]:
    """Configured emulator instances for the scan target selector.

    The first entry is the default target when a scan is started without an
    explicit ``instance_id`` (matching ``start_scan``).
    """
    settings = load_settings()
    return [
        {"instance_id": inst.instance_id, "serial": inst.bluestacks_window_title, "game": inst.game}
        for inst in settings.instances
    ]


@router.get("/runs/{run_id}/manifest")
def get_manifest(run_id: str) -> JSONResponse:
    return JSONResponse(_read_manifest(_run_dir(run_id)))


@router.delete("/runs")
def delete_all_runs(client: RedisDep) -> dict[str, Any]:
    """Clear the run history. The currently scanning run (if any) is kept."""
    active = read_active(client) or {}
    active_run = str(active.get("run_id") or "")
    root = runs_root()
    deleted: list[str] = []
    skipped: list[str] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or _run_summary(child) is None:
                continue
            if child.name == active_run:
                skipped.append(child.name)
                continue
            try:
                shutil.rmtree(child)
                deleted.append(child.name)
            except OSError:
                logger.exception("radar: failed to delete run %s", child.name)
                skipped.append(child.name)
    return {"deleted": deleted, "skipped": skipped, "status": "cleared"}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, client: RedisDep) -> dict[str, str]:
    active = read_active(client) or {}
    if active.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail=f"run {run_id} is currently scanning")
    run_dir = _run_dir(run_id)
    try:
        shutil.rmtree(run_dir)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"failed to delete run {run_id}: {exc}") from exc
    return {"run_id": run_id, "status": "deleted"}


@router.get("/runs/{run_id}/tiles.json")
def get_tiles_meta(run_id: str) -> JSONResponse:
    run_dir = _run_dir(run_id)
    path = run_dir / TILES_META_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id} has no tiles yet",
            headers={"Cache-Control": "no-store"},
        )
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"tiles meta unreadable: {exc}") from exc
    # Fold in the game↔canvas affine (verified sidecar, else stitch-derived) so
    # the viewer can read out world X:Y on hover without another round-trip. The
    # affine changes after a calibration sample, so this response is uncached.
    from modules.radar.coords import coords_affine

    affine = coords_affine(run_dir)
    if affine is not None:
        meta["coords"] = affine
    return JSONResponse(meta, headers={"Cache-Control": "no-store"})


@router.get("/runs/{run_id}/tiles/{z}/{x}/{y}")
def get_tile(run_id: str, z: int, x: int, y: int) -> FileResponse:
    path = _run_dir(run_id) / TILES_DIR_NAME / str(z) / str(x) / f"{y}.png"
    if not path.is_file():
        # Cheap and uncached: out-of-bounds tiles are normal at diamond edges.
        raise HTTPException(status_code=404, headers={"Cache-Control": "no-store"})
    return FileResponse(
        path,
        media_type="image/png",
        # Tiles are immutable per run — cache hard.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/runs/{run_id}/preview.jpg")
def get_map_preview(run_id: str) -> FileResponse:
    """Latest stitched preview (long side capped). During a scan the live
    stitcher rewrites it after every frame — hence no-store."""
    path = _run_dir(run_id) / MAP_PREVIEW_NAME
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id} has no stitched preview yet",
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


class ScanRequest(BaseModel):
    instance_id: str = ""
    target: str = DEFAULT_TARGET
    # Continue the newest unfinished run for this target instead of starting a
    # fresh one (re-anchors at the origin and captures only the missing cells).
    resume: bool = False


def _latest_resumable_run(target: str) -> str | None:
    """Newest run for ``target`` that has frames but is not yet complete."""
    root = runs_root()
    if not root.is_dir():
        return None
    best: tuple[float, str] | None = None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        summary = _run_summary(child)
        if not summary or summary["target"] != target:
            continue
        if 0 < summary["frames_done"] < summary["frames_total"] and (
            best is None or summary["started_at"] > best[0]
        ):
            best = (summary["started_at"], summary["run_id"])
    return best[1] if best else None


def _scan_grid_preview(config_path: Path, target: str) -> list[dict[str, int]]:
    from modules.radar.config import load_config
    from modules.radar.scanner import build_scan_grid

    cfg = load_config(config_path, target=target)
    grid = build_scan_grid(cfg)
    return [{"ix": p.ix, "iy": p.iy} for p in grid]


def _radar_config_path(target: str = DEFAULT_TARGET) -> Path:
    return default_config_path(target)


def _run_scan_now_blocking(
    run_id: str, instance_id: str, target: str, client: redis.Redis
) -> None:
    """Run a radar scan immediately from the API process, outside the bot queue."""
    from modules.radar.live import live_stitching
    from modules.radar.scanner import run_scan
    from modules.radar.stitch import run_stitch
    from modules.radar.tiles import generate_tiles

    settings = load_settings()
    serial = next(
        (
            inst.bluestacks_window_title
            for inst in settings.instances
            if inst.instance_id == instance_id
        ),
        None,
    )
    out_dir = runs_root() / run_id
    publisher = RadarEventPublisher(client, run_id, target)
    try:
        with live_stitching(out_dir, publisher):
            run_scan(
                _radar_config_path(target),
                out_dir,
                serial=serial,
                adb_bin=settings.worker.adb_executable or "adb",
                events=publisher,
                target=target,
            )
    except Exception as exc:
        # ``run_scan`` reports failures after scan_started; this branch also
        # covers earlier setup failures (config/ADB/device discovery).
        active = read_active(client) or {}
        if active.get("run_id") == run_id:
            publisher.scan_failed(str(exc))
        logger.exception("radar scan failed (run %s)", run_id)
        return

    try:
        run_stitch(out_dir)
        generate_tiles(out_dir)
        publisher.tiles_ready()
    except Exception:
        # Frames + manifest are safe on disk; tiles can be rebuilt from the UI.
        logger.exception("radar stitch/tiles failed for run %s", run_id)

    if target != DEFAULT_TARGET:
        # Raster targets (city/island) carry building name plates — extract the
        # name→position registry for the navigator. Best-effort: a missing
        # tesseract or OCR hiccup must not fail an otherwise good scan.
        try:
            from modules.radar.labels import build_registry

            reg = build_registry(out_dir)
            logger.info("radar: %s registry — %d buildings (run %s)", target, reg["count"], run_id)
        except Exception:
            logger.exception("radar building registry failed for run %s", run_id)


@router.post("/scan")
def start_scan(
    background: BackgroundTasks,
    client: RedisDep,
    body: ScanRequest | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    if not settings.instances:
        raise HTTPException(status_code=503, detail="no instances configured")
    instance_id = (body.instance_id if body else "").strip() or settings.instances[0].instance_id
    if instance_id not in {i.instance_id for i in settings.instances}:
        raise HTTPException(status_code=404, detail=f"unknown instance {instance_id!r}")
    try:
        target = normalize_target(body.target if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config_path = _radar_config_path(target)
    if not config_path.is_file():
        raise HTTPException(
            status_code=409,
            detail=(
                f"radar config not found: {config_path} — "
                f"create src/modules/radar/{config_path.name} first"
            ),
        )
    try:
        grid = _scan_grid_preview(config_path, target)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"radar config invalid: {exc}") from exc

    resume = bool(body and body.resume)
    if resume:
        run_id = _latest_resumable_run(target)
        if run_id is None:
            raise HTTPException(
                status_code=409,
                detail=f"no unfinished {target} run to resume — start a fresh scan instead",
            )
    else:
        run_id = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M%S")
    if not set_active(
        client, run_id, "scanning",
        total=len(grid), grid=grid, target=target, only_if_absent=True,
    ):
        active = read_active(client) or {}
        raise HTTPException(
            status_code=409,
            detail=(
                f"a radar scan is already {active.get('status', 'active')} "
                f"(run {active.get('run_id', '?')}, target {active.get('target', '?')}, "
                f"{active.get('done', 0)}/{active.get('total', 0)} frames)"
            ),
        )
    background.add_task(_run_scan_now_blocking, run_id, instance_id, target, client)
    logger.info(
        "radar scan %s (%s) %s for instance %s",
        run_id, target, "resumed" if resume else "started immediately", instance_id,
    )
    return {
        "run_id": run_id,
        "instance_id": instance_id,
        "target": target,
        "total_frames": len(grid),
        "grid": grid,
        "resumed": resume,
    }


@router.post("/corner-ref")
def calibrate_corner_ref(body: ScanRequest | None = None) -> dict[str, Any]:
    """Record the corner reference from the CURRENT screen.

    The operator pans the camera so the bottom-corner X is clearly visible,
    then triggers this. The reading (crossing position, minimap-rect reading
    at the corner, dark fraction) is saved to the sidecar next to the radar
    config and used by the origin servo to verify/align at the pan clamp.
    """
    from modules.radar.config import load_config, save_corner_ref
    from modules.radar.device import RadarDevice, pick_serial
    from modules.radar.scanner import capture_corner_reference

    settings = load_settings()
    if not settings.instances:
        raise HTTPException(status_code=503, detail="no instances configured")
    instance_id = (body.instance_id if body else "").strip() or settings.instances[0].instance_id
    try:
        target = normalize_target(body.target if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    serial = next(
        (
            inst.bluestacks_window_title
            for inst in settings.instances
            if inst.instance_id == instance_id
        ),
        None,
    )
    cfg = load_config(_radar_config_path(target), target=target)
    adb_bin = settings.worker.adb_executable or cfg.adb_bin or "adb"
    device = RadarDevice(serial or cfg.device_serial or pick_serial(adb_bin), adb_bin)
    frame = device.capture()
    try:
        ref = capture_corner_reference(frame, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    path = save_corner_ref(ref, target)
    logger.info("radar corner reference (%s) recorded: %s -> %s", target, ref.model_dump(), path)
    return {"corner_ref": ref.model_dump(), "target": target, "path": str(path)}


class MarkCornersRequest(BaseModel):
    # Canvas-pixel click per kingdom vertex on the displayed stitched map:
    # {"top":[x,y], "right":[x,y], "bottom":[x,y], "left":[x,y]}.
    corners: dict[str, tuple[float, float]]


def _restitch_corners_blocking(run_id: str, run_dir: Path) -> None:
    """Re-stitch with the corner constraints (grid bundle-adjusted) + re-tile."""
    from modules.radar.stitch import run_stitch
    from modules.radar.tiles import generate_tiles

    client = get_redis()
    try:
        run_stitch(run_dir)  # reads corners.json → corner-pinned solve
        generate_tiles(run_dir)
        RadarEventPublisher(client, run_id).tiles_ready()
        logger.info("radar: corner-pinned re-stitch done for run %s", run_id)
    except Exception:
        logger.exception("radar corner re-stitch failed for run %s", run_id)
    finally:
        try:
            client.delete(_TILES_BUILD_KEY_FMT.format(run_id=run_id))
        except Exception:
            logger.debug("radar: tile-build key cleanup failed", exc_info=True)


@router.post("/runs/{run_id}/corners", status_code=202)
def mark_corners(
    run_id: str, body: MarkCornersRequest, client: RedisDep, background: BackgroundTasks
) -> dict[str, Any]:
    """Pin the run's coordinate grid to operator-marked kingdom corners.

    The operator clicks the four diamond vertices on the stitched map; each
    click's game coordinate is known exactly (a fixed ``game_size`` square). The
    clicks become per-frame constraints and the run is re-stitched with the grid
    bundle-adjusted onto the square game lattice (drift spread out), then
    re-tiled. Completion is announced as ``tiles_ready`` on the SSE stream.
    """
    run_dir = _run_dir(run_id)
    manifest = _read_manifest(run_dir)
    game_size = int((manifest.get("config") or {}).get("game_size") or 1200)
    fsize = manifest.get("frame_size") or {}
    fw, fh = int(fsize.get("w") or 720), int(fsize.get("h") or 1280)
    from modules.radar.corners import save_corners

    try:
        sidecar = save_corners(
            run_dir,
            {k: (float(v[0]), float(v[1])) for k, v in body.corners.items()},
            game_size=game_size, frame_w=fw, frame_h=fh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    key = _TILES_BUILD_KEY_FMT.format(run_id=run_id)
    if not client.set(key, "1", nx=True, ex=_TILES_BUILD_TTL_S):
        raise HTTPException(status_code=409, detail=f"run {run_id} is already re-stitching")
    background.add_task(_restitch_corners_blocking, run_id, run_dir)
    return {"run_id": run_id, "corners": len(sidecar["corners"]), "status": "pinning"}


@router.post("/scan/stop")
def stop_scan(client: RedisDep) -> dict[str, Any]:
    """Ask the running scan to stop after its current frame (partial map kept)."""
    active = read_active(client)
    if not active or not active.get("run_id"):
        raise HTTPException(status_code=409, detail="no radar scan is currently running")
    run_id = str(active["run_id"])
    request_stop(client, run_id)
    logger.info("radar scan %s stop requested", run_id)
    return {"run_id": run_id, "status": "stopping"}


def _build_tiles_blocking(run_id: str, run_dir: Path) -> None:
    from modules.radar.stitch import run_stitch
    from modules.radar.tiles import generate_tiles

    client = get_redis()
    try:
        if not (run_dir / MAP_FULL_NAME).is_file():
            run_stitch(run_dir)
        generate_tiles(run_dir)
        RadarEventPublisher(client, run_id).tiles_ready()
        logger.info("radar tiles built for run %s", run_id)
    except Exception:
        logger.exception("radar tile build failed for run %s", run_id)
    finally:
        try:
            client.delete(_TILES_BUILD_KEY_FMT.format(run_id=run_id))
        except Exception:
            logger.debug("radar: tile-build key cleanup failed", exc_info=True)


@router.post("/runs/{run_id}/tiles", status_code=202)
def build_tiles(run_id: str, client: RedisDep, background: BackgroundTasks) -> dict[str, str]:
    """Stitch + tile an existing run in the background (for runs scanned before
    tiling existed, or after a stitch failure). Completion is announced as a
    ``tiles_ready`` event on the SSE stream."""
    run_dir = _run_dir(run_id)
    _read_manifest(run_dir)  # 404 for directories that aren't runs
    if not list(run_dir.glob("frame_*.png")):
        raise HTTPException(status_code=409, detail=f"run {run_id} has no frames to stitch")
    key = _TILES_BUILD_KEY_FMT.format(run_id=run_id)
    if not client.set(key, "1", nx=True, ex=_TILES_BUILD_TTL_S):
        raise HTTPException(status_code=409, detail=f"tiles for {run_id} are already building")
    background.add_task(_build_tiles_blocking, run_id, run_dir)
    return {"run_id": run_id, "status": "building"}


@router.post("/city-map")
def assemble_city_map_endpoint() -> dict[str, Any]:
    """Fuse every scanned main_city chunk into the persistent ``citymap`` the
    navigator routes over — the 'assemble base map' operator action."""
    from modules.radar.citymap import assemble_city_map

    try:
        return assemble_city_map(runs_root())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# SSE bridge: radar:events_stream → browser
# ---------------------------------------------------------------------------


def _sse_data(payload: dict[str, Any], *, event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _normalize_last_event_id(value: str | None) -> str:
    if not value:
        return "$"
    return value if re.match(r"^\d+-\d+$", value) else "$"


def _redis_text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _read_stream(client: redis.Redis, last_id: str, *, count: int = 50) -> tuple[str, list[str]]:
    """Block for pending radar stream events and convert them to SSE frames.

    Runs in a worker thread; Redis wakes it as soon as the scanner writes the
    next event, so progress is delivered per frame without periodic active-key
    polling.
    """
    out: list[str] = []
    rows = client.xread({STREAM: last_id}, count=count, block=_SSE_STREAM_BLOCK_MS)
    for _stream_name, entries in rows:
        for entry_id, fields in entries:
            last_id = _redis_text(entry_id)
            if not isinstance(fields, dict):
                continue
            raw = fields.get("data") or fields.get(b"data")
            if not raw:
                continue
            try:
                data = json.loads(_redis_text(raw))
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                continue
            if isinstance(data, dict):
                out.append(_sse_data(data, event_id=last_id))
    return last_id, out


@router.get("/events")
async def radar_events(request: Request, client: RedisDep) -> StreamingResponse:
    """One-directional live channel for the /radar page (no WebSockets).

    Starts with a ``scan_active`` snapshot so a page opened mid-scan can
    restore its progress state immediately, then forwards every stream event
    as soon as the scanner writes it.
    """

    async def body() -> AsyncIterator[str]:
        active = await asyncio.to_thread(read_active, client)
        yield _sse_data({"type": "scan_active", "active": active})

        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()
        last_id = _normalize_last_event_id(request.headers.get("last-event-id"))
        while not await request.is_disconnected():
            try:
                last_id, lines = await asyncio.to_thread(_read_stream, client, last_id)
            except (redis.RedisError, OSError):
                logger.warning("radar events stream read failed", exc_info=True)
                await asyncio.sleep(1.0)
                continue

            for line in lines:
                yield line
            if lines:
                last_heartbeat = loop.time()
            elif loop.time() - last_heartbeat >= _SSE_HEARTBEAT_INTERVAL_S:
                last_heartbeat = loop.time()
                yield ": heartbeat\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            # no-transform: the Next.js production server (`next start`) gzips
            # proxied responses, and its compression middleware buffers SSE
            # until the zlib window fills — events would arrive minutes late.
            # Compression skips responses marked no-transform.
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
