"""Read/set the OCR language — the dashboard's language selector backend.

The language is an explicit operator setting (``WOS_OCR_LANG`` in the repo
``.env``), never inferred from the running game build: the per-build auto-bind
only ever worked in the worker process (which probes the device package), while
the API's OCR fell back to ``eng`` and mangled Cyrillic pills. One setting,
every process.

Setting it here persists the ``.env`` line (so worker/standalone processes pick
it up on their next start) and applies to THIS process immediately (settings
swap + OCR client rebuild). A running worker keeps its old language until
restarted — the UI says so.
"""
from __future__ import annotations

import dataclasses
import logging
import re

from config.paths import repo_root

logger = logging.getLogger(__name__)

_LANG_RE = re.compile(r"^[a-z_]{2,12}(\+[a-z_]{2,12})*$")
# Tesseract ships non-language data packs alongside the real languages.
_NON_LANGS = frozenset({"osd", "snum", "equ"})


def available_langs() -> list[str]:
    """Installed Tesseract language codes (data packs filtered out)."""
    from ocr.client import OcrClient
    from services import get_settings

    cmd = get_settings().ocr.tesseract_cmd
    langs = OcrClient._available_langs(cmd)
    return sorted(code for code in langs if code not in _NON_LANGS)


def current_lang() -> str:
    from services import get_settings

    return get_settings().ocr.lang


def set_lang(lang: str) -> dict[str, object]:
    """Persist ``lang`` to ``.env`` and apply it to this process."""
    lang = (lang or "").strip().lower()
    if not _LANG_RE.match(lang):
        msg = f"invalid language code: {lang!r}"
        raise ValueError(msg)
    installed = available_langs()
    if installed and any(part not in installed for part in lang.split("+")):
        msg = f"language {lang!r} is not installed (have: {', '.join(installed)})"
        raise ValueError(msg)

    _persist_env_line(lang)

    # Apply to this process: env for any future load, live settings swap, and
    # a fresh OCR client so the next read uses the new traineddata.
    import os

    from config.loader import set_settings
    from services import get_settings, reset_ocr_client

    os.environ["WOS_OCR_LANG"] = lang
    settings = get_settings()
    set_settings(
        dataclasses.replace(settings, ocr=dataclasses.replace(settings.ocr, lang=lang))
    )
    reset_ocr_client()
    logger.info("ocr lang set to %r (persisted to .env)", lang)
    return {"ok": True, "lang": lang}


def _persist_env_line(lang: str) -> None:
    """Update-or-append ``WOS_OCR_LANG=<lang>`` in the repo ``.env``."""
    path = repo_root() / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    line = f"WOS_OCR_LANG={lang}"
    for i, existing in enumerate(lines):
        if existing.strip().startswith("WOS_OCR_LANG="):
            lines[i] = line
            break
    else:
        lines.extend(["", "# OCR language — set from the dashboard.", line])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
