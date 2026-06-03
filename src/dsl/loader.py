from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from dsl.models import Scenario

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _is_declarative_scenario_doc(raw: object) -> bool:
    """Return True for legacy scheduler scenarios handled by ``ScenarioEvaluator``.

    This is a whitelist for the declarative schema, not a DSL command skip-list.
    Imperative DSL YAMLs are owned by ``DslScenarioTask`` and do not have
    ``task``/``cooldown`` on every step.
    """
    if not isinstance(raw, dict):
        return False
    raw_doc: dict[str, Any] = cast("dict[str, Any]", raw)
    steps = raw_doc.get("steps")
    if not isinstance(steps, list) or not steps:
        return False

    for step in steps:
        if not isinstance(step, dict):
            return False
        if "task" not in step or "cooldown" not in step:
            return False
    return True


class ScenarioLoader:
    def __init__(self, path: Path | list[Path]) -> None:
        if isinstance(path, Path):
            self._paths = [path]
        else:
            self._paths = list(path)
        self._scenarios: list[Scenario] = []
        self._lock = threading.RLock()
        self._on_reload: Callable[[], None] | None = None
        self.reload(fire_callback=False)

    @property
    def _path(self) -> Path:
        """Primary root (compat) — first watched directory."""
        return self._paths[0] if self._paths else Path()

    def set_on_reload(self, callback: Callable[[], None] | None) -> None:
        """Register a no-arg callback fired after every reload()."""
        self._on_reload = callback

    def reload(self, *, fire_callback: bool = True) -> None:
        loaded: list[Scenario] = []
        seen_stems: set[tuple[str, str]] = set()
        for root in self._paths:
            if not root.is_dir():
                continue
            for yaml_file in sorted(root.rglob("*.yaml")):
                if "drafts" in {p.lower() for p in yaml_file.parts}:
                    continue
                dedup_key = (str(root.resolve()), yaml_file.stem)
                if dedup_key in seen_stems:
                    continue
                try:
                    raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                    if not _is_declarative_scenario_doc(raw):
                        if isinstance(raw, dict) and str(raw.get("kind") or "").strip() == "scenario":
                            logger.error(
                                "Declarative scenario has invalid schema: %s "
                                "(each step must define `task` and `cooldown`)",
                                yaml_file,
                            )
                        continue
                    if isinstance(raw, dict) and "name" not in raw:
                        raw["name"] = yaml_file.stem
                    scenario = Scenario.model_validate(raw)
                    seen_stems.add(dedup_key)
                    loaded.append(scenario)
                except Exception:
                    logger.exception("Failed to load scenario %s", yaml_file)
        with self._lock:
            self._scenarios = loaded
            cb = self._on_reload
        n_on = sum(1 for s in loaded if s.enabled)
        roots_label = ", ".join(str(p) for p in self._paths)
        # This loader only handles the legacy *declarative* schema (every step
        # has ``task``+``cooldown``, run by ``ScenarioEvaluator``). Imperative
        # DSL scenarios (``match``/``click``/``while_match``) are owned by
        # ``DslScenarioTask`` and loaded elsewhere — so 0 here is normal when a
        # game ships only imperative scenarios, not a discovery failure.
        logger.info(
            "Loaded %d declarative scheduler scenarios (%d enabled) from %d root(s)",
            len(loaded),
            n_on,
            len(self._paths),
        )
        logger.debug("Scenario roots: %s", roots_label)
        if fire_callback and cb is not None:
            try:
                cb()
            except Exception:
                logger.debug("ScenarioLoader on_reload callback failed", exc_info=True)

    def load_all(self, path: Path | list[Path] | None = None) -> list[Scenario]:
        if path is not None:
            self._paths = [path] if isinstance(path, Path) else list(path)
            self.reload()
        with self._lock:
            return list(self._scenarios)
