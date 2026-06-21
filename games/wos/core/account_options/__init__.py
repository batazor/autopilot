"""Registry of per-account ("per-character") feature options.

One declarative place to add a per-account toggle/choice. Append an
:class:`AccountOption` to :data:`ACCOUNT_OPTIONS` and it automatically shows up
in the dashboard options panel and the generic get/set API — **no new endpoint,
no new UI component** (that's the whole point: the surface scales with the
number of options, not the amount of code).

Each option owns:

* ``key`` — the per-gamer state key it persists to. MUST be ``planner.<name>``
  (exactly 2 levels): the state store writes into the free-form ``planner`` dict
  but doesn't auto-create deeper nesting, so a 3-level key silently no-ops.
* ``type`` — the control: ``"bool"`` (toggle) or ``"enum"`` (pick one).
* ``default`` / ``group`` (UI section) / ``choices``.

Pure: descriptors + value coercion only. Persistence (state store) lives at the
API edge; this module knows nothing of Redis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from games.wos.core.arena.opponent_filter import SETTING_KEY as _ARENA_EXCLUDE_OWN_ALLIANCE

if TYPE_CHECKING:
    from collections.abc import Mapping

BOOL = "bool"
ENUM = "enum"
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Choice:
    """One selectable value for an ``enum`` option."""

    value: str
    label: str


@dataclass(frozen=True, slots=True)
class AccountOption:
    """A single per-account option, rendered generically by the UI + API."""

    key: str                              # 2-level per-gamer state key: planner.<name>
    label: str                            # UI label
    description: str                      # UI help text
    type: str = BOOL                      # BOOL | ENUM
    default: Any = False
    group: str = "General"                # UI section header
    choices: tuple[Choice, ...] = ()      # ENUM only

    def __post_init__(self) -> None:
        if self.key.count(".") != 1 or not self.key.startswith("planner."):
            msg = f"option key must be 'planner.<name>' (2-level), got {self.key!r}"
            raise ValueError(msg)
        if self.type == ENUM and not self.choices:
            msg = f"enum option {self.key!r} needs choices"
            raise ValueError(msg)


# ── The registry. Add per-account options here. ──────────────────────────────
ACCOUNT_OPTIONS: tuple[AccountOption, ...] = (
    AccountOption(
        key=_ARENA_EXCLUDE_OWN_ALLIANCE,
        label="Skip own alliance in Arena",
        description=(
            "Don't attack players from your own alliance in Arena of Glory. "
            "Fights the first non-allied opponent and rerolls the list when "
            "every visible opponent is yours."
        ),
        type=BOOL,
        default=False,
        group="Arena",
    ),
    # ── Cross-account coordination (the fleet orchestrator) ──────────────────
    AccountOption(
        key="planner.events_participate",
        label="Join coordinated events",
        description=(
            "Include this account in alliance-coordinated event campaigns "
            "(gather points → converge → claim) during point-event windows."
        ),
        type=BOOL,
        default=False,
        group="Fleet",
    ),
    AccountOption(
        key="planner.raid_role",
        label="Farm-raid role",
        description=(
            "Role in farm-raid campaigns: farm (withdraws its troops, then is "
            "plundered) or fighter (plunders the farm). Off = not in raids. "
            "Distinct from the economy role."
        ),
        type=ENUM,
        default="off",
        group="Fleet",
        choices=(
            Choice("off", "Off"),
            Choice("farm", "Farm (gets plundered)"),
            Choice("fighter", "Fighter (plunders)"),
        ),
    ),
    AccountOption(
        key="planner.reinforce_enable",
        label="Auto-reinforce allies",
        description=(
            "Let this account be pulled in to send reinforcements when an ally "
            "is attacked (reactive, time-critical)."
        ),
        type=BOOL,
        default=False,
        group="Fleet",
    ),
)

_BY_KEY: dict[str, AccountOption] = {o.key: o for o in ACCOUNT_OPTIONS}


def option_by_key(key: str) -> AccountOption | None:
    """The registered option for ``key``, or ``None`` if unknown."""
    return _BY_KEY.get(key)


def coerce_value(option: AccountOption, raw: Any) -> Any:
    """Validate + coerce a raw value (from JSON or state) to the option's type.

    Raises :class:`ValueError` for an enum value that isn't a registered choice.
    Bool coercion is lenient (accepts ``true``/``1``/``yes``/``on`` strings) so a
    value round-tripped through Redis/JSON still reads correctly.
    """
    if option.type == BOOL:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        return str(raw).strip().lower() in _TRUTHY
    if option.type == ENUM:
        value = str(raw)
        valid = {c.value for c in option.choices}
        if value not in valid:
            msg = f"{value!r} is not a valid choice for {option.key!r} ({sorted(valid)})"
            raise ValueError(msg)
        return value
    msg = f"unknown option type {option.type!r}"
    raise ValueError(msg)


def current_value(option: AccountOption, flat_state: Mapping[str, Any]) -> Any:
    """The option's current value from a gamer's flat state, or its default."""
    raw = flat_state.get(option.key)
    if raw is None:
        return option.default
    try:
        return coerce_value(option, raw)
    except ValueError:
        return option.default


def descriptor(option: AccountOption) -> dict[str, Any]:
    """Static, value-free shape for the UI to render the control."""
    return {
        "key": option.key,
        "label": option.label,
        "description": option.description,
        "type": option.type,
        "default": option.default,
        "group": option.group,
        "choices": [{"value": c.value, "label": c.label} for c in option.choices],
    }


def options_for_state(flat_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every option with its current value, ready for the API/UI."""
    rows: list[dict[str, Any]] = []
    for option in ACCOUNT_OPTIONS:
        row = descriptor(option)
        row["value"] = current_value(option, flat_state)
        rows.append(row)
    return rows
