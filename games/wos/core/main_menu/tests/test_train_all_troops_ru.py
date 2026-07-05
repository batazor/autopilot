"""RU «Белая мгла» troop-training flow: panel recognition + train-all wiring.

bs1 runs the Russian build (catalog ``wos_ru``), which overlays the shared
EN ``core/main_menu`` + ``troops/*`` modules with no training-specific override.
These tests pin the Cyrillic panel-row recognition the scanner needs and the
``train_all_troops`` → ``accept_troops_*`` → ``troops.*.train`` chain (resource
popup + ``timer + 1m`` re-push) so a future EN-only refactor can't silently
strand the RU build.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

MAIN_MENU_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MAIN_MENU_DIR.parents[3]
TROOPS_DIR = REPO_ROOT / "games" / "wos" / "troops"
_TROOPS = ("infantry", "lancer", "marksman")


def _load_exec_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "main_menu_exec_ru_test", MAIN_MENU_DIR / "exec.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_yaml(path: Path) -> dict:
    assert path.exists(), f"missing: {path}"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# --- RU panel-row status classification ------------------------------------
# Observed live OCR on bs1: «Завершено» garbles frame-to-frame
# ("Завертено", "ЗаВерШетО", "завершено)"), «Свободно» is clean, and the 2nd
# build queue reads «Очередь покупки» (must stay locked, never idle).
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Завершено", "completed"),
        ("завершено)", "completed"),
        ("Завертено", "completed"),   # ш→т OCR slip
        ("завершето", "completed"),   # н→т OCR slip (ЗаВерШетО)
        ("Свободно", "idle"),
        ("Очередь покупки", "locked"),
        ("Completed", "completed"),   # EN build still classifies
        ("Idle", "idle"),
    ],
)
def test_classify_status_handles_russian(text: str, kind: str) -> None:
    mod = _load_exec_module()
    assert mod._classify_status(text)[0] == kind


# --- RU panel-row title → (section, row) ------------------------------------
@pytest.mark.parametrize(
    ("title", "section", "row"),
    [
        ("Пехота Й", "training", "infantry"),
        ("Копейщики Й", "training", "lancer"),
        ("Стрелки Й", "training", "marksman"),
        ("Очередь строительства 2 Й", "building_queue", "queue_2"),
        ("Научный центр Й", "tech_research", "center"),
    ],
)
def test_section_for_row_handles_russian(title: str, section: str, row: str) -> None:
    mod = _load_exec_module()
    got_section, got_row = mod._section_for_row(title, "", "")
    assert (got_section, got_row) == (section, row)


def test_completed_ru_row_dispatches_accept() -> None:
    """A Cyrillic-garbled «Завершено» training row still owns the accept rule."""
    mod = _load_exec_module()
    kind = mod._classify_status("Завертено")[0]
    rule = mod._dispatch_rule_for("training", kind, "infantry")
    assert rule is not None
    assert mod._resolve_dispatch_scenario(rule, "infantry") == "accept_troops_infantry"


# --- train_all_troops unified entry ----------------------------------------
def test_train_all_troops_fans_out_to_three_camps() -> None:
    doc = _load_yaml(MAIN_MENU_DIR / "scenarios" / "train_all_troops.yaml")
    assert doc.get("enabled") is True
    assert doc.get("node") == "main_menu"
    pushed = [s.get("push_scenario") for s in doc.get("steps", []) if "push_scenario" in s]
    # Each troops.<type>.train now self-navigates (OCR scroll-find), collects a
    # ready batch, and restarts training — so the unified entry pushes them direct.
    assert pushed == [
        "troops.infantry.train",
        "troops.lancer.train",
        "troops.marksman.train",
    ]


# --- per-camp train: resource popup + timer+1m re-push ----------------------
@pytest.mark.parametrize("troop", _TROOPS)
def test_train_scenario_handles_resources_and_reschedules(troop: str) -> None:
    path = TROOPS_DIR / troop / "scenarios" / f"troops.{troop}.train.yaml"
    doc = _load_yaml(path)
    steps = doc.get("steps", [])

    # Resource-shortage guard: read the confirm dialog, then a cond-gated tap of
    # the use-supplies button keyed on the RU/EN shortage strings.
    ocr_dialog = [s for s in steps if s.get("ocr") == "troops.train.confirm_dialog"]
    assert ocr_dialog, f"{troop}: missing resource-dialog OCR step"
    guard = [
        s for s in steps
        if "припас" in str(s.get("cond", "")) or "use_supplies" in str(s)
    ]
    assert guard, f"{troop}: missing use-supplies cond guard"
    cond_text = str(guard[0].get("cond", ""))
    assert "припас" in cond_text and "недостаточно" in cond_text
    inner = [str(x.get("click")) for x in guard[0].get("steps", [])]
    assert "troops.train.use_supplies" in inner

    # Conveyor re-push at timer + 1 min (the user-requested cadence).
    push = next(
        (s["push_scenario"] for s in steps
         if isinstance(s.get("push_scenario"), dict)
         and s["push_scenario"].get("name") == f"troops.{troop}.train"),
        None,
    )
    assert push is not None, f"{troop}: missing self re-push"
    assert push["delay"] == f"troops.{troop}.training + 1m"


def test_train_screen_declares_resource_popup_regions() -> None:
    import json

    doc = json.loads((TROOPS_DIR / "infantry" / "area.yaml").read_text())
    names = {r["name"] for s in doc["screens"] for r in s.get("regions", [])}
    assert "troops.train.confirm_dialog" in names
    assert "troops.train.use_supplies" in names
