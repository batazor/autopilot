from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from scenarios.models import Scenario

logger = logging.getLogger(__name__)

_WATCH_LOCK = threading.RLock()
# Guard against multiple Observer instances watching the same directory in one process.
# This can happen when Streamlit reloads or when async services are started twice.
_WATCHING_PATHS: set[str] = set()

# macOS fires several modify events per save (open → write → close), and a
# multi-module commit sprays dozens of .yaml writes within milliseconds.
# Each event triggered a full ``loader.reload()`` (an ``rglob`` walk over
# every scenario root) — debouncing collapses the burst into one re-scan.
_RELOAD_DEBOUNCE_SECONDS = 0.5


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


def _observer_for_platform() -> BaseObserver:
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
        self._timer_lock = threading.Lock()
        self._pending_timer: threading.Timer | None = None
        self._pending_label: str = ""

    def _schedule_reload(self, label: str) -> None:
        with self._timer_lock:
            if self._pending_timer is not None:
                # Coalesce: cancel the in-flight timer and restart the window.
                self._pending_timer.cancel()
            self._pending_label = label
            t = threading.Timer(_RELOAD_DEBOUNCE_SECONDS, self._fire_reload)
            t.daemon = True
            self._pending_timer = t
            t.start()

    def _fire_reload(self) -> None:
        with self._timer_lock:
            label = self._pending_label
            self._pending_timer = None
            self._pending_label = ""
        logger.info("Scenario change debounced — reloading (last=%s)", label)
        self._loader.reload()

    def on_modified(self, event: FileSystemEvent) -> None:
        if str(event.src_path).endswith(".yaml"):
            self._schedule_reload(str(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if str(event.src_path).endswith(".yaml"):
            self._schedule_reload(str(event.src_path))


class ScenarioLoader:
    def __init__(self, path: Path | list[Path]) -> None:
        if isinstance(path, Path):
            self._paths = [path]
        else:
            self._paths = list(path)
        self._scenarios: list[Scenario] = []
        self._lock = threading.RLock()
        self._observers: list[BaseObserver] = []
        self._on_reload: Callable[[], None] | None = None
        self.reload(fire_callback=False)

    @property
    def _path(self) -> Path:
        """Primary root (compat) — first watched directory."""
        return self._paths[0] if self._paths else Path(".")

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
        logger.info(
            "Loaded %d scenarios (%d enabled) from %d root(s)",
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

    def start_watching(self) -> None:
        with _WATCH_LOCK, self._lock:
            for observer in self._observers:
                try:
                    if observer.is_alive():
                        observer.stop()
                        observer.join(timeout=2)
                except Exception:
                    logger.exception("Failed to stop previous scenario observer")
            self._observers = []

            handler = _ScenarioReloadHandler(self)
            for root in self._paths:
                if not root.is_dir():
                    continue
                watch_key = str(root.resolve())
                if watch_key in _WATCHING_PATHS:
                    continue
                observer = _observer_for_platform()
                try:
                    observer.schedule(handler, str(root), recursive=True)
                    observer.start()
                except RuntimeError as exc:
                    logger.warning("Scenario watcher start failed (%s): %s", root, exc)
                    continue
                self._observers.append(observer)
                _WATCHING_PATHS.add(watch_key)

    def stop_watching(self) -> None:
        with self._lock:
            for observer in self._observers:
                try:
                    observer.stop()
                    observer.join(timeout=2)
                except Exception:
                    logger.exception("Failed to stop scenario observer")
            self._observers = []
        for root in self._paths:
            try:
                watch_key = str(root.resolve())
            except OSError:
                continue
            with _WATCH_LOCK:
                _WATCHING_PATHS.discard(watch_key)
