from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import cv2  # type: ignore[import-untyped]
import numpy as np

from config.loader import Settings
from layout.types import Region
from ocr.preprocess import enhance_for_ocr

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
    # Populated when local OCR failed for this region. Callers can use this to
    # distinguish "no text" from "OCR died" and decide whether to retry / skip
    # / surface to the user.
    error: str | None = None


class OcrClient:
    # Content-addressed cache: BLAKE2b of the bbox patch bytes → (ts, text, conf).
    # Pixels identical ⇒ OCR result identical, so the patch hash itself is a
    # correctness boundary (no need to invalidate on tap/swipe — pixel changes
    # already produce a different key).  TTL caps memory growth and is a safety
    # net against any edge case the hash misses.
    _OCR_CACHE_TTL_S: ClassVar[float] = 2.0
    _OCR_CACHE_MAX: ClassVar[int] = 256
    _cache: ClassVar[OrderedDict[bytes, tuple[float, str, float]]] = OrderedDict()

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
        self._lang = str(getattr(settings.ocr, "lang", "eng") or "eng").strip() or "eng"
        self._tesseract_cmd = (
            str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract").strip()
            or "tesseract"
        )
        self._tessdata_dir = str(getattr(settings.ocr, "tessdata_dir", "") or "").strip()
        self._timeout = float(settings.ocr.timeout_seconds)

    async def aclose(self) -> None:
        """Kept for the service container lifecycle; local OCR has no socket to close."""

    @staticmethod
    def _clamped_crop(image: np.ndarray, region: Region) -> np.ndarray:
        hi, wi = int(image.shape[0]), int(image.shape[1])
        x1 = max(0, min(int(region.x), wi))
        y1 = max(0, min(int(region.y), hi))
        x2 = max(x1, min(int(region.x + region.w), wi))
        y2 = max(y1, min(int(region.y + region.h), hi))
        return image[y1:y2, x1:x2]

    @staticmethod
    def _prepare_crop(crop: np.ndarray, preprocess: str | None) -> np.ndarray:
        pre_tag = (preprocess or "").strip().lower()
        if pre_tag == "enhance":
            return enhance_for_ocr(crop)
        return crop

    @staticmethod
    def _parse_tesseract_tsv(tsv: str) -> tuple[str, float]:
        lines = [line for line in tsv.splitlines() if line.strip()]
        if len(lines) < 2:
            return "", 0.0

        header = lines[0].split("\t")
        try:
            text_idx = header.index("text")
            conf_idx = header.index("conf")
            block_idx = header.index("block_num")
            par_idx = header.index("par_num")
            line_idx = header.index("line_num")
        except ValueError:
            return "", 0.0

        line_parts: OrderedDict[tuple[str, str, str], list[str]] = OrderedDict()
        confidences: list[float] = []
        for raw in lines[1:]:
            cols = raw.split("\t")
            if len(cols) <= max(text_idx, conf_idx, block_idx, par_idx, line_idx):
                continue
            word = cols[text_idx].strip()
            if not word:
                continue
            try:
                conf = float(cols[conf_idx])
            except ValueError:
                conf = -1.0
            if conf < 0:
                continue
            key = (cols[block_idx], cols[par_idx], cols[line_idx])
            line_parts.setdefault(key, []).append(word)
            confidences.append(conf / 100.0)

        text = "\n".join(" ".join(parts) for parts in line_parts.values()).strip()
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return text, confidence

    def _run_tesseract(self, crop: np.ndarray, *, preprocess: str | None = None) -> tuple[str, float]:
        if crop is None or crop.size == 0:
            return "", 0.0
        if shutil.which(self._tesseract_cmd) is None and not Path(self._tesseract_cmd).exists():
            raise RuntimeError(
                f"tesseract executable not found: {self._tesseract_cmd!r}. "
                "Install Tesseract with eng.traineddata or set WOS_TESSERACT_CMD."
            )

        work = self._prepare_crop(crop, preprocess)
        ok, buf = cv2.imencode(".png", work)
        if not ok or buf is None:
            raise RuntimeError("cv2.imencode('.png', crop) failed")

        psm = "7" if (preprocess or "").strip().lower() == "fast_line" else "6"
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            tmp.write(buf.tobytes())
            tmp.flush()
            cmd = [
                self._tesseract_cmd,
                tmp.name,
                "stdout",
                "-l",
                self._lang,
                "--oem",
                "1",
                "--psm",
                psm,
            ]
            if self._tessdata_dir:
                cmd.extend(["--tessdata-dir", self._tessdata_dir])
            cmd.append("tsv")
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(detail or f"tesseract exited with status {proc.returncode}")
        return self._parse_tesseract_tsv(proc.stdout)

    async def _ocr_crop(
        self,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
    ) -> OCRResult:
        try:
            text, conf = await asyncio.to_thread(
                self._run_tesseract,
                crop,
                preprocess=preprocess,
            )
            return OCRResult(region_id=region_id, text=text, confidence=conf)
        except Exception as exc:
            logger.exception(
                "OCR failed region=%s crop_shape=%s",
                region_id,
                getattr(crop, "shape", None),
            )
            return OCRResult(
                region_id=region_id,
                text="",
                confidence=0.0,
                error=f"{type(exc).__name__}: {exc}",
            )

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

        # Pre-fill from the content-addressed cache. Only regions whose bbox
        # patch has not been OCR'd recently are sent to Tesseract; identical
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
            # All regions served from the in-process patch-hash cache.
            # Verbose at default log levels — use DEBUG (e.g. ``LOGLEVEL=DEBUG``)
            # when investigating cache behavior.
            try:
                ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
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

        t0 = time.perf_counter()
        raw_results = [
            await self._ocr_crop(
                self._clamped_crop(image, regions[idx]),
                region_id=_rid(idx),
                preprocess=_pre(idx) or None,
            )
            for idx in rep_indices
        ]
        elapsed_ms = 1000.0 * (time.perf_counter() - t0)

        # Stitch OCR responses back by ``region_id``. Keeping this explicit
        # preserves the old ordering contract and catches mocked/local backend
        # mistakes before they warm the wrong cache key.
        idx_by_rid: dict[str, int] = {_rid(idx): idx for idx in rep_indices}
        key_by_rid: dict[str, bytes] = {
            _rid(rep_indices[pos]): unique_keys[pos] for pos in range(len(rep_indices))
        }
        seen_rids: set[str] = set()
        for item in raw_results:
            rid = item.region_id.strip()
            rep_idx = idx_by_rid.get(rid)
            if rep_idx is None:
                logger.warning("OCR returned unknown region_id=%r", rid)
                continue
            if rid in seen_rids:
                logger.warning("OCR returned duplicate region_id=%r", rid)
                continue
            seen_rids.add(rid)
            text = item.text
            conf = item.confidence
            err = item.error
            if err:
                logger.warning("OCR error region_id=%r err=%s", rid, err)
            # Fan out to every input slot that hashed to this patch — identical
            # pixels share the OCR verdict. Each fanout slot keeps its own
            # caller-supplied ``region_id`` so downstream logs stay readable.
            for fanout_idx in key_to_fanout[key_by_rid[rid]]:
                results[fanout_idx] = OCRResult(
                    region_id=_rid(fanout_idx), text=text, confidence=conf, error=err
                )
            # Don't cache OCR errors — caching a transient failure would mask
            # recovery until the entry's TTL expires.
            if not err:
                self._cache_put(key_by_rid[rid], text, conf)

        missing_rids = [rid for rid in idx_by_rid if rid not in seen_rids]
        if missing_rids:
            logger.warning("OCR response missing region_ids=%s", missing_rids)

        try:
            max_shown = 6
            parts: list[str] = []
            for item in raw_results[:max_shown]:
                rid = item.region_id.strip()
                txt = item.text.strip().replace("\n", " ")
                conf_f = float(item.confidence)
                if len(txt) > 80:
                    txt = f"{txt[:77]}..."
                parts.append(
                    f"{_ocr_tty_yellow_name(rid)} conf={conf_f:.3f} text={txt!r}"
                )
            if parts:
                ts = datetime.now(tz=UTC).strftime("%H:%M:%S")
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
                mode_tag = f" mode=tesseract:{self._lang}"
                omitted = len(raw_results) - len(parts)
                tail = f" | +{omitted} more" if omitted > 0 else ""
                logger.info(
                    "OCR ts=%s ms=%.0f%s%s%s%s %s%s",
                    ts, elapsed_ms, extra, mode_tag, cache_tag, dedup_tag,
                    " | ".join(parts), tail,
                )
        except Exception:
            pass

        return [r for r in results if r is not None]

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
