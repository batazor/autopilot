"""Tap execution for :class:`navigation.navigator.Navigator`.

Turns a navigation step (labelled region / template icon / tab by index or
identified page / calendar-Go walk / main-menu panel row) into a device tap,
routing through the click-approval surface when the tap callable supports it
(detected by signature introspection). Extracted from the Navigator god-class;
mutable dependencies (tap/capture/ocr/area-doc/system-back/region-visible) are
read live from the owning Navigator through properties, so the methods move here
verbatim and test reassignments of those attributes are still observed.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_random_point_to_device_point
from layout.tabs_strip_identifier import (
    discover_tab_templates,
    identify_tabs_by_template,
)
from layout.tabs_strip_segmenter import detect_tabs_in_strip
from layout.types import Point
from navigation.screen_graph import screen_family_for

if TYPE_CHECKING:
    import numpy as np

    from navigation.navigator import Navigator

logger = logging.getLogger(__name__)

_NAV_TAP_SETTLE_S = 0.4
_TAB_IDENTIFY_MAX_ADVANCE = 8
"""Max strip ``advance`` scrolls while hunting a tab by template before giving
up (the navigator then falls back to main_city and re-enters the family fresh,
which resets the strip to its leftmost position). Generous enough to walk a
~12-tab Shop strip that shows 3-4 tabs per view."""

_TAB_IDENTIFY_STRIP_MOVE_EPS = 4.0
"""Mean abs gray diff (0-255) below which a tab-strip swipe is treated as "no
movement" — i.e. a swipe-only carousel hit an end. Lets swipe-advance stop and
reverse instead of swiping into a wall forever."""


def _strip_signature(image: Any, bbox: dict[str, Any]) -> Any:
    """Small grayscale signature of the tab-strip region, for swipe end-detection."""
    import cv2

    h, w = image.shape[:2]
    x0 = max(0, int(float(bbox["x"]) / 100.0 * w))
    y0 = max(0, int(float(bbox["y"]) / 100.0 * h))
    x1 = min(w, int((float(bbox["x"]) + float(bbox["width"])) / 100.0 * w))
    y1 = min(h, int((float(bbox["y"]) + float(bbox["height"])) / 100.0 * h))
    patch = image[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    return cv2.resize(gray, (120, 16), interpolation=cv2.INTER_AREA)


def _strip_moved(a: Any, b: Any) -> bool:
    """True if the strip changed between two signatures (swipe scrolled it)."""
    if a is None or b is None:
        return True
    import cv2

    return float(cv2.absdiff(a, b).mean()) > _TAB_IDENTIFY_STRIP_MOVE_EPS


class TapExecutor:
    """Executes navigation taps on behalf of (and reading state from) a Navigator."""

    def __init__(self, navigator: Navigator) -> None:
        self._nav = navigator
        self._tap_accepts_approval_source: bool | None = None
        self._tap_accepts_revalidate: bool | None = None

    # Live views onto the owning Navigator so reassignments (and test patches)
    # of these attributes are picked up on every call.
    @property
    def _tap(self):  # noqa: ANN202
        return self._nav._tap

    @property
    def _capture(self):  # noqa: ANN202
        return self._nav._capture

    @property
    def _ocr(self):  # noqa: ANN202
        return self._nav._ocr

    @property
    def _system_back(self):  # noqa: ANN202
        return self._nav._system_back

    @property
    def _repo_root(self):  # noqa: ANN202
        return self._nav._repo_root

    @property
    def _load_area_doc(self):  # noqa: ANN202
        return self._nav._load_area_doc

    @property
    def _region_visible_async(self):  # noqa: ANN202
        return self._nav._region_visible_async

    @property
    def _swipe(self):  # noqa: ANN202
        return self._nav._swipe

    async def _swipe_tab_strip_async(
        self, instance_id: str, bbox: dict[str, Any], *, forward: bool
    ) -> bool:
        """Swipe the tab strip horizontally to scroll a swipe-only carousel.

        ``forward`` reveals tabs to the right (drag right→left); ``not forward``
        reveals tabs to the left / toward the start (drag left→right). Swipes
        bypass click-approval (they aren't taps), like other navigation scrolls.
        """
        swipe = self._swipe
        if swipe is None:
            return False
        dev_w = int(bbox.get("original_width") or 720)
        dev_h = int(bbox.get("original_height") or 1280)
        y = int(round((float(bbox["y"]) + float(bbox["height"]) / 2.0) / 100.0 * dev_h))
        x_lo = int(round((float(bbox["x"]) + float(bbox["width"]) * 0.18) / 100.0 * dev_w))
        x_hi = int(round((float(bbox["x"]) + float(bbox["width"]) * 0.82) / 100.0 * dev_w))
        start, end = (
            (Point(x_hi, y), Point(x_lo, y))
            if forward
            else (Point(x_lo, y), Point(x_hi, y))
        )
        return bool(await asyncio.to_thread(swipe, instance_id, start, end))

    def _tap_region_name(
        self,
        instance_id: str,
        region_name: str,
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        area_doc = self._load_area_doc()
        tap_variant = f"{region_name}_tap"
        pair = screen_region_by_name(
            area_doc, tap_variant, state_flat=state_flat
        ) or screen_region_by_name(area_doc, region_name, state_flat=state_flat)
        if pair is None:
            logger.warning("Navigator: unknown region %r in area.json", region_name)
            return False
        _entry, reg = pair
        bbox = reg.get("bbox")
        if not isinstance(bbox, dict):
            logger.warning("Navigator: region %r missing bbox", region_name)
            return False
        if bool(reg.get("isSearch")):
            match = self._match_search_region_for_tap(
                instance_id,
                str(reg.get("name") or region_name),
                threshold=reg.get("threshold", 0.9),
                state_flat=state_flat,
            )
            if match is None:
                logger.info(
                    "Navigator: dynamic region %r not visible — cancelling navigation tap",
                    region_name,
                )
                return False
            pt = match
        else:
            pt = None
        try:
            dev_w = int(bbox["original_width"])
            dev_h = int(bbox["original_height"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Navigator: region %r missing original_width/original_height",
                region_name,
            )
            return False
        if pt is None:
            pt = bbox_percent_random_point_to_device_point(bbox, dev_w, dev_h)
        approval_context: dict[str, Any] = {}
        if from_screen:
            approval_context["from_screen"] = from_screen
        if to_screen:
            approval_context["to_screen"] = to_screen
        # Full path (CSV of screen ids) + 1-based index of the destination
        # being tapped right now. Both are optional — single-hop / non-route
        # taps via ``_tap_region_name`` skip them and the approvals UI falls
        # back to rendering just the local edge as before.
        if path_csv:
            approval_context["path"] = path_csv
        if hop_index is not None:
            approval_context["hop_index"] = str(hop_index)
        from layout.area_lookup import region_tap_hold_ms

        hold_ms = region_tap_hold_ms(reg)
        if self._tap_supports_approval_source():
            tap_kwargs: dict[str, Any] = {
                "approval_region": str(reg.get("name") or region_name),
                "approval_source": "navigation",
                "approval_context": approval_context,
            }
            if hold_ms > 0:
                tap_kwargs["hold_ms"] = hold_ms
            return bool(self._tap(instance_id, pt, **tap_kwargs))  # type: ignore[operator]
        return bool(
            self._tap(
                instance_id,
                pt,
                approval_region=str(reg.get("name") or region_name),
            )
        )  # type: ignore[operator]

    def _match_search_region_for_tap(
        self,
        instance_id: str,
        region_name: str,
        *,
        threshold: Any,
        state_flat: dict[str, Any] | None,
    ) -> Point | None:
        """Resolve an ``isSearch`` tap region through findIcon before clicking."""
        try:
            image = self._capture(instance_id)  # type: ignore[operator]
        except Exception:
            logger.warning(
                "Navigator: capture failed before dynamic region tap %s",
                region_name,
                exc_info=True,
            )
            return None
        rule = {
            "name": "navigator.dynamic_region_tap",
            "action": "findIcon",
            "region": region_name,
            "threshold": threshold,
        }
        try:
            out = asyncio.run(
                evaluate_overlay_rules_async(
                    image,
                    self._load_area_doc(),
                    self._repo_root,
                    [rule],
                    state_flat=state_flat,
                )
            )
        except Exception:
            logger.warning(
                "Navigator: findIcon failed before dynamic region tap %s",
                region_name,
                exc_info=True,
            )
            return None
        hit = out.get("navigator.dynamic_region_tap")
        if not isinstance(hit, dict) or not hit.get("matched"):
            return None
        try:
            x_pct = float(hit["tap_match_x_pct"])
            y_pct = float(hit["tap_match_y_pct"])
        except (KeyError, TypeError, ValueError):
            logger.info(
                "Navigator: dynamic region match missing tap coords for %s: %s",
                region_name,
                hit,
            )
            return None
        h, w = int(image.shape[0]), int(image.shape[1])
        return Point(
            int(round(x_pct / 100.0 * w)),
            int(round(y_pct / 100.0 * h)),
        )

    async def _tap_region_name_async(
        self,
        instance_id: str,
        region_name: str,
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        """Run ADB tap/approval wait off the event loop so rolling preview keeps ticking."""
        return bool(
            await asyncio.to_thread(
                self._tap_region_name,
                instance_id,
                region_name,
                from_screen=from_screen,
                to_screen=to_screen,
                state_flat=state_flat,
                path_csv=path_csv,
                hop_index=hop_index,
            )
        )

    async def _tap_any_of_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        """Tap the first of several alternative regions that is actually on screen.

        Models a transition triggerable by **any of** several buttons (the screen
        graph keys edges by destination, so two buttons that both open the same
        screen can't be two edges — they're one edge with alternative taps). Each
        candidate is presence-checked with findIcon at its own threshold; the
        first visible one is tapped. Returns ``False`` when none are visible so
        the navigator can retry / reroute instead of tapping blindly.
        """
        regions = [str(r) for r in (spec.get("regions") or [])]
        area_doc = self._load_area_doc()
        for region_name in regions:
            pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
            threshold = pair[1].get("threshold", 0.9) if pair is not None else 0.9
            present = await asyncio.to_thread(
                self._match_search_region_for_tap,
                instance_id,
                region_name,
                threshold=threshold,
                state_flat=state_flat,
            )
            if present is not None:
                return await self._tap_region_name_async(
                    instance_id,
                    region_name,
                    from_screen=from_screen,
                    to_screen=to_screen,
                    state_flat=state_flat,
                    path_csv=path_csv,
                    hop_index=hop_index,
                )
        logger.info(
            "Navigator: any_of — none of %s visible on %s; aborting hop",
            regions,
            instance_id,
        )
        return False

    def _tap_template_icon(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        image: np.ndarray,
        from_screen: str | None = None,
        to_screen: str | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        hit = spec.get("_match")
        if not isinstance(hit, dict) or not hit.get("matched"):
            logger.info("Navigator: template_icon tap has no successful match: %s", spec)
            return False
        try:
            x_pct = float(hit["tap_match_x_pct"])
            y_pct = float(hit["tap_match_y_pct"])
        except (KeyError, TypeError, ValueError):
            logger.info("Navigator: template_icon match missing tap coordinates: %s", hit)
            return False
        h, w = int(image.shape[0]), int(image.shape[1])
        point = Point(
            int(round(x_pct / 100.0 * w)),
            int(round(y_pct / 100.0 * h)),
        )
        approval_context: dict[str, Any] = {}
        if from_screen:
            approval_context["from_screen"] = from_screen
        if to_screen:
            approval_context["to_screen"] = to_screen
        if path_csv:
            approval_context["path"] = path_csv
        if hop_index is not None:
            approval_context["hop_index"] = str(hop_index)
        region = str(spec.get("region") or hit.get("region") or "template_icon")

        # Re-validate the template match right before the tap fires. With
        # ``click_approval`` enabled, minutes can pass between the original
        # match (computed at scenario start) and the operator's approve —
        # event icons that rotate position would already be elsewhere by
        # then, and the recorded ``point`` would tap empty space. The
        # revalidate hook re-captures, re-runs ``findIcon`` against the
        # same template + threshold, and cancels the tap when the icon is
        # no longer there. The navigator returns ``False`` so
        # ``_navigate_to_node`` treats this as ``navigation_failed`` and
        # the scenario aborts cleanly.
        def _revalidate_match() -> bool:
            try:
                fresh = self._capture(instance_id)  # type: ignore[operator]
            except Exception:
                logger.warning(
                    "Navigator revalidate: capture failed for %s", instance_id, exc_info=True
                )
                return False
            rule: dict[str, Any] = {
                "name": "navigator.template_icon.revalidate",
                "action": "findIcon",
                "region": str(spec.get("region") or "").strip(),
                "template": str(spec.get("template") or "").strip(),
                "threshold": spec.get("threshold", 0.9),
            }
            if "search_region" in spec:
                rule["search_region"] = spec["search_region"]
            try:
                fresh_out = asyncio.run(
                    evaluate_overlay_rules_async(
                        fresh,
                        self._load_area_doc(),
                        self._repo_root,
                        [rule],
                    )
                )
            except Exception:
                logger.warning(
                    "Navigator revalidate: evaluate_overlay_rules_async raised for %s",
                    spec, exc_info=True,
                )
                return False
            fresh_hit = fresh_out.get("navigator.template_icon.revalidate")
            still = bool(isinstance(fresh_hit, dict) and fresh_hit.get("matched"))
            if not still:
                logger.info(
                    "Navigator revalidate: template no longer matches — cancelling tap (%s)",
                    spec.get("template"),
                )
            return still

        revalidate = (
            _revalidate_match if self._tap_supports_revalidate() else None
        )
        if self._tap_supports_approval_source():
            return bool(
                self._tap(
                    instance_id,
                    point,
                    approval_region=region,
                    approval_source="navigation",
                    approval_context=approval_context,
                    **({"revalidate": revalidate} if revalidate is not None else {}),
                )
            )  # type: ignore[operator]
        return bool(
            self._tap(
                instance_id,
                point,
                approval_region=region,
                **({"revalidate": revalidate} if revalidate is not None else {}),
            )
        )  # type: ignore[operator]

    async def _tap_template_icon_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        image: np.ndarray = self._capture(instance_id)  # type: ignore[operator]
        rule: dict[str, Any] = {
            "name": "navigator.template_icon",
            "action": "findIcon",
            "region": str(spec.get("region") or "").strip(),
            "template": str(spec.get("template") or "").strip(),
            "threshold": spec.get("threshold", 0.9),
        }
        for key in ("search_region",):
            if key in spec:
                rule[key] = spec[key]
        try:
            out = await evaluate_overlay_rules_async(
                image,
                self._load_area_doc(),
                self._repo_root,
                [rule],
                state_flat=state_flat,
            )
        except Exception:
            logger.debug("Navigator: template_icon match failed for %s", spec, exc_info=True)
            return False
        hit = out.get("navigator.template_icon")
        if not isinstance(hit, dict) or not hit.get("matched"):
            logger.info("Navigator: template_icon did not match: %s", hit)
            return False
        spec_with_match = dict(spec)
        spec_with_match["_match"] = hit
        return bool(
            await asyncio.to_thread(
                self._tap_template_icon,
                instance_id,
                spec_with_match,
                image=image,
                from_screen=from_screen,
                to_screen=to_screen,
                path_csv=path_csv,
                hop_index=hop_index,
            )
        )

    async def _tap_tab_index_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        region_name = str(spec.get("region") or "").strip()
        try:
            target_index = int(spec["index"])
        except (KeyError, TypeError, ValueError):
            logger.info("Navigator: tab_index spec missing index: %s", spec)
            return False
        area_doc = self._load_area_doc()
        pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
        bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
        if not region_name or not isinstance(bbox, dict):
            logger.info("Navigator: tab_index region unavailable: %s", spec)
            return False

        image: np.ndarray = await asyncio.to_thread(self._capture, instance_id)  # type: ignore[arg-type]
        tabs = detect_tabs_in_strip(image, bbox)
        tab = next((t for t in tabs if t.index == target_index), None)
        if tab is None:
            logger.info(
                "Navigator: tab_index=%d not detected in region=%s tabs=%s",
                target_index,
                region_name,
                [t.index for t in tabs],
            )
            return False

        # Click the capsule-tight rectangle so the tap centre lands on the tab
        # body, not the padding the full strip bbox includes above/below it.
        b = tab.tap_bbox_percent or tab.bbox_percent
        h, w = int(image.shape[0]), int(image.shape[1])
        point = Point(
            int(round((float(b["x"]) + float(b["width"]) / 2.0) / 100.0 * w)),
            int(round((float(b["y"]) + float(b["height"]) / 2.0) / 100.0 * h)),
        )
        approval_context: dict[str, Any] = {}
        if from_screen:
            approval_context["from_screen"] = from_screen
        if to_screen:
            approval_context["to_screen"] = to_screen
        if path_csv:
            approval_context["path"] = path_csv
        if hop_index is not None:
            approval_context["hop_index"] = str(hop_index)
        approval_context["tab_index"] = str(target_index)

        tap_kwargs: dict[str, Any] = {"approval_region": region_name}
        if self._tap_supports_approval_source():
            tap_kwargs["approval_source"] = "navigation"
            tap_kwargs["approval_context"] = approval_context
        return bool(
            await asyncio.to_thread(self._tap, instance_id, point, **tap_kwargs)  # type: ignore[arg-type]
        )

    async def _tap_tab_identify_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        """Navigate to a family tab found by template, scrolling the strip.

        Detect → identify each tab by its per-page icon → click the tab whose
        identified page matches the target. When the target is not on screen,
        tap the family ``next_region`` advance arrow and retry, up to
        :data:`_TAB_IDENTIFY_MAX_ADVANCE` times. Returning ``False`` lets the
        navigator fall back through main_city (which re-enters the family fresh,
        resetting the strip), so a target that scrolled past is still reachable.
        """
        region_name = str(spec.get("region") or "").strip()
        target_page = str(spec.get("page") or to_screen or "").strip()
        namespace = str(spec.get("namespace") or "").strip()
        if not namespace and "." in region_name:
            namespace = region_name.split(".", 1)[0]
        if not region_name or not target_page:
            logger.info("Navigator: tab_identify spec incomplete: %s", spec)
            return False

        area_doc = self._load_area_doc()
        pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
        bbox = pair[1].get("bbox") if pair and isinstance(pair[1], dict) else None
        if not isinstance(bbox, dict):
            logger.info("Navigator: tab_identify region unavailable: %s", spec)
            return False

        # How to advance the strip when the target isn't visible. From the
        # target's screen-family config: ``advance: swipe`` drives a swipe-only
        # carousel (e.g. the events panel); otherwise tap the ``next_region``
        # arrow (Shop/Deals). Single source of truth shared with the analyzer.
        next_region = ""
        advance_swipe = False
        fam = screen_family_for(target_page)
        if fam is not None:
            next_region = str(fam[1].get("next_region") or "").strip()
            advance_swipe = str(fam[1].get("advance") or "").strip() == "swipe"

        templates = discover_tab_templates(
            area_doc, self._repo_root, bbox, namespace=namespace
        )
        if not templates:
            logger.info(
                "Navigator: tab_identify found no templates for namespace=%s",
                namespace,
            )
            return False

        # Swipe-advance state: sweep one way until the strip stops moving (end of
        # carousel), then reverse to cover the other end before giving up.
        swept_both = False
        direction_back = True  # start toward the strip's start (left)
        last_sig: Any = None

        for attempt in range(_TAB_IDENTIFY_MAX_ADVANCE + 1):
            image: np.ndarray = await asyncio.to_thread(self._capture, instance_id)  # type: ignore[arg-type]
            tabs = detect_tabs_in_strip(image, bbox)
            ids = identify_tabs_by_template(image, tabs, templates)
            target_tab = next(
                (t for t in tabs if ids.get(t.index) == target_page), None
            )
            if target_tab is not None:
                b = target_tab.tap_bbox_percent or target_tab.bbox_percent
                h, w = int(image.shape[0]), int(image.shape[1])
                point = Point(
                    int(round((float(b["x"]) + float(b["width"]) / 2.0) / 100.0 * w)),
                    int(round((float(b["y"]) + float(b["height"]) / 2.0) / 100.0 * h)),
                )
                approval_context: dict[str, Any] = {}
                if from_screen:
                    approval_context["from_screen"] = from_screen
                if to_screen:
                    approval_context["to_screen"] = to_screen
                if path_csv:
                    approval_context["path"] = path_csv
                if hop_index is not None:
                    approval_context["hop_index"] = str(hop_index)
                approval_context["tab_page"] = target_page
                tap_kwargs: dict[str, Any] = {"approval_region": region_name}
                if self._tap_supports_approval_source():
                    tap_kwargs["approval_source"] = "navigation"
                    tap_kwargs["approval_context"] = approval_context
                return bool(
                    await asyncio.to_thread(self._tap, instance_id, point, **tap_kwargs)  # type: ignore[arg-type]
                )

            # Target not on screen — advance the strip.
            if advance_swipe:
                if self._swipe is None:
                    logger.info(
                        "Navigator: tab_identify %r needs swipe-advance but no "
                        "swipe_fn is wired",
                        target_page,
                    )
                    return False
                sig = _strip_signature(image, bbox)
                if last_sig is not None and not _strip_moved(last_sig, sig):
                    # Previous swipe didn't move the strip → this end is reached.
                    if swept_both:
                        logger.info(
                            "Navigator: tab_identify %r not found after sweeping "
                            "both ends of %s (identified=%s)",
                            target_page,
                            region_name,
                            sorted(set(ids.values())),
                        )
                        return False
                    swept_both = True
                    direction_back = not direction_back
                last_sig = sig
                logger.info(
                    "Navigator: tab_identify swipe-advancing (%s) to find %r "
                    "(attempt %d)",
                    "back" if direction_back else "forward",
                    target_page,
                    attempt + 1,
                )
                await self._swipe_tab_strip_async(
                    instance_id, bbox, forward=not direction_back
                )
                await asyncio.sleep(_NAV_TAP_SETTLE_S)
                continue

            # Arrow-advance (Shop/Deals): scroll the strip if the arrow shows.
            if not next_region or not await self._region_visible_async(
                image, next_region, state_flat=state_flat
            ):
                logger.info(
                    "Navigator: tab_identify %r not found in %s (identified=%s); "
                    "no further advance",
                    target_page,
                    region_name,
                    sorted(set(ids.values())),
                )
                return False
            logger.info(
                "Navigator: tab_identify advancing %s to find %r (attempt %d)",
                next_region,
                target_page,
                attempt + 1,
            )
            if not await self._tap_region_name_async(
                instance_id,
                next_region,
                state_flat=state_flat,
                from_screen=from_screen,
                to_screen=to_screen,
            ):
                return False
            await asyncio.sleep(_NAV_TAP_SETTLE_S)
        return False

    async def _tap_calendar_go_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        """Reach an event by tapping its calendar bar and the popup's Go button.

        Delegates the interactive walk to the calendar module (it needs swipe +
        OCR, which the bot-action surface provides). Aliases come from the spec's
        ``aliases`` and/or an ``event`` slug (de-slugged to words for matching).
        """
        _ = (from_screen, to_screen, state_flat, path_csv, hop_index)
        aliases: list[str] = []
        raw = spec.get("aliases")
        if isinstance(raw, str):
            aliases.append(raw)
        elif isinstance(raw, list):
            aliases.extend(str(a) for a in raw)
        event_id = str(spec.get("event") or "").strip()
        if event_id:
            aliases.append(event_id.replace("_", " "))  # slug → words for fuzzy match
        aliases = [a for a in (a.strip() for a in aliases) if a]
        if not aliases:
            logger.info("Navigator: calendar_go spec has no aliases/event: %s", spec)
            return False
        try:
            from games.wos.core.calendar.go_nav import navigate_via_go

            from tasks import dsl_runtime

            actions = dsl_runtime.bot_actions()
            threshold = float(spec.get("threshold", 0.72))
            return bool(
                await navigate_via_go(
                    actions, instance_id, self._ocr._run_tesseract, aliases, threshold=threshold
                )
            )
        except Exception:
            logger.exception("Navigator: calendar_go navigation failed: %s", spec)
            return False

    async def _tap_goto_calendar_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        """Open the Events panel and select the (always-leftmost) Calendar tab.

        Delegates the interactive walk to the calendar module (it needs swipe +
        OCR, which the bot-action surface provides). The navigator verifies
        ``event.calendar`` after this returns.
        """
        _ = (spec, from_screen, to_screen, state_flat, path_csv, hop_index)
        try:
            from games.wos.core.calendar.open_nav import open_calendar_tab

            from tasks import dsl_runtime

            actions = dsl_runtime.bot_actions()
            return bool(await open_calendar_tab(actions, instance_id, self._ocr))
        except Exception:
            logger.exception("Navigator: goto_calendar navigation failed: %s", spec)
            return False

    async def _tap_main_menu_panel_row_async(
        self,
        instance_id: str,
        spec: dict[str, Any],
        *,
        from_screen: str | None = None,
        to_screen: str | None = None,
        state_flat: dict[str, Any] | None = None,
        path_csv: str | None = None,
        hop_index: int | None = None,
    ) -> bool:
        _ = state_flat
        section = str(spec.get("section") or "").strip().lower()
        row = str(spec.get("row") or "").strip().lower()
        rows_raw = spec.get("rows")
        rows = {
            str(r).strip().lower()
            for r in rows_raw
            if str(r).strip()
        } if isinstance(rows_raw, list) else set()
        if row:
            rows.add(row)
        if not section or not rows:
            logger.info("Navigator: main_menu_panel_row spec incomplete: %s", spec)
            return False

        try:
            # ``games`` is a repo-root namespace package, but installed entry
            # points (botctl, ``uv run bot``) only put ``src/`` on sys.path — so the
            # dotted import below raises ModuleNotFoundError until the repo root is
            # added. The DSL exec loader does this; the navigator must too, since a
            # ``node:`` building hop resolves the menu teleport through here.
            from config.paths import ensure_repo_on_sys_path

            ensure_repo_on_sys_path()
            main_menu_exec = __import__(
                "games.wos.core.main_menu.exec",
                fromlist=[
                    "_BUTTON_X0_PCT",
                    "_BUTTON_X1_PCT",
                    "_scan_panel_rows",
                ],
            )
        except Exception:
            logger.exception("Navigator: main_menu panel scanner import failed")
            return False
        try:
            from tasks import dsl_runtime

            actions = dsl_runtime.bot_actions()
        except Exception:
            logger.exception("Navigator: bot actions unavailable for main_menu panel")
            return False

        for _attempt in range(4):
            ok = await asyncio.to_thread(
                actions.swipe_direction,
                instance_id,
                direction="down",
                delta=500,
                duration_ms=350,
            )
            if not ok:
                return False
            await asyncio.sleep(0.5)

        # Match the DSL exec's scroll-find budget + capture: 16 sweeps and adb
        # lossless frames. scrcpy H.264 degrades the small City-panel row titles
        # below the fuzzy section/row match, so scrcpy capture reports "row not
        # found" even when it is plainly on screen (verified on bs3: "Center
        # Research" missed under scrcpy, hit under adb).
        for sweep in range(16):
            image = await main_menu_exec._capture_panel_frame(  # type: ignore[attr-defined]
                actions, instance_id
            )
            if image is None:
                logger.warning(
                    "Navigator: main_menu panel capture failed instance=%s", instance_id
                )
                return False
            _h, w = image.shape[:2]
            scan_rows = await main_menu_exec._scan_panel_rows(  # type: ignore[attr-defined]
                image,
                ocr=self._ocr,
                with_status=False,
            )
            target = next(
                (
                    r
                    for r in scan_rows
                    if str(r.get("section") or "").strip().lower() == section
                    and str(r.get("row") or "").strip().lower() in rows
                    and r.get("button")
                ),
                None,
            )
            if target is not None:
                bx = int(
                    (
                        float(main_menu_exec._BUTTON_X0_PCT)  # type: ignore[attr-defined]
                        + float(main_menu_exec._BUTTON_X1_PCT)  # type: ignore[attr-defined]
                    )
                    / 2
                    / 100
                    * w
                )
                point = Point(bx, int(target["cy"]))
                approval_region = str(
                    spec.get("approval_region")
                    or f"main_menu.panel.{section}.{target['row']}"
                )
                approval_context: dict[str, Any] = {
                    "section": section,
                    "row": str(target["row"]),
                    "sweep": str(sweep),
                }
                if from_screen:
                    approval_context["from_screen"] = from_screen
                if to_screen:
                    approval_context["to_screen"] = to_screen
                if path_csv:
                    approval_context["path"] = path_csv
                if hop_index is not None:
                    approval_context["hop_index"] = str(hop_index)
                tap_kwargs: dict[str, Any] = {"approval_region": approval_region}
                if self._tap_supports_approval_source():
                    tap_kwargs["approval_source"] = "navigation"
                    tap_kwargs["approval_context"] = approval_context
                return bool(
                    await asyncio.to_thread(
                        self._tap,  # type: ignore[arg-type]
                        instance_id,
                        point,
                        **tap_kwargs,
                    )
                )

            ok = await asyncio.to_thread(
                actions.swipe_direction,
                instance_id,
                direction="up",
                delta=400,
                duration_ms=350,
            )
            if not ok:
                return False
            await asyncio.sleep(0.6)

        logger.info(
            "Navigator: main_menu_panel_row not found section=%s rows=%s",
            section,
            sorted(rows),
        )
        return False

    async def _system_back_async(self, instance_id: str) -> bool:
        if self._system_back is None:
            logger.warning("Navigator: system_back action has no handler configured")
            return False
        return bool(await asyncio.to_thread(self._system_back, instance_id))

    def _tap_supports_approval_source(self) -> bool:
        if self._tap_accepts_approval_source is not None:
            return self._tap_accepts_approval_source
        try:
            sig = inspect.signature(self._tap)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self._tap_accepts_approval_source = False
            return False
        self._tap_accepts_approval_source = (
            "approval_source" in sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        )
        return self._tap_accepts_approval_source

    def _tap_supports_revalidate(self) -> bool:
        if self._tap_accepts_revalidate is not None:
            return self._tap_accepts_revalidate
        try:
            sig = inspect.signature(self._tap)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            self._tap_accepts_revalidate = False
            return False
        self._tap_accepts_revalidate = (
            "revalidate" in sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        )
        return self._tap_accepts_revalidate

