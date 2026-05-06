from __future__ import annotations

import logging
import threading
from pathlib import Path

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from scenarios.models import Scenario

logger = logging.getLogger(__name__)


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
        handler = _ScenarioReloadHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._path), recursive=False)
        self._observer.start()
        logger.info("Watching scenario directory: %s", self._path)

    def stop_watching(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
