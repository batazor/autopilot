"""Parse the OCR'd daily-mission buffer into structured DailyTasks.

``chapter.claim_missions`` accumulates the daily list as text with a per-row
``(done / target)`` progress (e.g. ``+ Train 10 Infantry (0 / 10)``). This turns
that buffer into the coordinator's :class:`DailyTask`s — the structured input
:func:`coordinator.daily_bias` consumes (it had no producer otherwise) and the
dashboard's daily-tasks panel renders.

Pure: classifies each line against the same registry the router uses
(``daily_missions.yaml``), now carrying a ``category`` per mission, and reads the
``(done / target)`` the game renders. ``claimable`` is derived as done-at-read —
the list is OCR'd while pristine (before Claim All sweeps), so a completed row's
reward is still unclaimed at read time.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from games.wos.core.coordinator import DailyTask

if TYPE_CHECKING:
    from collections.abc import Sequence

_REGISTRY_PATH = Path(__file__).resolve().parent / "daily_missions.yaml"

# Trailing "(done / target)" the game renders per row; counts may carry thousands
# separators ("(0 / 50,000)"). Tolerant of the stray whitespace OCR introduces.
_PROGRESS_RE = re.compile(r"\(\s*([\d,]+)\s*/\s*([\d,]+)\s*\)")

# A compiled classification entry: (pattern, category, args). ``args`` is read
# only to disambiguate the task id by its literal troop name; a resource id comes
# from the pattern's named group.
_TaskRule = tuple["re.Pattern[str]", str, dict]


def _int(value: str) -> int:
    digits = value.replace(",", "").strip()
    return int(digits) if digits.isdigit() else 0


@lru_cache(maxsize=4)
def _load_rules_cached(path_str: str, _mtime_ns: int) -> tuple[_TaskRule, ...]:
    try:
        doc = yaml.safe_load(Path(path_str).read_text(encoding="utf-8")) or {}
    except OSError:
        return ()
    out: list[_TaskRule] = []
    for entry in doc.get("missions") or []:
        if not isinstance(entry, dict):
            continue
        raw_pat = str(entry.get("pattern") or "").strip()
        category = str(entry.get("category") or "").strip()
        if not raw_pat or not category:        # a mission with no category isn't tracked
            continue
        try:
            compiled = re.compile(raw_pat, re.IGNORECASE)
        except re.error:
            continue
        args = entry.get("args")
        out.append((compiled, category, dict(args) if isinstance(args, dict) else {}))
    return tuple(out)


def _load_rules() -> tuple[_TaskRule, ...]:
    try:
        st = _REGISTRY_PATH.stat()
    except OSError:
        return ()
    return _load_rules_cached(str(_REGISTRY_PATH), st.st_mtime_ns)


def _task_id(category: str, args: dict, match: re.Match[str]) -> str:
    """A stable id for the mission, disambiguated by troop / resource so the
    several train / gather rows don't collapse into one task."""
    troop = args.get("troop")
    if troop:
        return f"{category}:{troop}"
    resource = match.groupdict().get("resource")
    if resource:
        return f"{category}:{resource.lower()}"
    return category


def parse_daily_tasks(
    buffer: str, rules: Sequence[_TaskRule] | None = None
) -> list[DailyTask]:
    """Parse the accumulated daily-mission OCR ``buffer`` into ``DailyTask``s.

    One task per recognised line (first matching rule wins); OCR noise lines that
    match nothing are skipped. ``progress`` / ``target`` come from the rendered
    ``(done / target)`` (falling back to the pattern's ``target`` group, else 1).
    """
    reg = rules if rules is not None else _load_rules()
    tasks: list[DailyTask] = []
    seen: set[str] = set()
    for raw_line in buffer.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern, category, args in reg:
            m = pattern.search(line)
            if m is None:
                continue
            prog = _PROGRESS_RE.search(line, m.end()) or _PROGRESS_RE.search(line)
            if prog is not None:
                progress, target = _int(prog.group(1)), _int(prog.group(2))
            else:
                progress = 0
                target = _int(m.groupdict().get("target") or "")
            target = max(1, target)
            task_id = _task_id(category, args, m)
            if task_id in seen:
                break
            seen.add(task_id)
            tasks.append(DailyTask(
                id=task_id,
                category=category,
                target=target,
                progress=progress,
                claimable=progress >= target,
            ))
            break
    return tasks
