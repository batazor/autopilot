"""DSL exec handler: read stamina from the green fill-bar under the avatar.

On main_city / main_world a green bar under the commander avatar shows stamina.
We measure its fill ratio (fraction of the track that's green) and store
`stamina = round(ratio * cap)`. Cheaper and far more frequent than OCR — those
home screens are where the bot idles most — and it self-validates, so running
it off those screens is a harmless no-op (the bbox won't look like a bar).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from layout.area_lookup import screen_region_by_name
from tasks import dsl_runtime

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# A column counts as filled if it's bright green (BGR ~ (0,194,38)); as empty
# track if near-black (BGR ~ (0,0,13)). Everything else (frame border, wrong
# screen) is "neither" — if too few columns are bar-like we bail (not a bar).
_MIN_TRACK_FRACTION = 0.6


def bar_fill_ratio(crop: np.ndarray | None) -> float | None:
    """Green-fill fraction of a stamina bar crop, or ``None`` if it doesn't
    look like a bar (so callers can skip writing on the wrong screen)."""
    if crop is None or crop.size == 0 or crop.ndim != 3:
        return None
    b = crop[:, :, 0].astype(int)
    g = crop[:, :, 1].astype(int)
    r = crop[:, :, 2].astype(int)
    green = (g > 120) & (g > r + 40) & (g > b + 40)
    dark = np.maximum(np.maximum(b, g), r) < 60
    # Per-column majority vote (robust to a 1px anti-aliased edge).
    green_col = green.mean(axis=0) > 0.5
    dark_col = dark.mean(axis=0) > 0.5
    total = int(crop.shape[1])
    track_n = int((green_col | dark_col).sum())
    green_n = int(green_col.sum())
    # Not a bar: too few bar-like columns, or no green at all (an all-dark patch
    # on the wrong screen would otherwise read as a false "0"). A genuinely
    # empty bar reads as None too — rare, and OCR / the last estimate cover it.
    if total == 0 or track_n < _MIN_TRACK_FRACTION * total or green_n == 0:
        return None
    return max(0.0, min(1.0, green_n / track_n))


def _load_area_doc() -> dict[str, Any]:
    from config.paths import repo_root
    from layout.area_manifest import load_area_doc

    try:
        return load_area_doc(repo_root(), game="wos")
    except Exception:
        logger.exception("stamina bar: area doc load failed")
        return {}


def _budget_cap(override: Any) -> int:
    if override not in (None, ""):
        try:
            return int(override)
        except (TypeError, ValueError):
            pass
    try:
        from games.wos.core.stamina.adapter import load_budget

        return load_budget().cap
    except Exception:
        return 200


async def _exec_read_stamina_bar(ctx: DslExecContext) -> None:
    """Measure the avatar stamina bar and store the estimate. Args: ``region``
    (default ``stamina.bar``), ``cap`` (default from budget.yaml)."""
    region_name = str((ctx.args or {}).get("region") or "stamina.bar").strip()
    area_doc = _load_area_doc()
    pair = screen_region_by_name(area_doc, region_name) if area_doc else None
    bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
    if not isinstance(bbox, dict):
        ctx.result.update({"action": "unknown_region", "region": region_name})
        return

    actions = dsl_runtime.bot_actions()
    try:
        image = await asyncio.to_thread(actions.capture_screen_bgr, ctx.instance_id)
    except Exception:
        logger.exception("stamina bar: capture failed instance=%s", ctx.instance_id)
        ctx.result.update({"action": "capture_failed"})
        return

    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    ratio = bar_fill_ratio(image[py : py + ph, px : px + pw])
    if ratio is None:
        ctx.result.update({"action": "not_a_bar"})   # wrong screen / no bar
        return

    cap = _budget_cap((ctx.args or {}).get("cap"))
    stamina = round(ratio * cap)
    if ctx.redis_client is None or not ctx.player_id:
        ctx.result.update({"action": "no_target", "stamina": stamina})
        return
    try:
        await ctx.redis_client.hset(
            f"wos:player:{ctx.player_id}:state",
            mapping={
                "stamina": str(stamina),
                "stamina_at": str(time.time()),
                "stamina_ratio": f"{ratio:.4f}",
                "stamina_source": "bar",
            },
        )
    except Exception:
        logger.exception("stamina bar: hset failed player=%s", ctx.player_id)
        return
    ctx.result.update({"action": "measured", "stamina": stamina, "ratio": round(ratio, 4)})
    logger.info(
        "stamina bar: player=%s ratio=%.3f stamina=%d", ctx.player_id, ratio, stamina
    )


DSL_EXEC_HANDLERS = {
    "read_stamina_bar": _exec_read_stamina_bar,
}
