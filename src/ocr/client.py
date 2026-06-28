from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import subprocess
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import cv2  # type: ignore[import-untyped]
import numpy as np

from ocr.digit_markers import DigitMarker, detect_digit_markers
from ocr.preprocess import (
    DIGITS_CHAR_WHITELIST,
    WORD_CHAR_WHITELIST,
    badge_digits_for_ocr,
    badge_white_for_ocr,
    badge_yellow_for_ocr,
    bar_timer_for_ocr,
    digits_for_ocr,
    enhance_for_ocr,
)
from ocr.word_cleaning import clean_word_text

if TYPE_CHECKING:
    from config.loader import Settings
    from layout.types import Region

logger = logging.getLogger(__name__)

# Highlight only the region **name** (region_id) in terminal OCR logs; bbox/conf/text stay plain.
_OCR_YELLOW = "\033[33m"
_OCR_RESET = "\033[0m"
_TITLE_PROGRESS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%.*$", re.IGNORECASE)


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
    # Memoized ``tesseract --list-langs`` per executable — probed once so a
    # missing ``rus.traineddata`` degrades to the default lang instead of a hard
    # tesseract error on every RU-build OCR call.
    _avail_langs: ClassVar[dict[str, frozenset[str]]] = {}

    @classmethod
    def _patch_hash(
        cls,
        image: np.ndarray,
        region: Region,
        *,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
        lang: str = "",
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
        # Fold the resolved language in too: the same pixels read under ``eng``
        # vs ``rus`` produce different text, so they must not share a cache
        # entry when a worker switches builds.
        if lang:
            h.update(b"|lang=")
            h.update(lang.encode("utf-8"))
        del digit_count, digit_x0
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
        # Per-module-catalog language overrides (e.g. ``wos_ru`` → ``rus`` for
        # the Russian "Белая мгла" build — never ``rus+eng``, which homoglyphs
        # Cyrillic into Latin). Resolved per OCR call against the active catalog
        # so a mixed fleet reads each build in its own script.
        self._catalog_lang = {
            str(k).strip(): str(v).strip()
            for k, v in dict(getattr(settings.ocr, "catalog_lang", {}) or {}).items()
            if str(k).strip() and str(v).strip()
        }
        self._lang_warned: set[str] = set()
        self._tesseract_cmd = (
            str(getattr(settings.ocr, "tesseract_cmd", "tesseract") or "tesseract").strip()
            or "tesseract"
        )
        self._tessdata_dir = str(getattr(settings.ocr, "tessdata_dir", "") or "").strip()
        self._timeout = float(settings.ocr.timeout_seconds)

    async def aclose(self) -> None:
        """Kept for the service container lifecycle; local OCR has no socket to close."""

    @classmethod
    def _available_langs(cls, tesseract_cmd: str) -> frozenset[str]:
        """Installed Tesseract language codes (memoized per executable)."""
        cached = cls._avail_langs.get(tesseract_cmd)
        if cached is not None:
            return cached
        langs: frozenset[str] = frozenset()
        try:
            proc = subprocess.run(
                [tesseract_cmd, "--list-langs"],
                check=False,
                capture_output=True,
                timeout=10.0,
            )
            out = proc.stdout.decode("utf-8", errors="replace")
            # First line is a header ("List of available languages ..."); the
            # remaining lines are one language code each.
            langs = frozenset(
                line.strip() for line in out.splitlines()[1:] if line.strip()
            )
        except Exception:
            logger.debug("tesseract --list-langs failed for %r", tesseract_cmd, exc_info=True)
        cls._avail_langs[tesseract_cmd] = langs
        return langs

    def _resolve_lang(self) -> str:
        """Tesseract ``-l`` value for the build active in this worker process.

        Consults the per-process active module catalog (bound when the running
        game package is detected). The Russian "Белая мгла" build binds
        ``wos_ru`` → ``rus``. Falls back to the default ``lang`` when no
        override applies or the mapped traineddata is not installed, so a missing
        language pack degrades to English instead of erroring every OCR call.
        """
        if not self._catalog_lang:
            return self._lang
        try:
            from services import get_active_module_catalog

            catalog = get_active_module_catalog()
        except Exception:
            return self._lang
        mapped = self._catalog_lang.get(catalog)
        if not mapped or mapped == self._lang:
            return self._lang
        available = self._available_langs(self._tesseract_cmd)
        if available and all(part in available for part in mapped.split("+")):
            return mapped
        if mapped not in self._lang_warned:
            self._lang_warned.add(mapped)
            logger.warning(
                "OCR language %r for catalog %r unavailable (installed: %s) — "
                "falling back to %r; install the traineddata to read this build.",
                mapped,
                catalog,
                sorted(available),
                self._lang,
            )
        return self._lang

    def detect_digit_markers(
        self,
        image_bgr: np.ndarray,
        *,
        psm: int = 11,
        min_conf: float = 0.30,
        upscale: float = 2.0,
    ) -> list[DigitMarker]:
        """Find scattered numbered markers in a full image (Dreamscape guides).

        Forwards this client's resolved tesseract config to
        :func:`ocr.digit_markers.detect_digit_markers`. Synchronous and uncached
        — a one-shot operator action, not a hot polling path. Run it in a worker
        thread from async callers.
        """
        return detect_digit_markers(
            image_bgr,
            tesseract_cmd=self._tesseract_cmd,
            lang=self._lang,
            tessdata_dir=self._tessdata_dir,
            timeout_s=self._timeout,
            psm=psm,
            min_conf=min_conf,
            upscale=upscale,
        )

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
        if pre_tag in ("enhance", "enhance_line", "title_line"):
            return enhance_for_ocr(crop)
        if pre_tag == "digits":
            return digits_for_ocr(crop)
        if pre_tag == "badge_digits":
            return badge_digits_for_ocr(crop)
        if pre_tag == "badge_white":
            return badge_white_for_ocr(crop)
        if pre_tag == "badge_yellow":
            return badge_yellow_for_ocr(crop)
        if pre_tag == "bar_timer":
            return bar_timer_for_ocr(crop)
        return crop

    @staticmethod
    def _tesseract_psm_and_whitelist(preprocess: str | None) -> tuple[str, str | None]:
        """Return ``(psm, char_whitelist)`` for the preprocess tag."""
        pre_tag = (preprocess or "").strip().lower()
        if pre_tag == "fast_digits":
            # Single line of digits (player id, power, server id): force the
            # digit whitelist so an ambiguous glyph resolves to a digit instead
            # of a symbol that gets stripped, shortening the number.
            return "7", DIGITS_CHAR_WHITELIST
        if pre_tag == "fast_line":
            return "7", None
        if pre_tag == "word_line":
            return "7", WORD_CHAR_WHITELIST
        if pre_tag in ("enhance_line", "title_line", "bar_timer"):
            return "7", None
        if pre_tag in ("badge_digits", "badge_white", "badge_yellow"):
            # "Lv. N" is a short LINE (prefix + number), so PSM 7 (single line) reads
            # it better than PSM 8 (single word); the digit whitelist drops "Lv.".
            return "7", DIGITS_CHAR_WHITELIST
        if pre_tag in ("enhance", "digits"):
            return "8", DIGITS_CHAR_WHITELIST if pre_tag == "digits" else None
        return "6", None

    @staticmethod
    def _clean_title_line_text(raw: str) -> str:
        text = _TITLE_PROGRESS_RE.sub(" ", raw).replace("\n", " ")
        # ``[^\W_]`` matches any Unicode letter or digit (Latin + Cyrillic), so
        # Russian titles/names survive — the old ``[A-Za-z0-9]`` class stripped
        # Cyrillic wholesale (e.g. "Грядет буря" → ""). For ASCII text the two
        # are equivalent, so the English path is unchanged.
        text = re.sub(r"(?<=[^\W_])[\W_]+(?=[^\W_])", " ", text)
        text = re.sub(r"^[\W_]+|[\W_]+$", "", text)
        return " ".join(text.split())

    @staticmethod
    def _clean_word_line_text(raw: str) -> str:
        return clean_word_text(raw)

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

    def _run_ocr_backend(
        self,
        crop: np.ndarray,
        *,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> tuple[str, float]:
        del digit_count, digit_x0
        return self._run_tesseract(crop, preprocess=preprocess)

    def _run_tesseract(self, crop: np.ndarray, *, preprocess: str | None = None) -> tuple[str, float]:
        if crop is None or crop.size == 0:
            return "", 0.0
        if shutil.which(self._tesseract_cmd) is None and not Path(self._tesseract_cmd).exists():
            msg = (
                f"tesseract executable not found: {self._tesseract_cmd!r}. "
                "Install Tesseract with eng.traineddata or set WOS_TESSERACT_CMD."
            )
            raise RuntimeError(
                msg
            )

        work = self._prepare_crop(crop, preprocess)
        ok, buf = cv2.imencode(".png", work)
        if not ok or buf is None:
            msg = "cv2.imencode('.png', crop) failed"
            raise RuntimeError(msg)

        psm, char_whitelist = self._tesseract_psm_and_whitelist(preprocess)
        # Pipe the PNG to tesseract via stdin (``tesseract stdin stdout``) rather
        # than writing a temp file: no per-OCR disk I/O, and no dependency on the
        # system temp dir being readable by the spawned process (sandboxed runs
        # give the parent a private TMPDIR the tesseract child can't open).
        cmd = [
            self._tesseract_cmd,
            "stdin",
            "stdout",
            "-l",
            self._resolve_lang(),
            "--oem",
            "1",
            "--psm",
            psm,
        ]
        if char_whitelist:
            cmd.extend(["-c", f"tessedit_char_whitelist={char_whitelist}"])
        if self._tessdata_dir:
            cmd.extend(["--tessdata-dir", self._tessdata_dir])
        cmd.append("tsv")
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            input=buf.tobytes(),
            timeout=self._timeout,
        )
        # Decode as bytes + errors="replace" rather than ``text=True``: tesseract
        # can emit non-UTF8 bytes on either stream (a binary blob in a leptonica
        # error dump, or an OCR'd glyph that isn't valid UTF-8), and ``text=True``
        # would raise an opaque UnicodeDecodeError that masks the real failure
        # (e.g. the underlying "image file not found" stderr).
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            detail = (stderr or stdout or "").strip()
            raise RuntimeError(detail or f"tesseract exited with status {proc.returncode}")
        text, confidence = self._parse_tesseract_tsv(stdout)
        pre_tag = (preprocess or "").strip().lower()
        if pre_tag == "title_line":
            text = self._clean_title_line_text(text)
        elif pre_tag == "word_line":
            text = self._clean_word_line_text(text)
        return text, confidence

    async def _ocr_crop(
        self,
        crop: np.ndarray,
        *,
        region_id: str,
        preprocess: str | None = None,
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        try:
            text, conf = await asyncio.to_thread(
                self._run_ocr_backend,
                crop,
                preprocess=preprocess,
                digit_count=digit_count,
                digit_x0=digit_x0,
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
        region_digit_count: list[int | None] | None = None,
        region_digit_x0: list[int] | None = None,
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
        # Resolved once per call: the active build's catalog can't change mid-scan,
        # and ``_run_tesseract`` resolves to the same value for the actual OCR.
        resolved_lang = self._resolve_lang()
        def _digit_count(i: int) -> int | None:
            if region_digit_count is None or i >= len(region_digit_count):
                return None
            raw = region_digit_count[i]
            if raw is None:
                return None
            try:
                n = int(raw)
            except (TypeError, ValueError):
                return None
            return n if n > 0 else None

        def _digit_x0(i: int) -> int:
            if region_digit_x0 is None or i >= len(region_digit_x0):
                return 0
            return int(region_digit_x0[i])

        for i, region in enumerate(regions):
            key = self._patch_hash(
                image,
                region,
                preprocess=_pre(i) or None,
                digit_count=_digit_count(i),
                digit_x0=_digit_x0(i),
                lang=resolved_lang,
            )
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
                digit_count=_digit_count(idx),
                digit_x0=_digit_x0(idx),
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
                mode_tag = f" mode=tesseract:{resolved_lang}"
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
        digit_count: int | None = None,
        digit_x0: int = 0,
    ) -> OCRResult:
        rid = (region_id or "").strip() or "r0"
        results = await self.ocr_regions(
            image,
            [region],
            region_ids=[rid],
            region_preprocess=[preprocess] if preprocess else None,
            region_digit_count=[digit_count],
            region_digit_x0=[digit_x0],
        )
        return results[0] if results else OCRResult(region_id=rid, text="", confidence=0.0)
