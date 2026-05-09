from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from scenarios.models import Scenario

logger = logging.getLogger(__name__)

_WATCH_LOCK = threading.RLock()
# Guard against multiple Observer instances watching the same directory in one process.
# This can happen when Streamlit reloads or when async services are started twice.
_WATCHING_PATHS: set[str] = set()


def _is_declarative_scenario_doc(raw: object) -> bool:
    """Return True for legacy scheduler scenarios handled by ``ScenarioEvaluator``.

    This is a whitelist for the declarative schema, not a DSL command skip-list.
    Imperative DSL YAMLs are owned by ``DslScenarioTask`` and do not have
    ``task``/``cooldown`` on every step.
    """
    if not isinstance(raw, dict):
        return False
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        return False

    for step in steps:
        if not isinstance(step, dict):
            return False
        if "task" not in step or "cooldown" not in step:
            return False
    return True


def _observer_for_platform() -> Observer:
    """macOS FSEvents can error with "already scheduled" if the same tree is watched twice
    (e.g. scheduler restart without tearing down the previous native watch). Polling avoids that.
    """
    if sys.platform == "darwin":
        from watchdog.observers.polling import PollingObserver

        return PollingObserver(timeout=1.0)  # type: ignore[return-value]
    return Observer()


class _ScenarioReloadHandler(FileSystemEventHandler):
    def __init__(self, loader: ScenarioLoader) -> None:
        super().__init__()
        self._loader = loader

    def on_modified(self, event: FileSystemEvent) -> None:
        if str(event.src_path).endswith(".yaml"):
            logger.info("Scenario changed: %s — reloading", event.src_path)
            self._loader.reload()

    def on_created(self, event: FileSystemEvent) -> None:
        if str(event.src_path).endswith(".yaml"):
            logger.info("New scenario: %s — reloading", event.src_path)
            self._loader.reload()


class ScenarioLoader:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._scenarios: list[Scenario] = []
        self._lock = threading.RLock()
        self._observer: Observer | None = None
        self.reload()

    def reload(self) -> None:
        loaded: list[Scenario] = []
        for yaml_file in sorted(self._path.rglob("*.yaml")):
            # Draft scenarios are not executable and must never be auto-loaded.
            if "drafts" in {p.lower() for p in yaml_file.parts}:
                continue
            try:
                raw = yaml.safe_load(yaml_file.read_text())
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
                loaded.append(scenario)
            except Exception:
                logger.exception("Failed to load scenario %s", yaml_file)
        with self._lock:
            self._scenarios = loaded
        n_on = sum(1 for s in loaded if s.enabled)
        logger.info("Loaded %d scenarios (%d enabled) from %s", len(loaded), n_on, self._path)

    def load_all(self, path: Path | None = None) -> list[Scenario]:
        if path is not None and path != self._path:
            self._path = path
            self.reload()
        with self._lock:
            return list(self._scenarios)

    def start_watching(self) -> None:
        watch_key = str(self._path.resolve())
        with _WATCH_LOCK:
            if watch_key in _WATCHING_PATHS:
                return
            with self._lock:
                if self._observer is not None:
                    if self._observer.is_alive():
                        _WATCHING_PATHS.add(watch_key)
                        return
                    try:
                        self._observer.stop()
                        self._observer.join(timeout=2)
                    except Exception:
                        logger.exception("Failed to stop previous scenario observer")
                    self._observer = None

                handler = _ScenarioReloadHandler(self)
                self._observer = _observer_for_platform()
                try:
                    self._observer.schedule(handler, str(self._path), recursive=True)
                    self._observer.start()
                except RuntimeError as exc:
                    logger.warning("Scenario watcher start failed (%s): %s", self._path, exc)
                    self._observer = None
                    return
                _WATCHING_PATHS.add(watch_key)
        logger.info("Watching scenario directory: %s", self._path)

    def stop_watching(self) -> None:
        with self._lock:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=2)
                self._observer = None
        try:
            watch_key = str(self._path.resolve())
        except OSError:
            return
        with _WATCH_LOCK:
            _WATCHING_PATHS.discard(watch_key)
