from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

import cv2  # type: ignore[import-untyped]
import httpx
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from config.loader import Settings
from layout.types import Region

logger = logging.getLogger(__name__)

# Highlight only the region **name** (region_id) in terminal OCR logs; bbox/conf/text stay plain.
_OCR_YELLOW = "\033[33m"
_OCR_RESET = "\033[0m"


def _ocr_tty_yellow_name(name: str) -> str:
    err = sys.stderr
    if not name or not getattr(err, "isatty", lambda: False)():
        return name
    return f"{_OCR_YELLOW}{name}{_OCR_RESET}"


@dataclass(frozen=True)
class OCRResult:
    region_id: str
    text: str
    confidence: float
    # Populated when the OCR backend reported a per-region failure (e.g. the
    # paddle call raised). Callers can use this to distinguish "no text" from
    # "OCR died" and decide whether to retry / skip / surface to the user.
    error: str | None = None


class OcrClient:
    _clients: ClassVar[dict[tuple[str, float, int], httpx.AsyncClient]] = {}

    # Content-addressed cache: BLAKE2b of the bbox patch bytes → (ts, text, conf).
    # Pixels identical ⇒ OCR result identical, so the patch hash itself is a
    # correctness boundary (no need to invalidate on tap/swipe — pixel changes
    # already produce a different key).  TTL caps memory growth and is a safety
    # net against any edge case the hash misses.
    _OCR_CACHE_TTL_S: ClassVar[float] = 2.0
    _OCR_CACHE_MAX: ClassVar[int] = 256
    _cache: ClassVar[OrderedDict[bytes, tuple[float, str, float]]] = OrderedDict()

    # Switch to ``/ocr_crops`` (per-region encoded PNGs) when the total area
    # of unique crops to send is at most this fraction of the full frame.
    # Above the threshold, individual PNG headers + base64 overhead per crop
    # outweigh the savings vs one full-frame PNG that the backend slices.
    # Tuned for the typical 720x1280 framebuffer: countdown timers / stat
    # cells / chapter-task strips are <5% of the frame and benefit hugely;
    # full-page reads (hero grid, settings panels) stay on ``/ocr``.
    _OCR_CROPS_AREA_THRESHOLD: ClassVar[float] = 0.5

    # Backwards-compat shim: if the backend at ``settings.ocr.url`` is older
    # than this client and doesn't know ``/ocr_crops`` yet, the first 404
    # latches this flag for the rest of the process so subsequent requests
    # skip the doomed crops endpoint and go straight to ``/ocr``. A new
    # service deploy needs the bot process restart to re-enable crops mode.
    _crops_endpoint_unavailable: ClassVar[bool] = False

    @classmethod
    def _patch_hash(
        cls,
        image: np.ndarray,
        region: Region,
        *,
        preprocess: str | None = None,
    ) -> bytes:
        hi, wi = int(image.shape[0]), int(image.shape[1])
        x1 = max(0, min(int(region.x), wi))
        y1 = max(0, min(int(region.y), hi))
        x2 = max(x1, min(int(region.x + region.w), wi))
        y2 = max(y1, min(int(region.y + region.h), hi))
        patch = image[y1:y2, x1:x2]
        h = hashlib.blake2b(digest_size=16)
        h.update(np.ascontiguousarray(patch).tobytes())
        # Fold the preprocess tag into the cache key so identical pixels run
        # through different pipelines occupy distinct entries — otherwise the
        # raw-crop result would overwrite the enhanced-crop one (or vice versa)
        # and the next request would serve the wrong text.  An empty/None tag
        # is the historical "raw passthrough" path and stays binary-identical
        # to the pre-preprocess hash.
        pre_tag = (preprocess or "").strip().lower()
        if pre_tag:
            h.update(b"|preprocess=")
            h.update(pre_tag.encode("utf-8"))
        return h.digest()

    @classmethod
    def _cache_get(cls, key: bytes) -> tuple[str, float] | None:
        entry = cls._cache.get(key)
        if entry is None:
            return None
        ts, text, conf = entry
        if (time.monotonic() - ts) > cls._OCR_CACHE_TTL_S:
            cls._cache.pop(key, None)
            return None
        cls._cache.move_to_end(key)
        return text, conf

    @classmethod
    def _cache_put(cls, key: bytes, text: str, confidence: float) -> None:
        cls._cache[key] = (time.monotonic(), text, confidence)
        cls._cache.move_to_end(key)
        while len(cls._cache) > cls._OCR_CACHE_MAX:
            cls._cache.popitem(last=False)

    @classmethod
    def clear_cache(cls) -> None:
        """Drop all cached OCR results — for tests and explicit recovery paths."""
        cls._cache.clear()

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.ocr.url
        self._timeout = float(settings.ocr.timeout_seconds)

    def _client_key(self) -> tuple[str, float, int]:
        return (self._base_url, self._timeout, id(asyncio.get_running_loop()))

    async def _http_client(self) -> httpx.AsyncClient:
        key = self._client_key()
        client = self._clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(timeout=self._timeout)
            self._clients[key] = client
        return client

    async def aclose(self) -> None:
        client = self._clients.pop(self._client_key(), None)
        if client is not None and not client.is_closed:
            await client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
    async def ocr_regions(
        self,
        image: np.ndarray,
        regions: list[Region],
        *,
        region_ids: list[str] | None = None,
        region_preprocess: list[str | None] | None = None,
    ) -> list[OCRResult]:
        def _rid(i: int) -> str:
            if region_ids is not None and i < len(region_ids):
                s = str(region_ids[i] or "").strip()
                if s:
                    return s
            return f"r{i}"

        def _pre(i: int) -> str:
            """Normalized preprocess tag for slot ``i`` — lowercase or empty."""
            if region_preprocess is None or i >= len(region_preprocess):
                return ""
            raw = region_preprocess[i]
            if raw is None:
                return ""
            return str(raw).strip().lower()

        # Pre-fill from the content-addressed cache.  Only regions whose bbox
        # patch has not been OCR'd recently are sent to the backend; identical
        # pixels return the prior result instantly.
        results: list[OCRResult | None] = [None] * len(regions)
        miss_indices: list[int] = []
        miss_keys: list[bytes] = []
        for i, region in enumerate(regions):
            key = self._patch_hash(image, region, preprocess=_pre(i) or None)
            hit = self._cache_get(key)
            if hit is not None:
                text, conf = hit
                results[i] = OCRResult(region_id=_rid(i), text=text, confidence=conf)
            else:
                miss_indices.append(i)
                miss_keys.append(key)

        if not miss_indices:
            # All regions served from the in-process patch-hash cache (no HTTP).
            # Verbose at default log levels — use DEBUG (e.g. ``LOGLEVEL=DEBUG``)
            # when investigating cache behavior.
            try:
                ts = datetime.now().strftime("%H:%M:%S")
                if len(regions) == 1:
                    r = regions[0]
                    extra = f" bbox=({r.x},{r.y},{r.w}x{r.h})"
                else:
                    extra = f" batch={len(regions)}"
                logger.debug(
                    "OCR ts=%s ms=0 cached%s — %d hit",
                    ts,
                    extra,
                    len(regions),
                )
            except Exception:
                pass
            return [r for r in results if r is not None]

        # Within-batch dedup by patch hash. The TTL cache above only collapses
        # repeats *across* calls — on the first scan of e.g. ``screen_verify.yaml``
        # the cache is cold, and the 141 ``page.heroes.unit.name`` cells with
        # identical pixels all fan out to the backend. Group misses by patch
        # hash, send one representative per unique key, fan the response back
        # out to every slot that shares the hash.
        key_to_fanout: dict[bytes, list[int]] = {}
        unique_keys: list[bytes] = []
        for idx, key in zip(miss_indices, miss_keys, strict=True):
            bucket = key_to_fanout.get(key)
            if bucket is None:
                key_to_fanout[key] = [idx]
                unique_keys.append(key)
            else:
                bucket.append(idx)
        rep_indices = [key_to_fanout[k][0] for k in unique_keys]

        # Choose endpoint: ``/ocr_crops`` (encode each crop separately) when
        # the unique crops we'd send cover only a small fraction of the
        # frame; otherwise the original ``/ocr`` (one full-frame PNG +
        # bboxes, backend slices). Decision is purely on transmitted-bytes
        # economics — both endpoints produce the same response schema.
        hi_full, wi_full = int(image.shape[0]), int(image.shape[1])
        full_area = hi_full * wi_full
        clamped: list[tuple[int, int, int, int]] = []
        total_crop_area = 0
        for idx in rep_indices:
            r = regions[idx]
            cx1 = max(0, min(int(r.x), wi_full))
            cy1 = max(0, min(int(r.y), hi_full))
            cx2 = max(cx1, min(int(r.x + r.w), wi_full))
            cy2 = max(cy1, min(int(r.y + r.h), hi_full))
            clamped.append((cx1, cy1, cx2, cy2))
            total_crop_area += (cx2 - cx1) * (cy2 - cy1)
        use_crops = (
            not self._crops_endpoint_unavailable
            and full_area > 0
            and (total_crop_area / full_area) < self._OCR_CROPS_AREA_THRESHOLD
        )

        if use_crops:
            crop_payloads: list[dict[str, object]] = []
            for pos, idx in enumerate(rep_indices):
                cx1, cy1, cx2, cy2 = clamped[pos]
                crop = image[cy1:cy2, cx1:cx2]
                ok, cbuf = cv2.imencode(".png", crop)
                if not ok or cbuf is None:
                    raise RuntimeError(
                        f"cv2.imencode('.png', crop) failed for region_id={_rid(idx)!r}"
                    )
                entry: dict[str, object] = {
                    "region_id": _rid(idx),
                    "image_b64": base64.b64encode(cbuf.tobytes()).decode(),
                }
                # ``preprocess`` is opt-in per region. Omit the key entirely
                # when not set so older backends that don't know the field
                # still accept the payload (Pydantic ignores unknown extras
                # but newer schemas reject typos — safer to not emit nulls).
                pre = _pre(idx)
                if pre:
                    entry["preprocess"] = pre
                crop_payloads.append(entry)
            request_url = f"{self._base_url}/ocr_crops"
            request_json: dict[str, object] = {"regions": crop_payloads}
        else:
            ok, buf = cv2.imencode(".png", image)
            if not ok or buf is None:
                raise RuntimeError("cv2.imencode('.png', image) failed")
            image_b64 = base64.b64encode(buf.tobytes()).decode()
            region_payloads: list[dict[str, object]] = []
            for idx in rep_indices:
                entry: dict[str, object] = {
                    "region_id": _rid(idx),
                    "x": regions[idx].x,
                    "y": regions[idx].y,
                    "w": regions[idx].w,
                    "h": regions[idx].h,
                }
                pre = _pre(idx)
                if pre:
                    entry["preprocess"] = pre
                region_payloads.append(entry)
            request_url = f"{self._base_url}/ocr"
            request_json = {"image_b64": image_b64, "regions": region_payloads}

        client = await self._http_client()
        t0 = time.perf_counter()
        resp = await client.post(request_url, json=request_json)
        # Backend that predates ``/ocr_crops`` returns 404. Retry the same
        # logical OCR call against ``/ocr`` and latch the unavailability so
        # the rest of the process stops paying the 404 round-trip.
        if use_crops and resp.status_code == 404:
            logger.info(
                "OCR: ``/ocr_crops`` returned 404 — backend predates crops "
                "support, falling back to ``/ocr`` for the rest of this process"
            )
            type(self)._crops_endpoint_unavailable = True
            use_crops = False
            ok, buf = cv2.imencode(".png", image)
            if not ok or buf is None:
                raise RuntimeError("cv2.imencode('.png', image) failed")
            image_b64 = base64.b64encode(buf.tobytes()).decode()
            region_payloads = [
                {
                    "region_id": _rid(idx),
                    "x": regions[idx].x,
                    "y": regions[idx].y,
                    "w": regions[idx].w,
                    "h": regions[idx].h,
                }
                for idx in rep_indices
            ]
            resp = await client.post(
                f"{self._base_url}/ocr",
                json={"image_b64": image_b64, "regions": region_payloads},
            )
        elapsed_ms = 1000.0 * (time.perf_counter() - t0)
        resp.raise_for_status()

        raw_results = resp.json()
        items = raw_results if isinstance(raw_results, list) else []
        # Stitch HTTP responses back by ``region_id``. The backend echoes the
        # region_id we sent — trusting array order silently mis-attributes
        # results when the backend reorders, drops, or adds entries, and that
        # mis-attribution warms the wrong cache key.
        idx_by_rid: dict[str, int] = {_rid(idx): idx for idx in rep_indices}
        key_by_rid: dict[str, bytes] = {
            _rid(rep_indices[pos]): unique_keys[pos] for pos in range(len(rep_indices))
        }
        seen_rids: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            rid = str(item.get("region_id") or "").strip()
            rep_idx = idx_by_rid.get(rid)
            if rep_idx is None:
                logger.warning("OCR returned unknown region_id=%r", rid)
                continue
            if rid in seen_rids:
                logger.warning("OCR returned duplicate region_id=%r", rid)
                continue
            seen_rids.add(rid)
            text = str(item.get("text") or "")
            conf_raw = item.get("confidence")
            try:
                conf = float(conf_raw) if conf_raw is not None else 0.0
            except (TypeError, ValueError):
                conf = 0.0
            err_raw = item.get("error")
            err = str(err_raw) if err_raw else None
            if err:
                logger.warning("OCR backend error region_id=%r err=%s", rid, err)
            # Fan out to every input slot that hashed to this patch — identical
            # pixels share the OCR verdict. Each fanout slot keeps its own
            # caller-supplied ``region_id`` so downstream logs stay readable.
            for fanout_idx in key_to_fanout[key_by_rid[rid]]:
                results[fanout_idx] = OCRResult(
                    region_id=_rid(fanout_idx), text=text, confidence=conf, error=err
                )
            # Don't cache backend errors — caching a transient failure would
            # mask a recovered backend until the entry's TTL expires.
            if not err:
                self._cache_put(key_by_rid[rid], text, conf)

        missing_rids = [rid for rid in idx_by_rid if rid not in seen_rids]
        if missing_rids:
            logger.warning("OCR response missing region_ids=%s", missing_rids)

        try:
            max_shown = 6
            parts: list[str] = []
            for item in items[:max_shown]:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("region_id") or "").strip()
                txt = str(item.get("text") or "").strip().replace("\n", " ")
                conf = item.get("confidence")
                conf_f = float(conf) if isinstance(conf, (int, float, str)) and str(conf) else 0.0
                if len(txt) > 80:
                    txt = f"{txt[:77]}..."
                parts.append(
                    f"{_ocr_tty_yellow_name(rid)} conf={conf_f:.3f} text={txt!r}"
                )
            if parts:
                ts = datetime.now().strftime("%H:%M:%S")
                extra = ""
                if len(rep_indices) == 1:
                    r = regions[rep_indices[0]]
                    extra = f" bbox=({r.x},{r.y},{r.w}x{r.h})"
                elif len(rep_indices) > 1:
                    extra = f" batch={len(rep_indices)}"
                cached = len(regions) - len(miss_indices)
                within_batch = len(miss_indices) - len(rep_indices)
                cache_tag = f" cached={cached}" if cached else ""
                dedup_tag = f" dedup={within_batch}" if within_batch else ""
                mode_tag = " mode=crops" if use_crops else " mode=full"
                omitted = len(items) - len(parts)
                tail = f" | +{omitted} more" if omitted > 0 else ""
                logger.info(
                    "OCR ts=%s ms=%.0f%s%s%s%s %s%s",
                    ts, elapsed_ms, extra, mode_tag, cache_tag, dedup_tag,
                    " | ".join(parts), tail,
                )
        except Exception:
            pass

        return [r for r in results if r is not None]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4))
    async def ocr_region(
        self,
        image: np.ndarray,
        region: Region,
        *,
        region_id: str | None = None,
        preprocess: str | None = None,
    ) -> OCRResult:
        rid = (region_id or "").strip() or "r0"
        results = await self.ocr_regions(
            image,
            [region],
            region_ids=[rid],
            region_preprocess=[preprocess] if preprocess else None,
        )
        return results[0] if results else OCRResult(region_id=rid, text="", confidence=0.0)
