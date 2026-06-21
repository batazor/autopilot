"""Century Game gift-code domain: shared models, per-game scraper+redeemer.

Gift-code work is intentionally engine-level — it talks to Century Game's
HTTP APIs directly without any emulator interaction. The scheduler
(:mod:`scheduler.runner`) drives the global poller; the dashboard "Redeem
now" button reuses the same code via the DSL ``exec:`` handlers in
:mod:`century.gift_codes.exec`.

Per-game scraper + redeemer functions live in :mod:`century.gift_codes.wos`
and :mod:`century.gift_codes.kingshot`, each exposing matching
``poll_once()`` and ``run_gift_code_redeemer()`` async entry points.
"""

from century.gift_codes.models import GiftCode, GiftCodeDB, RedeemStatus

__all__ = ["GiftCode", "GiftCodeDB", "RedeemStatus"]
