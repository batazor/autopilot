"""Structured editor for DSL scenarios (``scenarios/<domain>/<key>.yaml``).

Companion to ``ui/views/debug_scenarios.py`` (read-only runner). This page edits
the YAML in a form-based UI and saves with a timestamped backup. Running the
edited scenario is delegated to the runner page via a deep link.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import streamlit as st
import yaml
from pydantic import ValidationError
from st_ant_tree import st_ant_tree
from streamlit_dnd_sortable import apply_order_to_list, sortable_list

from config.reference_naming import event_icon_abs_path
from config.startup_validation import duplicate_scenario_names
from navigation.screen_graph import screen_verify_screen_names
from scenarios.dsl_schema import DSL_ACTION_KEYS, dump_scenario, parse_scenario
from scenarios.registry import iter_scenario_yaml_files, scenario_source_label
from tasks.dsl_exec import DSL_EXEC_REGISTRY
from ui.module_scope import render_module_scope_selector

DOMAINS_READONLY = {"drafts", "by_cron"}
DOMAINS_EDITABLE = {
    "ads",
    "chapters",
    "event",
    "mail",
    "main_city",
    "onboarding",
    "overlay",
    "workers",
}

STEP_TYPES_FOR_NEW: tuple[str, ...] = (
    "click",
    "match",
    "while_match",
    "wait",
    "ocr",
    "exec",
    "push_scenario",
    "set_node",
    "swipe_direction",
    "loop",
    "cond",
    "long_click",
)

# Only valid inside ``loop`` / ``repeat`` / ``while_match`` (raises ``_BreakRepeat`` in the
# executor; outside a loop the step is a no-op).
LOOP_PARENT_KINDS = frozenset({"loop", "repeat", "while_match"})

SWIPE_DIRECTIONS = ("up", "down", "left", "right")


def default_repo_root() -> Path:
    from config.paths import repo_root

    return repo_root()


def _scenarios_root() -> Path:
    from config.paths import core_scenarios_root

    return core_scenarios_root()


def _list_scenario_files(module_scope: str) -> list[Path]:
    repo = default_repo_root()
    out: list[Path] = []
    for _root, p in iter_scenario_yaml_files(repo, module_scope):
        rel = scenario_source_label(p, repo)
        if rel.startswith("modules/"):
            domain = rel.split("/")[2] if len(rel.split("/")) > 2 else ""
        else:
            domain = rel.split("/", 1)[0]
        if domain in DOMAINS_READONLY:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.as_posix())


def _scenario_keys() -> list[str]:
    return sorted({p.stem for p in _scenarios_root().rglob("*.yaml")})


@st.cache_data(show_spinner=False)
def _region_names_cached(area_mtime: float) -> list[str]:
    del area_mtime
    path = default_repo_root() / "area.json"
    if not path.is_file():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for screen in doc.get("screens", []) or []:
        if not isinstance(screen, dict):
            continue
        sources = [screen.get("regions") or []]
        for ver in screen.get("versions") or []:
            if isinstance(ver, dict):
                sources.append(ver.get("regions") or [])
        for regs in sources:
            for reg in regs or []:
                name = str((reg or {}).get("name") or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
    return sorted(out)


def _region_names() -> list[str]:
    path = default_repo_root() / "area.json"
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    return _region_names_cached(mtime)


def _fsm_nodes() -> list[str]:
    try:
        return screen_verify_screen_names() or []
    except Exception:
        return []


def _exec_names() -> list[str]:
    return sorted(DSL_EXEC_REGISTRY.keys())


def _page_url(page_path: str, params: dict[str, str]) -> str:
    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    return urlunparse((u.scheme, u.netloc, "/" + page_path.strip("/"), "", urlencode(params), ""))


def _safe_filename(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip()).strip("._-")
    return s or "scenario"


def _load_doc(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: root is not a mapping")
    raw.setdefault("steps", [])
    return raw


def _save_doc(path: Path, doc: dict[str, Any]) -> Path:
    """Validate, back up the existing file, then write."""
    parsed = parse_scenario(doc)
    out_doc = dump_scenario(parsed)

    backups_root = _scenarios_root() / ".backups"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backups_root / ts
    if path.is_file():
        rel = path.relative_to(_scenarios_root())
        backup_path = backup_dir / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_path)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(out_doc, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _new_step(step_type: str) -> dict[str, Any]:
    if step_type == "wait":
        return {"wait": "1s"}
    if step_type == "click":
        return {"click": ""}
    if step_type == "long_click":
        return {"long_click": ""}
    if step_type == "match":
        return {"match": ""}
    if step_type == "while_match":
        return {"while_match": "", "max": 5, "steps": []}
    if step_type == "ocr":
        return {"ocr": ""}
    if step_type == "exec":
        return {"exec": ""}
    if step_type == "push_scenario":
        return {"push_scenario": ""}
    if step_type == "set_node":
        return {"set_node": ""}
    if step_type == "swipe_direction":
        return {"swipe_direction": {"direction": "up", "delta": 400, "duration_ms": 600}}
    if step_type == "loop":
        return {"loop": {"max": 3, "steps": []}}
    if step_type == "cond":
        return {"cond": "", "steps": []}
    if step_type == "break":
        return {"break": "loop"}
    return {step_type: ""}


def _detect_step_type(step: dict[str, Any]) -> str:
    for k in DSL_ACTION_KEYS:
        if k in step and step.get(k) is not None:
            return k
    if "cond" in step and "steps" in step:
        return "cond"
    return "?"


def _step_summary_one_line(step: dict[str, Any]) -> str:
    """Short hint for drag-and-drop list rows (scenario editor)."""

    stype = _detect_step_type(step)
    if stype == "click":
        return str(step.get("click") or "")
    if stype == "long_click":
        return str(step.get("long_click") or "")
    if stype == "match":
        return str(step.get("match") or "")
    if stype == "while_match":
        return str(step.get("while_match") or "")
    if stype == "ocr":
        return str(step.get("ocr") or "")
    if stype == "exec":
        return str(step.get("exec") or "")
    if stype == "push_scenario":
        ps = step.get("push_scenario")
        if isinstance(ps, dict):
            return str(ps.get("name") or "")
        return str(ps or "")
    if stype == "set_node":
        return str(step.get("set_node") or "")
    if stype == "wait":
        return str(step.get("wait") or "")
    if stype == "break":
        return str(step.get("break") or "")
    if stype == "swipe_direction":
        spec = step.get("swipe_direction")
        if isinstance(spec, dict):
            return str(spec.get("direction") or "")
        return str(spec or "")
    if stype in {"loop", "repeat"}:
        spec = step.get(stype)
        if isinstance(spec, dict):
            n_inner = len(spec.get("steps") or []) if isinstance(spec.get("steps"), list) else 0
            return f"max={spec.get('max')} inner={n_inner}"
        return str(spec or "")
    if stype == "cond":
        inner = step.get("steps") if isinstance(step.get("steps"), list) else []
        return str(step.get("cond") or "").strip() or f"steps={len(inner)}"
    return ""


def _steps_dnd_items(steps: list[Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for idx, raw in enumerate(steps):
        if not isinstance(raw, dict):
            row = {"id": str(idx), "title": f"{idx + 1}. (invalid)"}
        else:
            st_local = _detect_step_type(raw)
            title = f"{idx + 1}. {st_local}"
            subt = _step_summary_one_line(raw).strip()
            row = {"id": str(idx), "title": title}
            if subt:
                row["subtitle"] = subt[:200]
        items.append(row)
    return items


def _path_key(path: tuple[int, ...]) -> str:
    return "edit_scenarios::" + "/".join(str(i) for i in path) if path else "edit_scenarios::root"


def _move(steps: list[Any], idx: int, delta: int) -> None:
    j = idx + delta
    if 0 <= j < len(steps):
        steps[idx], steps[j] = steps[j], steps[idx]


def _render_step_card(step: dict[str, Any], path: tuple[int, ...], depth: int) -> None:
    """Render one step's editor controls; mutates ``step`` in place."""
    stype = _detect_step_type(step)
    pk = _path_key(path)

    cond = st.text_input(
        "cond (guard, optional)",
        value=str(step.get("cond") or ""),
        key=pk + "::cond",
        placeholder='e.g. currentNode == main_city or chapter.task ~= "Upgrade|Build"',
    )
    if cond.strip():
        step["cond"] = cond.strip()
    elif "cond" in step and stype != "cond":
        step.pop("cond", None)

    regions = _region_names()
    scen_keys = _scenario_keys()
    execs = _exec_names()
    nodes = _fsm_nodes()

    if stype == "click":
        step["click"] = _region_select("region (click)", step.get("click") or "", regions, pk + "::click")
    elif stype == "long_click":
        c1, c2 = st.columns(2)
        with c1:
            step["long_click"] = _region_select(
                "region (long_click)", step.get("long_click") or "", regions, pk + "::lc"
            )
        with c2:
            dur = st.text_input("duration (e.g. 800ms)", value=str(step.get("wait") or "800ms"), key=pk + "::lcdur")
            step["wait"] = dur
    elif stype == "match":
        step["match"] = _region_select("region (match)", step.get("match") or "", regions, pk + "::m")
        _match_params(step, pk)
    elif stype == "while_match":
        step["while_match"] = _region_select(
            "region (while_match)", step.get("while_match") or "", regions, pk + "::wm"
        )
        c1, c2 = st.columns(2)
        with c1:
            step["max"] = st.number_input(
                "max iterations", min_value=0, max_value=999, value=int(step.get("max") or 5), key=pk + "::wmmax"
            )
        with c2:
            sat = st.number_input(
                "min_match_saturation",
                min_value=0,
                max_value=100,
                value=int(step.get("min_match_saturation") or 0),
                key=pk + "::wmsat",
                help="0 = use detector default",
            )
            if sat:
                step["min_match_saturation"] = int(sat)
            else:
                step.pop("min_match_saturation", None)
        if not isinstance(step.get("steps"), list):
            step["steps"] = []
        st.caption("Inner steps (run on each iteration):")
        _render_steps_list(step["steps"], path, depth + 1, parent_kind="while_match")
    elif stype == "ocr":
        step["ocr"] = _region_select("region (ocr)", step.get("ocr") or "", regions, pk + "::ocr")
    elif stype == "exec":
        step["exec"] = _select_with_freetext(
            "function", str(step.get("exec") or ""), execs, pk + "::exec"
        )
    elif stype == "push_scenario":
        cur = step.get("push_scenario")
        if isinstance(cur, dict):
            name_v = str(cur.get("name") or "")
            prio_v = int(cur.get("priority") or 0)
        else:
            name_v = str(cur or "")
            prio_v = 0
        c1, c2 = st.columns([2, 1])
        with c1:
            new_name = _select_with_freetext("scenario key", name_v, scen_keys, pk + "::psn")
        with c2:
            new_prio = st.number_input(
                "priority (0 = inherit)",
                min_value=0,
                max_value=10_000_000,
                value=prio_v,
                step=1000,
                key=pk + "::psp",
            )
        if new_prio:
            step["push_scenario"] = {"name": new_name, "priority": int(new_prio)}
        else:
            step["push_scenario"] = new_name
    elif stype == "set_node":
        step["set_node"] = _select_with_freetext(
            "target node", str(step.get("set_node") or ""), nodes, pk + "::sn"
        )
    elif stype == "wait":
        step["wait"] = st.text_input(
            "duration (e.g. 500ms, 2s, 0.8s)",
            value=str(step.get("wait") or "1s"),
            key=pk + "::w",
        )
    elif stype == "swipe_direction":
        spec = step.get("swipe_direction") or {}
        if not isinstance(spec, dict):
            spec = {}
        c1, c2, c3 = st.columns(3)
        with c1:
            d = st.selectbox(
                "direction",
                SWIPE_DIRECTIONS,
                index=SWIPE_DIRECTIONS.index(str(spec.get("direction") or "up"))
                if str(spec.get("direction") or "up") in SWIPE_DIRECTIONS
                else 0,
                key=pk + "::swd",
            )
        with c2:
            delta = st.number_input(
                "delta (px)", min_value=10, max_value=2000, value=int(spec.get("delta") or 400), key=pk + "::swl"
            )
        with c3:
            dur = st.number_input(
                "duration_ms", min_value=50, max_value=5000, value=int(spec.get("duration_ms") or 600), key=pk + "::swt"
            )
        step["swipe_direction"] = {"direction": d, "delta": int(delta), "duration_ms": int(dur)}
    elif stype in {"loop", "repeat"}:
        spec = step.get(stype)
        if isinstance(spec, dict):
            cur_max = int(spec.get("max") or 1)
            inner = spec.get("steps")
            if not isinstance(inner, list):
                inner = []
                spec["steps"] = inner
        else:
            cur_max = int(spec or 1)
            spec = {"max": cur_max, "steps": []}
            step[stype] = spec
            inner = spec["steps"]
        spec["max"] = st.number_input(
            "max iterations", min_value=0, max_value=999, value=cur_max, key=pk + "::rmax"
        )
        st.caption("Inner steps (use `break: loop` to exit early):")
        _render_steps_list(inner, path, depth + 1, parent_kind=stype)
    elif stype == "cond":
        if not isinstance(step.get("steps"), list):
            step["steps"] = []
        st.caption("Composite cond block. Inner steps run only if guard above is true.")
        _render_steps_list(step["steps"], path, depth + 1, parent_kind="cond")
    elif stype == "break":
        step["break"] = st.text_input(
            "label", value=str(step.get("break") or "loop"), key=pk + "::brk"
        )
    else:
        st.warning(f"Unknown step type — raw fields: {list(step.keys())}")
        st.json(step, expanded=False)


def _match_params(step: dict[str, Any], pk: str) -> None:
    c1, c2 = st.columns(2)
    with c1:
        thr = st.number_input(
            "threshold",
            min_value=0.0,
            max_value=1.0,
            value=float(step.get("threshold") or 0.0),
            step=0.05,
            format="%.2f",
            key=pk + "::thr",
            help="0.0 = use region default from area.json",
        )
        if thr:
            step["threshold"] = float(thr)
        else:
            step.pop("threshold", None)
    with c2:
        sat = st.number_input(
            "min_match_saturation",
            min_value=0,
            max_value=100,
            value=int(step.get("min_match_saturation") or 0),
            key=pk + "::msat",
            help="0 = no saturation gate",
        )
        if sat:
            step["min_match_saturation"] = int(sat)
        else:
            step.pop("min_match_saturation", None)


def _region_select(label: str, current: str, options: list[str], key: str) -> str:
    return _select_with_freetext(label, current, options, key)


def _select_with_freetext(label: str, current: str, options: list[str], key: str) -> str:
    """Selectbox over ``options`` with a free-text fallback for unknown values."""
    cur = (current or "").strip()
    opts = list(options)
    if cur and cur not in opts:
        opts = [cur, *opts]
    if not opts:
        return st.text_input(label, value=cur, key=key + "::txt")
    idx = opts.index(cur) if cur in opts else 0
    chosen = st.selectbox(label, opts, index=idx, key=key + "::sel")
    return chosen


def _render_steps_list(
    steps: list[Any],
    parent_path: tuple[int, ...],
    depth: int,
    *,
    parent_kind: str = "",
) -> None:
    """Render a list of steps with reorder/delete controls.

    ``parent_kind`` is the type of the enclosing block (``loop`` / ``repeat`` /
    ``while_match`` / ``cond`` / "" for the scenario root). It controls which
    step types are offered in *Add step* — e.g. ``break`` only inside a loop.
    """
    pk = _path_key(parent_path) + "::list"
    pending_actions: list[tuple[str, int]] = []

    rev_key = _path_key(parent_path) + "::dnd_revision"
    st.session_state.setdefault(rev_key, 0)
    cur_rev = int(st.session_state[rev_key])

    if len(steps) > 1:
        st.caption("Drag **≡** handles to reorder. **↑↓** buttons still work below.")
        dnd_pick = sortable_list(
            _steps_dnd_items(steps),
            revision=cur_rev,
            key=_path_key(parent_path) + "::dnd_sortable",
        )
        if isinstance(dnd_pick, dict):
            try:
                srv_rev_ok = int(dnd_pick["revision"]) == cur_rev  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                srv_rev_ok = False
            raw_order = dnd_pick.get("order")
            if (
                srv_rev_ok
                and isinstance(raw_order, list)
                and apply_order_to_list(steps, [str(x) for x in raw_order])
            ):
                st.session_state[rev_key] = cur_rev + 1
                st.rerun()

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            steps[i] = {"wait": "0s"}
            step = steps[i]
        path = parent_path + (i,)
        stype = _detect_step_type(step)
        with st.container(border=True):
            head = st.columns([0.5, 4, 0.4, 0.4, 0.4])
            head[0].markdown(f"**{i + 1}.**")
            head[1].markdown(f"`{stype}`")
            if head[2].button("↑", key=pk + f"::up::{i}", disabled=(i == 0)):
                pending_actions.append(("up", i))
            if head[3].button("↓", key=pk + f"::dn::{i}", disabled=(i == len(steps) - 1)):
                pending_actions.append(("dn", i))
            if head[4].button("✕", key=pk + f"::rm::{i}"):
                pending_actions.append(("rm", i))
            _render_step_card(step, path, depth)

    available_types = list(STEP_TYPES_FOR_NEW)
    if parent_kind in LOOP_PARENT_KINDS:
        available_types.append("break")

    add_cols = st.columns([3, 1])
    new_type = add_cols[0].selectbox(
        "add step type",
        available_types,
        key=pk + "::addtype",
        label_visibility="collapsed",
    )
    if add_cols[1].button("Add step", key=pk + "::addbtn", width="stretch"):
        steps.append(_new_step(str(new_type)))
        st.rerun()

    if pending_actions:
        for action, idx in pending_actions:
            if action == "up":
                _move(steps, idx, -1)
            elif action == "dn":
                _move(steps, idx, +1)
            elif action == "rm" and 0 <= idx < len(steps):
                steps.pop(idx)
        st.rerun()


def _name_collisions(current_rel: str, current_name: str) -> list[str]:
    """Other scenario rel-paths whose ``name:`` equals ``current_name``.

    Disk-backed: reads sibling files. Excludes ``current_rel`` itself so a
    user who hasn't yet saved a rename doesn't collide with their own draft.
    """
    nm = (current_name or "").strip()
    if not nm:
        return []
    dups = duplicate_scenario_names(_scenarios_root())
    others: list[str] = []
    for rel in dups.get(nm, []):
        if rel != current_rel:
            others.append(rel)
    return others


def _render_header_form(doc: dict[str, Any], current_rel: str) -> None:
    nodes = _fsm_nodes()
    c1, c2 = st.columns([3, 2])
    with c1:
        doc["name"] = st.text_input("name", value=str(doc.get("name") or ""), key="es::name")
        if not str(doc["name"]).strip():
            st.error("Scenario `name` is required.")
        else:
            collisions = _name_collisions(current_rel, doc["name"])
            if collisions:
                joined = ", ".join(f"`{p}`" for p in collisions)
                st.error(
                    f"Duplicate scenario name — also used by: {joined}. "
                    "Rename here or in the other file before saving."
                )
    with c2:
        doc["node"] = _select_with_freetext(
            "node (FSM target before steps)",
            str(doc.get("node") or ""),
            ["", *nodes],
            "es::node",
        )
        if not (doc["node"] or "").strip():
            doc.pop("node", None)
    doc["cond"] = st.text_input(
        "cond (root guard, optional)",
        value=str(doc.get("cond") or ""),
        key="es::cond",
        placeholder='e.g. active_player == "" or currentNode == none',
    )
    if not doc["cond"].strip():
        doc.pop("cond", None)

    ci1, ci2 = st.columns([3, 1])
    with ci1:
        doc["icon"] = st.text_input(
            "icon slug (resolves to references/events/event.<slug>.png)",
            value=str(doc.get("icon") or ""),
            key="es::icon",
            placeholder="e.g. 7-day, first_purchase, snowstorm",
        )
        if not doc["icon"].strip():
            doc.pop("icon", None)
    with ci2:
        icon_path = event_icon_abs_path(default_repo_root(), str(doc.get("icon") or ""))
        if icon_path is not None:
            st.image(str(icon_path), width=64)
        elif doc.get("icon"):
            st.caption("⚠️ no file at references/events/")

    c3, c4, c5, c6 = st.columns(4)
    with c3:
        doc["enabled"] = st.toggle("enabled", value=bool(doc.get("enabled")), key="es::en")
    with c4:
        doc["device_level"] = st.toggle(
            "device_level", value=bool(doc.get("device_level")), key="es::dl"
        )
    with c5:
        prio = st.number_input(
            "priority (0 = default)",
            min_value=0,
            max_value=10_000_000,
            value=int(doc.get("priority") or 0),
            step=1000,
            key="es::prio",
        )
        if prio:
            doc["priority"] = int(prio)
        else:
            doc.pop("priority", None)
    with c6:
        cron = st.text_input(
            "cron (optional)",
            value=str(doc.get("cron") or ""),
            placeholder="*/5 * * * *",
            key="es::cron",
        )
        if cron.strip():
            doc["cron"] = cron.strip()
        else:
            doc.pop("cron", None)


def _validate(doc: dict[str, Any]) -> tuple[bool, str]:
    try:
        parse_scenario(doc)
        return True, ""
    except ValidationError as e:
        return False, str(e)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _doc_session_key(path: Path) -> str:
    return f"es::doc::{path.as_posix()}"


def _selected_path_key() -> str:
    return "es::selected_path"


# ---------------------------------------------------------------------------
# Page body
# ---------------------------------------------------------------------------

st.title("Scenarios editor")
st.caption(
    "Edit DSL scenarios in form view. `drafts/` is read-only. Saves are validated against "
    "the DSL schema and back up the previous file under `scenarios/.backups/<timestamp>/`."
)

with st.sidebar:
    module_scope = render_module_scope_selector(in_sidebar=True)

files = _list_scenario_files(module_scope)
if not files:
    st.warning("No editable scenarios found for this module scope.")
    st.stop()

_repo = default_repo_root()


def _resolve_query_scenario(rels: list[str], stems: list[str]) -> str | None:
    raw = st.query_params.get("scenario")
    if raw is None:
        return None
    s = raw[0] if isinstance(raw, list) and raw else raw
    s = str(s or "").strip().replace("\\", "/")
    if not s:
        return None
    if s in rels:
        return s
    if s in stems:
        for r, stem in zip(rels, stems, strict=True):
            if stem == s:
                return r
    return None


rels = [scenario_source_label(p, _repo) for p in files]
stems = [p.stem for p in files]
deep_link = _resolve_query_scenario(rels, stems)
if deep_link is not None:
    st.session_state[_selected_path_key()] = deep_link
sel_default = st.session_state.get(_selected_path_key()) or rels[0]
if sel_default not in rels:
    sel_default = rels[0]

path_by_rel = {r: f for r, f in zip(rels, files, strict=True)}


def _build_scenario_tree_data(rel_paths: list[str]) -> list[dict[str, Any]]:
    """Produce ``treeData`` for ``st_ant_tree`` from scenario rel paths.

    Mirrors the shape of :func:`ui.reference_tree.dir_node_to_ant_tree_data`:
    folder nodes use ``__dir__/<name>`` as a placeholder ``value``, leaf
    ``value``s are the rel paths themselves so callers map back to files.
    """
    root: dict[str, Any] = {"files": [], "dirs": {}}
    for rel in rel_paths:
        parts = rel.split("/")
        node = root
        for part in parts[:-1]:
            node["dirs"].setdefault(part, {"files": [], "dirs": {}})
            node = node["dirs"][part]
        node["files"].append(rel)

    def _walk(node: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rel in sorted(node["files"]):
            out.append({"value": rel, "title": Path(rel).stem})
        for dirname in sorted(node["dirs"]):
            children = _walk(node["dirs"][dirname], f"{prefix}{dirname}/")
            if not children:
                continue
            out.append(
                {
                    "value": f"__dir__/{prefix}{dirname}",
                    "title": f"{dirname}/",
                    "children": children,
                }
            )
        return out

    return _walk(root, "")


tree_data = _build_scenario_tree_data(rels)

top_pick, top_new, top_run = st.columns([6, 1, 1], vertical_alignment="bottom")
with top_pick:
    picked = st_ant_tree(
        treeData=tree_data,
        treeCheckable=False,
        multiple=False,
        showSearch=True,
        placeholder="Select scenario YAML",
        defaultValue=[sel_default],
        width_dropdown="100%",
        max_height=380,
        treeLine=True,
        only_children_select=True,
        allowClear=False,
        key="es::filepick_tree",
    )

picked_one: str | None = None
if isinstance(picked, list) and picked:
    picked_one = str(picked[0])
elif isinstance(picked, str):
    picked_one = picked
if picked_one and not picked_one.startswith("__dir__/") and picked_one in path_by_rel:
    chosen = picked_one
else:
    chosen = sel_default

selected_path = path_by_rel[chosen]
chosen_stem = selected_path.stem
st.session_state[_selected_path_key()] = chosen
if str(st.query_params.get("scenario") or "") != chosen_stem:
    st.query_params["scenario"] = chosen_stem

with top_new, st.popover("New", width="stretch", help="Create a new scenario YAML."):
    new_domain = st.selectbox("domain", sorted(DOMAINS_EDITABLE), key="es::newdom")
    new_key = st.text_input("file key", placeholder="e.g. dismiss_popup", key="es::newkey")
    if st.button("Create", key="es::newbtn", type="primary", width="stretch"):
        key = _safe_filename(new_key)
        new_path = _scenarios_root() / new_domain / f"{key}.yaml"
        if new_path.exists():
            st.error(f"already exists: {new_path.relative_to(_scenarios_root())}")
        elif not new_key.strip():
            st.error("file key is required")
        else:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            stub = {"name": key.replace("_", " "), "enabled": False, "steps": []}
            new_path.write_text(yaml.safe_dump(stub, sort_keys=False), encoding="utf-8")
            st.session_state[_selected_path_key()] = new_path.relative_to(_scenarios_root()).as_posix()
            st.session_state.pop(_doc_session_key(new_path), None)
            st.rerun()

with top_run:
    st.link_button(
        "Runner",
        _page_url("debug_scenarios", {"scenario": chosen_stem}),
        width="stretch",
        help="Open this scenario in Scenario runner (Debug page).",
    )

doc_key = _doc_session_key(selected_path)
if doc_key not in st.session_state:
    try:
        st.session_state[doc_key] = _load_doc(selected_path)
    except Exception as e:
        st.error(f"Failed to load `{selected_path.name}`: {e}")
        st.stop()
doc: dict[str, Any] = st.session_state[doc_key]

_render_header_form(doc, chosen)

st.markdown("### Steps")
if not isinstance(doc.get("steps"), list):
    doc["steps"] = []
_render_steps_list(doc["steps"], (), 0)

st.divider()

ok, err = _validate(doc)
name_value = str(doc.get("name") or "").strip()
name_collisions = _name_collisions(chosen, name_value)
save_disabled = (not ok) or (not name_value) or bool(name_collisions)
save_l, save_r = st.columns([1, 5])
with save_l:
    if st.button("Save", type="primary", disabled=save_disabled, key="es::save", width="stretch"):
        try:
            _save_doc(selected_path, doc)
            st.session_state.pop(doc_key, None)
            st.success(f"Saved `{selected_path.name}` (backup written).")
            time.sleep(0.4)
            st.rerun()
        except Exception as e:
            st.error(f"Save failed: {e}")
with save_r:
    if not ok:
        st.error("Schema errors — fix before saving.")
        with st.expander("Validation details", expanded=False):
            st.code(err)
    else:
        st.success("Schema OK.")

with st.expander("YAML preview", expanded=False):
    try:
        preview = yaml.safe_dump(
            dump_scenario(parse_scenario(doc)),
            sort_keys=False,
            allow_unicode=True,
        )
    except Exception:
        preview = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    st.code(preview, language="yaml")

if st.button("Reload from disk (discard changes)", key="es::reload"):
    st.session_state.pop(doc_key, None)
    st.rerun()
