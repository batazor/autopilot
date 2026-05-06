from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from actions.tap import BotActions
from analysis.overlay import run_overlay_analysis
from layout.bbox_percent import bbox_percent_center_to_device_point
from layout.types import Point
from tasks.base import TaskResult

logger = logging.getLogger(__name__)


def _tap_from_overlay_payload(actions: BotActions, instance_id: str, payload: dict[str, object]) -> None:
    dev_w, dev_h = actions.screen_resolution(instance_id)
    tx = payload.get("tap_x_pct")
    ty = payload.get("tap_y_pct")
    if tx is not None and ty is not None:
        point = Point(
            int(round(float(tx) / 100.0 * dev_w)),
            int(round(float(ty) / 100.0 * dev_h)),
        )
        actions.tap(instance_id, point)
        return

    # Fallback to region bbox center
    region = str(payload.get("region") or "").strip()
    if not region:
        return
    repo_root = Path(__file__).resolve().parent.parent
    area_doc = json.loads((repo_root / "area.json").read_text(encoding="utf-8"))
    screens = area_doc.get("screens") or []
    for screen in screens:
        for r in screen.get("regions") or []:
            if str(r.get("name") or "").strip() == region and isinstance(r.get("bbox"), dict):
                point = bbox_percent_center_to_device_point(r["bbox"], dev_w, dev_h)
                actions.tap(instance_id, point)
                return


@dataclass
class MailTask:
    task_id: str
    player_id: str
    priority: int = 200
    cooldown_seconds: int = 3600
    is_cooperative: bool = False
    task_type: str = field(default="mail", init=False)

    def estimate_duration(self) -> int:
        return 90

    async def execute(self, instance_id: str) -> TaskResult:
        """If the mail badge is visible, open mail and try to claim/confirm a few times.

        This task uses overlay templates (``references/analyze.yaml``) rather than OCR.
        """
        actions = BotActions()
        repo_root = Path(__file__).resolve().parent.parent

        image_bgr = actions.capture_screen_bgr(instance_id)
        overlay = await run_overlay_analysis(image_bgr, repo_root=repo_root)

        # 1) Open mail if badge is visible
        badge = overlay.get("is_has_new_mail.visible")
        if isinstance(badge, dict) and badge.get("matched"):
            logger.info("Mail badge visible → opening mail (%s)", instance_id)
            _tap_from_overlay_payload(actions, instance_id, badge)
            await asyncio.sleep(1.25)

        # 2) On mail screen (or popups), spam a small set of common confirm/claim buttons.
        # These templates are intentionally generic; we stop when nothing matches.
        taps = 0
        for _ in range(12):
            image_bgr = actions.capture_screen_bgr(instance_id)
            overlay = await run_overlay_analysis(image_bgr, repo_root=repo_root)
            did = False

            # If we're on the mail page and a gift icon is present, click it first.
            on_mail = overlay.get("mail_page_back.visible")
            big_claim = overlay.get("big_claim_button.visible")
            gift = overlay.get("mail_gift.visible")
            if (
                isinstance(on_mail, dict)
                and on_mail.get("matched")
            ):
                if isinstance(big_claim, dict) and big_claim.get("matched"):
                    logger.info("Big claim button visible → tapping (%s)", instance_id)
                    _tap_from_overlay_payload(actions, instance_id, big_claim)
                    taps += 1
                    did = True
                    await asyncio.sleep(0.8)
                    continue

                if isinstance(gift, dict) and gift.get("matched"):
                    logger.info("Mail gift visible → tapping (%s)", instance_id)
                    _tap_from_overlay_payload(actions, instance_id, gift)
                    taps += 1
                    did = True
                    await asyncio.sleep(0.8)
                    continue

            if did:
                continue

            for key in (
                "claim_button.visible",
                "confirm_button.visible",
                "upgrade_big_button.visible",
                "upgrade_button.visible",
                "build_button.visible",
                "go_button.visible",
            ):
                p = overlay.get(key)
                if isinstance(p, dict) and p.get("matched"):
                    _tap_from_overlay_payload(actions, instance_id, p)
                    taps += 1
                    did = True
                    await asyncio.sleep(0.6)
                    break

            if not did:
                break

        logger.info("Mail task done on %s/%s (taps=%d)", instance_id, self.player_id, taps)
        return TaskResult(
            success=True,
            next_run_at=datetime.now(tz=UTC) + timedelta(seconds=self.cooldown_seconds),
            metadata={"taps": taps},
        )

