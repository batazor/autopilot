"""Pure directive routing: a target spec + a fleet snapshot → instance ids.

No Redis, no IO. ``DirectiveBus.post`` calls this to decide which per-instance
inboxes to LPUSH. Kept separate so routing is exhaustively unit-testable with a
plain :class:`~coord.models.FleetView`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    TARGET_ALL,
    TARGET_ALLIANCE,
    TARGET_FID,
    TARGET_INSTANCE,
)

if TYPE_CHECKING:
    from .models import DirectiveTarget, FleetView


def resolve_targets(target: DirectiveTarget, view: FleetView) -> list[str]:
    """Resolve a directive target to a list of concrete instance ids.

    * ``instance`` → that instance id verbatim (posting to an offline inbox just
      waits; the entry is not lost).
    * ``fid`` → the single ONLINE instance the account is active on, or ``[]``
      (caller decides whether to request a switch / defer).
    * ``all`` → every online instance.
    * ``alliance`` → online instances whose ``alliance_tag`` matches.
    """
    kind = target.kind
    if kind == TARGET_INSTANCE:
        return [target.value] if target.value else []
    if kind == TARGET_FID:
        iid = view.instance_for_fid(target.value)
        return [iid] if iid else []
    if kind == TARGET_ALL:
        return [i.instance_id for i in view.online_instances()]
    if kind == TARGET_ALLIANCE:
        return view.instances_for_alliance(target.value)
    return []
