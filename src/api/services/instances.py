"""Instance listing shared across API routers."""
from __future__ import annotations

from config.loader import load_settings


def list_instance_ids() -> list[str]:
    return [i.instance_id for i in load_settings().instances if i.instance_id.strip()]
