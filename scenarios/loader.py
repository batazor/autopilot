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
_DSL_STEP_KEYS = frozenset(
    {
        "break",
        "click",
        "exec",
        "goto",
        "match",
        "ocr",
        "push_scenario",
        "repeat",
        "screenshot",
        "set_node",
        "sleep",
        "swipe",
        "swipe_direction",
        "tap",
        "wait",
        "while_match",
    }
)


def _is_dsl_scenario_doc(raw: object) -> bool:
    """Return True for imperative DSL YAML handled by ``DslScenarioTask``."""
    if not isinstance(raw, dict):
        return False
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        return False

    for step in steps:
        if not isinstance(step, dict):
            continue
        if any(k in step for k in _DSL_STEP_KEYS):
            return True
    return False


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
                # DSL scenarios (imperative click/wait/etc) are executed via `DslScenarioTask`
                # and must not be validated/loaded as `Scenario`.
                if _is_dsl_scenario_doc(raw):
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
