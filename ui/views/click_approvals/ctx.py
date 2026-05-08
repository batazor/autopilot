from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClickApprovalsCtx:
    instance_id: str
    repo_root: Path
    area_path: Path
    analyze_path: Path

    # UI sizing
    preview_max_side: int = 360
    probe_overlay_max_side: int = 900
    region_crop_max_side: int = 220

    @property
    def hb_key(self) -> str:
        return f"wos:ui:click_approval:heartbeat:{self.instance_id}"

    @property
    def enabled_key(self) -> str:
        return f"wos:ui:click_approval:enabled:{self.instance_id}"

    @property
    def current_key(self) -> str:
        return f"wos:ui:click_approval:current:{self.instance_id}"

