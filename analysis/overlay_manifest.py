from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _resolve_includes(manifest_path: Path, include: list[object]) -> list[Path]:
    out: list[Path] = []
    for item in include:
        s = str(item or "").strip()
        if not s:
            continue
        p = Path(s)
        if not p.is_absolute():
            p = manifest_path.parent / p
        out.append(p)
    return out


def load_analyze_yaml(path: Path) -> dict[str, Any]:
    """Load analyze.yaml config.

    Supports a "manifest" file with:

    - include: ["analyze_pages/analyze_main_page.yaml", ...]

    In that case, the returned dict merges keys, and concatenates ``overlay`` lists
    from all included files (and from the manifest itself, if present).
    """
    if not path.is_file():
        return {}

    raw = _load_yaml_dict(path)

    overlay_merged: list[dict[str, Any]] = []
    ov = raw.get("overlay")
    if isinstance(ov, list):
        overlay_merged.extend([r for r in ov if isinstance(r, dict)])

    inc = raw.get("include")
    if isinstance(inc, list) and inc:
        for inc_path in _resolve_includes(path, inc):
            if not inc_path.is_file():
                continue
            doc = _load_yaml_dict(inc_path)
            for k, v in doc.items():
                if k == "overlay":
                    continue
                if k not in raw:
                    raw[k] = v
            ov2 = doc.get("overlay")
            if isinstance(ov2, list):
                overlay_merged.extend([r for r in ov2 if isinstance(r, dict)])

    if overlay_merged:
        raw["overlay"] = overlay_merged
    return raw

