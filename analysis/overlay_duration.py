from __future__ import annotations


def parse_duration_seconds(value: object) -> int | None:
    """Parse duration into seconds.

    Accepts:
    - number: seconds (int/float)
    - string: "<num>[s|m|h|d]" (case-insensitive), e.g. "15m", "2h", "900", "0.5h"
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        sec = int(value)
        return sec if sec > 0 else None

    s = str(value).strip()
    if not s:
        return None

    mult = 1.0
    unit = s[-1].lower()
    num = s
    if unit in {"s", "m", "h", "d"}:
        num = s[:-1].strip()
        mult = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[unit]
    try:
        v = float(num)
    except (TypeError, ValueError):
        return None
    sec = int(v * mult)
    return sec if sec > 0 else None

