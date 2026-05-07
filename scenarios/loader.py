from __future__ import annotations

import logging
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
        for yaml_file in sorted(self._path.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_file.read_text())
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
                    with _WATCH_LOCK:
                        _WATCHING_PATHS.add(watch_key)
                    return
                try:
                    self._observer.stop()
                    self._observer.join(timeout=2)
                except Exception:
                    logger.exception("Failed to stop previous scenario observer")
                self._observer = None

            handler = _ScenarioReloadHandler(self)
            self._observer = Observer()
            try:
                self._observer.schedule(handler, str(self._path), recursive=False)
                self._observer.start()
            except RuntimeError as exc:
                # watchdog fsevents can raise "already scheduled" if something else
                # in this process already registered the same watch.
                logger.warning("Scenario watcher start failed (%s): %s", self._path, exc)
                self._observer = None
                return
            with _WATCH_LOCK:
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
