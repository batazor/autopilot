"""Wiki: browse analyze/overlay YAML as a human-readable story."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
import yaml
from streamlit_extras.stoggle import stoggle

from analysis.overlay_manifest import default_analyze_yaml_path
from analysis.overlay_rules import overlay_rule_screen_allowlist


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


def _push_scenario_names(rule: dict[str, Any]) -> list[str]:
    pu = rule.get("pushScenario")
    if not isinstance(pu, list):
        pu = rule.get("pushUsecase")  # backward compat
    if not isinstance(pu, list):
        return []

    out: list[str] = []
    for item in pu:
        if not isinstance(item, dict):
            continue
        task = item.get("task")
        src = task if isinstance(task, dict) else item
        nm = str(src.get("name") or src.get("type") or "").strip()
        if nm:
            out.append(nm)
    # Deduplicate, keep stable order
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _render_rule(rule: dict[str, Any]) -> None:
    name = str(rule.get("name") or "").strip() or "(unnamed)"
    action = str(rule.get("action") or "").strip() or "(no action)"
    st.markdown(f"**`{name}`** · `{action}`")

    scenarios = _push_scenario_names(rule)
    if scenarios:
        st.markdown("**pushScenario**")
        for s in scenarios:
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            with c1:
                st.markdown(f"- `{s}`")
            with c2:
                st.page_link(
                    "views/scenarios.py",
                    label="Open",
                    query_params={"q": s},
                    width="stretch",
                )


st.title("Wiki · Analyze")
st.caption("Browse `analyze/analyze.yaml` overlay rules as a readable story.")

repo_root = _repo_root()
analyze_path = default_analyze_yaml_path(repo_root)

loaded_files, overlay_rules = _load_analyze_manifest(analyze_path)
if not overlay_rules:
    st.warning(f"No `overlay` rules loaded from `{analyze_path}`.")
    st.stop()

if loaded_files:
    stoggle(
        "Sources",
        "\n".join(f"- `{p.relative_to(repo_root).as_posix()}`" for p in loaded_files),
    )

q = st.text_input("Filter (name/action contains)", value="", key="wiki_analyze_filter").strip().lower()

filtered: list[dict[str, Any]] = []
for r in overlay_rules:
    if not isinstance(r, dict):
        continue
    hay = "\n".join(
        [
            str(r.get("name") or ""),
            str(r.get("action") or ""),
        ]
    ).lower()
    if q and q not in hay:
        continue
    filtered.append(r)

groups: dict[str, list[dict[str, Any]]] = {}
for r in filtered:
    allow = overlay_rule_screen_allowlist(r)
    if not allow:
        key = "global"
    elif len(allow) == 1:
        key = allow[0].lower() if allow[0].lower() == "none" else allow[0]
    else:
        key = ", ".join(allow)
    groups.setdefault(key, []).append(r)

st.subheader(f"Rules: {len(filtered)} · Screen groups: {len(groups)}")

for node in sorted(groups.keys(), key=lambda s: (s == "global", s == "none", s.lower())):
    with st.expander(f"`{node}` · {len(groups[node])}", expanded=(node not in {"global", "none"})):
        for idx, rule in enumerate(groups[node], start=1):
            nm = str(rule.get("name") or "").strip() or f"rule_{idx}"
            act = str(rule.get("action") or "").strip() or "action"
            label = f"{nm} · `{act}`"
            with st.expander(label, expanded=False):
                _render_rule(rule)

