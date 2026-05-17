"""Redirect version-specific reference links to base ref + ``?version=...``.

After the v3 schema flip, the canonical link is the base reference image plus a
``?version=<vid>`` selector. This module computes the redirect target purely
from ``area.json`` data so the Streamlit page can apply it before any
state-restoring code runs.
"""
from __future__ import annotations

from typing import Any


def resolve_version_ref_redirect(
    area_doc: dict[str, Any],
    current_ref: Any,
) -> tuple[str, str] | None:
    """Return ``(base_ref, version_id)`` if ``current_ref`` matches a ``versions[].ocr``.

    ``current_ref`` is the raw canonical ``?ref=`` value: repo-relative path to
    a PNG under a ``references`` directory.
    Returns ``None`` when no redirect is needed — including the cases of empty /
    invalid / non-version paths.
    """
    if not isinstance(current_ref, str):
        return None
    cand = current_ref.replace("\\", "/").strip().lstrip("/")
    if not cand or cand.startswith("..") or "/.." in cand:
        return None

    screens = area_doc.get("screens") if isinstance(area_doc, dict) else None
    if not isinstance(screens, list):
        return None
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            ver_ocr = str(ver.get("ocr") or "").replace("\\", "/").strip().lstrip("/")
            if not ver_ocr or ver_ocr != cand:
                continue
            base_ocr = str(entry.get("ocr") or "").replace("\\", "/").strip().lstrip("/")
            vid = str(ver.get("id") or "").strip()
            if not vid or not base_ocr:
                return None
            return base_ocr, vid
    return None
