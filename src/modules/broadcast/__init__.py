"""Alliance broadcast / reminders — game-agnostic core.

Periodically posts helpful messages into the in-game **alliance chat** so members
get nudged about live events, hero-development tips, and daily tasks. Triggers are
either a **cron** cadence or an **event** condition (a calendar ``event_<slug>``
flag from :mod:`games.wos.core.calendar`). The catalog lives in SQLite and is
edited from the dashboard (CRUD); there is no YAML catalog.

Layers:

* :mod:`~.models` — the pure :class:`~.models.BroadcastMessage` dataclass + enums.
* :mod:`~.engine` — pure selection: which message is *due now* (cooldown + cron +
  event ``cond`` against the player's flat state). One message per tick.
* :mod:`~.election` — pure broadcaster election: exactly one account per alliance
  posts (deterministic, lowest fid among the currently-active eligible accounts).
* :mod:`~.keys` — Redis key builders for per-alliance cooldown + claim locks.
* :mod:`~.db` — SQLite catalog + send-log (CRUD), via :mod:`config.orm`.
* :mod:`~.runner` — the IO orchestrator a per-game ``exec.py`` calls: elect →
  select → claim → navigate to ``chat.alliance`` → type → send → stamp cooldown.

Per-game delivery wrappers live under ``games/<game>/chat/exec.py``; they only
pass their ``game`` to :func:`~.runner.run_broadcast_tick`.
"""
