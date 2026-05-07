"""Wiki: browse analyze/overlay YAML as a human-readable story."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st
import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
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


def _load_analyze_manifest(path: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Returns (loaded_files, merged_overlay_rules)."""
    if not path.is_file():
        return ([], [])

    raw = _load_yaml_dict(path)
    overlay_merged: list[dict[str, Any]] = []

    ov = raw.get("overlay")
    if isinstance(ov, list):
        overlay_merged.extend([r for r in ov if isinstance(r, dict)])

    loaded: list[Path] = [path]

    inc = raw.get("include")
    if isinstance(inc, list) and inc:
        for inc_path in _resolve_includes(path, inc):
            if not inc_path.is_file():
                continue
            loaded.append(inc_path)
            doc = _load_yaml_dict(inc_path)
            ov2 = doc.get("overlay")
            if isinstance(ov2, list):
                overlay_merged.extend([r for r in ov2 if isinstance(r, dict)])

    return (loaded, overlay_merged)


@dataclass(frozen=True)
class RegionRef:
    name: str
    screen_id: str
    ocr: str
    bbox: dict[str, Any] | None


def _index_regions(area_doc: dict[str, Any]) -> dict[str, RegionRef]:
    out: dict[str, RegionRef] = {}
    for scr in (area_doc.get("screens") or []) if isinstance(area_doc, dict) else []:
        if not isinstance(scr, dict):
            continue
        screen_id = str(scr.get("screen_id") or "")
        ocr = str(scr.get("ocr") or "")
        for reg in scr.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            nm = str(reg.get("name") or "").strip()
            if not nm:
                continue
            bbox = reg.get("bbox")
            out[nm] = RegionRef(name=nm, screen_id=screen_id, ocr=ocr, bbox=bbox if isinstance(bbox, dict) else None)
    return out


def _rule_regions(rule: dict[str, Any]) -> list[str]:
    keys = ["region", "search_region", "tap_region"]
    out: list[str] = []
    for k in keys:
        v = rule.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _render_rule(rule: dict[str, Any], *, regions_idx: dict[str, RegionRef]) -> None:
    name = str(rule.get("name") or "").strip() or "(unnamed)"
    action = str(rule.get("action") or "").strip() or "(no action)"

    header_cols = st.columns([2.2, 1.4, 1.4, 3.0])
    header_cols[0].markdown(f"**Name**: `{name}`")
    header_cols[1].markdown(f"**Action**: `{action}`")
    header_cols[2].markdown(f"**Region**: `{rule.get('region')}`")
    header_cols[3].markdown(f"**Flow**: `{rule.get('node', '')}` → `{rule.get('set_node', '')}`")

    meta = {
        "threshold": rule.get("threshold"),
        "priority": rule.get("priority"),
        "ttl": rule.get("ttl"),
        "min_match_saturation": rule.get("min_match_saturation"),
        "expected": rule.get("expected"),
        "fuzzy_threshold": rule.get("fuzzy_threshold"),
        "tap_offset_from_match": rule.get("tap_offset_from_match"),
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    if meta:
        st.code(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip(), language="yaml")

    regs = _rule_regions(rule)
    if regs:
        st.markdown("**Regions**")
        for nm in regs:
            ref = regions_idx.get(nm)
            if ref is None:
                st.markdown(f"- `{nm}` (not found in `area.json`)")
            else:
                scr = ref.screen_id or "(no screen_id)"
                ocr = ref.ocr or "(no ocr)"
                st.markdown(f"- `{nm}` · screen `{scr}` · ocr `{ocr}`")
                if ref.bbox:
                    st.code(yaml.safe_dump(ref.bbox, allow_unicode=True, sort_keys=False).strip(), language="yaml")

    pu = rule.get("pushUsecase")
    if isinstance(pu, list) and pu:
        st.markdown("**pushUsecase**")
        st.code(yaml.safe_dump(pu, allow_unicode=True, sort_keys=False).strip(), language="yaml")

    extra_keys = [
        "search_region",
        "tap_region",
        "tap_offset_from_match",
        "set_node",
        "node",
        "set_node",
        "expected",
        "fuzzy_threshold",
    ]
    extras = {k: rule.get(k) for k in extra_keys if rule.get(k) is not None}
    if extras:
        st.caption("Details")
        st.code(yaml.safe_dump(extras, allow_unicode=True, sort_keys=False).strip(), language="yaml")


st.title("Wiki · Analyze")
st.caption("Browse `references/analyze.yaml` overlay rules as a readable story.")

repo_root = _repo_root()
analyze_path = repo_root / "references" / "analyze.yaml"
area_path = repo_root / "area.json"

area_doc = _load_yaml_dict(area_path) if area_path.is_file() else {}
regions_idx = _index_regions(area_doc)

loaded_files, overlay_rules = _load_analyze_manifest(analyze_path)
if not overlay_rules:
    st.warning(f"No `overlay` rules loaded from `{analyze_path}`.")
    st.stop()

with st.expander("Sources", expanded=False):
    if loaded_files:
        for p in loaded_files:
            st.markdown(f"- `{p.relative_to(repo_root).as_posix()}`")

q = st.text_input("Filter (name/action/region contains)", value="", key="wiki_analyze_filter").strip().lower()

filtered: list[dict[str, Any]] = []
for r in overlay_rules:
    if not isinstance(r, dict):
        continue
    hay = "\n".join(
        [
            str(r.get("name") or ""),
            str(r.get("action") or ""),
            str(r.get("region") or ""),
            str(r.get("search_region") or ""),
            str(r.get("tap_region") or ""),
            str(r.get("node") or ""),
            str(r.get("set_node") or ""),
        ]
    ).lower()
    if q and q not in hay:
        continue
    filtered.append(r)

groups: dict[str, list[dict[str, Any]]] = {}
for r in filtered:
    node = str(r.get("node") or "").strip()
    # YAML uses `node: none` as a conventional "no specific node" marker.
    key = node if (node and node.lower() != "none") else "none"
    groups.setdefault(key, []).append(r)

st.subheader(f"Rules: {len(filtered)} · Nodes: {len(groups)}")

for node in sorted(groups.keys(), key=lambda s: (s == "none", s)):
    with st.expander(f"`{node}` · {len(groups[node])}", expanded=(node != "none")):
        for idx, rule in enumerate(groups[node], start=1):
            nm = str(rule.get("name") or "").strip() or f"rule_{idx}"
            act = str(rule.get("action") or "").strip() or "action"
            reg = str(rule.get("region") or "").strip()
            label = f"{nm} · `{act}`" + (f" · `{reg}`" if reg else "")
            with st.expander(label, expanded=False):
                _render_rule(rule, regions_idx=regions_idx)

