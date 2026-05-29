# WOS Bot — Pop-up Detection Package Prompt

> Standalone prompt. The project scaffold, perception layer, and events system
> already exist. Generate only the modules described here — do not regenerate
> existing files. This package replaces ad-hoc pop-up handling with a single
> game-wide, template-free detector.

---

## Role

You are a senior Python architect. Implement a **game-wide pop-up detector** for a
Whiteout Survival bot running on macOS Apple Silicon with BlueStacks Air
(`720×1280 @ 320 DPI`, but the detector MUST be resolution-independent — derive all
sizes from image dimensions, never hardcode pixel coordinates).

Pop-ups in WoS appear over **any** screen (gift packs, event splashes, level-up,
daily login, ads, captcha). Enumerating each type as its own template does not
scale and breaks on every UI update. Instead, detect the two invariants that hold
across essentially all native modals:

1. **Blurred scrim** — the underlying game is Gaussian-blurred while the modal card
   stays sharp. This is the primary, screen-agnostic signal.
2. **Dismiss affordance** — the X close button, located geometrically as the
   top-right of the card, not recognized per-pop-up.

The detector **localizes**; it never decides safety from geometry alone. A safe
dismissal decision always comes from OCR of the card text against allow/deny lists.

---

## Existing Infrastructure (do not regenerate)

- `capture/window.py` — `QuartzCapture.capture(window_id) → np.ndarray` (BGR)
- `ocr/client.py` — `await ocr_regions(image, regions) → list[OCRResult]`
- `ocr/fuzzy.py` — `match(raw, candidates, threshold) → MatchResult | None`
- `layout/types.py` — `Point`, `Region` (Region has `.x, .y, .w, .h`)
- `perception/models.py` — `ScreenName(StrEnum)`, `ScreenState`
- `perception/screen_detector.py` — `ScreenDetector.detect(image) → ScreenState`
  (returns `ScreenName.UNKNOWN` / low confidence when a modal occludes landmarks)
- `events/models.py` — `EventType`, `EventPriority`, `DetectedEvent`,
  `EventHandleResult`, `HandleContext`
- `events/handlers.py` — existing `PopupBlockingHandler`, `SystemDialogHandler`
  (their allow/deny text logic is the reference for safety classification)
- `actions/tap.py` — `BotActions.tap(instance_id, point)`,
  `BotActions.tap_outside(instance_id)`
- `actions/recovery.py` — `RecoveryHandler`
- captcha solving handler (2captcha) already exists — the detector must ROUTE to it,
  never dismiss captcha

---

## Validated Algorithm (reference implementation — match this behavior)

This was validated on a real `Charm Master Pack` screenshot (1006×1796). It
recovered the full modal bbox at `card_frac ≈ 0.58`, `center ≈ (0.50, 0.49)`,
`scrim_sharp ≈ 0.000`, and the inferred top-right region landed exactly on the X.

```python
import cv2, numpy as np

def localize(img: np.ndarray):
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. sharpness energy — fine edges survive only where NOT blurred
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    energy = cv2.boxFilter(lap, ddepth=-1, ksize=(31, 31))
    energy = cv2.normalize(energy, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

    # 2. binarize, despeckle, then FUSE card elements into one blob
    _, mask = cv2.threshold(energy, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    big = max(31, (W // 8) | 1)  # kernel scaled to image; bridges interior gaps
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (big, big)))

    # 3. union bounding boxes of all sizable components = full modal rect
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.01 * W * H]
    if not keep:
        return None
    x1 = min(stats[i, cv2.CC_STAT_LEFT] for i in keep)
    y1 = min(stats[i, cv2.CC_STAT_TOP] for i in keep)
    x2 = max(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] for i in keep)
    y2 = max(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] for i in keep)
    return mask, (x1, y1, x2 - x1, y2 - y1)
```

Decision signals to compute from the bbox + mask (all screen-agnostic):

- `card_frac = w*h / (W*H)` — large blocking modal vs small toast
- `center = ((x+w/2)/W, (y+h/2)/H)` — modals are roughly centered
- `scrim_sharp` — fraction of sharp pixels in a padded ring OUTSIDE the card;
  near 0 ⇒ clean blurred surround ⇒ real modal (this is the key discriminator
  luminance cannot provide)
- X search region = top-right slice of the card bbox
  (`x[0.82w : w], y[0 : 0.12h]` relative to bbox)

---

## Files to Generate

```
popup/
├── __init__.py        — public exports: PopupDetector, PopupState, PopupKind
├── models.py          — PopupKind, PopupState, DetectionSignals dataclasses
├── mask.py            — sharpness-mask localizer (the validated algorithm)
├── classify.py        — OCR-gated safety classification (allow/deny/captcha/reward)
├── detector.py        — PopupDetector: orchestrates mask → signals → classify
├── close_model.py     — STUB: learned close_button detector for ad/webview tail
└── handler.py         — updated PopupBlockingHandler (loop-until-clear, safe taps)
tests/
└── test_popup.py      — offline tests against saved screenshots in tests/fixtures/
```

---

### `popup/models.py`

```python
class PopupKind(StrEnum):
    NONE          = "none"          # no modal detected
    SAFE_DISMISS  = "safe_dismiss"  # close via X / "Got it" / "Later"
    REWARD_CLAIM  = "reward_claim"  # has Claim/Confirm, no X — tap the claim button
    PURCHASE      = "purchase"      # price/Buy/Spend present — NEVER tap CTA, only X
    CAPTCHA       = "captcha"       # route to 2captcha handler, do NOT dismiss
    AD_WEBVIEW    = "ad_webview"    # full-bleed, no blurred scrim — model fallback
    UNKNOWN_MODAL = "unknown_modal" # overlay present but unclassified — escalate-aware

@dataclass(frozen=True)
class DetectionSignals:
    card_frac: float
    center: tuple[float, float]
    scrim_sharp: float
    overlay_present: bool          # final gate: scrim_sharp low AND card_frac in band

@dataclass(frozen=True)
class PopupState:
    kind: PopupKind
    bbox: Region | None            # full modal rect, None if no modal
    close_point: Point | None      # tap target for the X (top-right of bbox)
    primary_point: Point | None    # Claim/Confirm CTA, only set for REWARD_CLAIM
    card_text: str                 # OCR'd card text (lowercased, joined)
    signals: DetectionSignals
```

---

### `popup/mask.py`

`SharpnessMask` class wrapping the validated `localize()` above.

- `localize(image) → tuple[np.ndarray, Region] | None` — mask + bbox, or None
- `compute_signals(mask, bbox, image_shape) → DetectionSignals`
  - `overlay_present = scrim_sharp < SCRIM_MAX and CARD_FRAC_MIN <= card_frac <= CARD_FRAC_MAX`
  - thresholds in a module-level config dataclass, not magic numbers:
    `SCRIM_MAX = 0.01`, `CARD_FRAC_MIN = 0.10`, `CARD_FRAC_MAX = 0.90`
- `close_region(bbox) → Region` — top-right slice (`0.82w→w`, `0→0.12h`)
- `debug_overlay(image, mask, bbox, close_region) → np.ndarray` — for tests/UI,
  draws mask + boxes (mirrors the validation visualization)

All kernel sizes derived from image width; nothing hardcoded to 720×1280.

---

### `popup/classify.py`

`SafetyClassifier` class. Takes the OCR'd card text and the signals, returns a
`PopupKind`. Reuse the allow/deny vocabularies consistent with `SystemDialogHandler`.

```python
DENY_PURCHASE = ["$", "usd", "buy", "purchase", "spend", "gems", "price", "/mo"]
                # also: a currency glyph or a "$N.NN" regex match
SAFE_DISMISS  = ["got it", "close", "later", "skip", "ok", "confirm later"]
REWARD_CUES   = ["claim", "collect", "tap to", "received", "reward", "level up"]
CAPTCHA_CUES  = ["verify", "select all", "tap the", "captcha", "i am not a robot"]
```

Classification order (first match wins):
1. CAPTCHA cues → `PopupKind.CAPTCHA`
2. PURCHASE cues (price regex `\$?\d+[.,]\d{2}` or deny words) → `PopupKind.PURCHASE`
3. REWARD cues AND no close button found → `PopupKind.REWARD_CLAIM`
4. SAFE cues OR a close button found → `PopupKind.SAFE_DISMISS`
5. overlay present but nothing matched → `PopupKind.UNKNOWN_MODAL`

Fuzzy-match via `ocr.fuzzy.match` (threshold ≥ 0.85), not substring equality.

---

### `popup/detector.py`

`PopupDetector` class. Runs **unconditionally, before `ScreenDetector`**, in the
perception pipeline and short-circuits it when a modal is present.

```python
async def detect(self, image: np.ndarray) -> PopupState:
    loc = self._mask.localize(image)
    if loc is None:
        return PopupState(PopupKind.NONE, None, None, None, "", _empty_signals())

    mask, bbox = loc
    signals = self._mask.compute_signals(mask, bbox, image.shape)

    # full-bleed with no blurred scrim → likely ad/webview, hand to model fallback
    if signals.card_frac > 0.95 and signals.scrim_sharp >= SCRIM_MAX:
        return await self._model_fallback(image)

    if not signals.overlay_present:
        return PopupState(PopupKind.NONE, None, None, None, "", signals)

    close_region = self._mask.close_region(bbox)
    close_pt = self._find_close(image, close_region)   # template first, model later
    text = await self._ocr_card(image, bbox)           # OCR ONLY the bbox crop
    kind = self._classifier.classify(text, signals, has_close=close_pt is not None)
    primary_pt = self._find_primary(image, bbox) if kind == PopupKind.REWARD_CLAIM else None

    return PopupState(kind, bbox, close_pt, primary_pt, text, signals)
```

Notes:
- `_find_close` tries a small bank of X templates in `close_region` only; if it
  misses but `overlay_present` is true, fall back to the geometric center of
  `close_region` as the tap point.
- Corroborate with `ScreenDetector`: caller may pass the prior `ScreenState`;
  `overlay_present AND screen == UNKNOWN` is a strong modal vote even when the X
  template misses (ads/webviews). Expose a helper for the pipeline to combine them.
- Never OCR the full frame — always the bbox crop. Faster and kills background
  false positives.

---

### `popup/close_model.py`  (STUB — do not fully implement)

Interface only, with a clear `NotImplementedError` and a docstring describing the
intended training: two generic classes `close_button` and `modal_card`, trained via
autodistill → RF-DETR/YOLO on clustered pop-up screenshots, run on the bbox crop.
This is the fallback for the ad/webview tail where the blurred-scrim heuristic
fails. Provide the class signature so `detector.py` can call it today and swap in a
real model later:

```python
class CloseButtonModel:
    def available(self) -> bool: ...                  # False until weights present
    async def find(self, image: np.ndarray) -> Point | None: ...
```

---

### `popup/handler.py`

Updated `PopupBlockingHandler` (replaces the existing one). Loop-until-clear with
safe escalation:

```python
async def handle(self, event, ctx, actions) -> EventHandleResult:
    for attempt in range(self.MAX_LAYERS):          # pop-ups stack
        image = self._capture(ctx.instance_id)
        state = await self._detector.detect(image)

        if state.kind == PopupKind.NONE:
            return EventHandleResult.HANDLED

        if state.kind == PopupKind.CAPTCHA:
            return await self._captcha_handler.handle(event, ctx, actions)  # never dismiss

        if state.kind == PopupKind.REWARD_CLAIM and state.primary_point:
            await actions.tap(ctx.instance_id, state.primary_point)
        elif state.close_point:
            await actions.tap(ctx.instance_id, state.close_point)           # prefer X
        elif state.signals.overlay_present:
            await actions.tap_outside(ctx.instance_id)                      # see caveat
        else:
            break

        await asyncio.sleep(self.SETTLE_S)           # let animation finish, then re-check

    # still blocked after MAX_LAYERS → recovery
    return EventHandleResult.ESCALATE
```

Safety caveats to enforce in code:
- For `PopupKind.PURCHASE`, the ONLY permitted tap is `close_point` (or the
  geometric X fallback). Never tap a primary CTA. If no X is found, ESCALATE rather
  than guess.
- `tap_outside` is unsafe on map/city screens (a stray tap issues a real action).
  Only allow it when `overlay_present` is confidently true; prefer the X always.
- `MAX_LAYERS = 4`, `SETTLE_S = 0.6` — module config, tunable.

---

## Tests (`tests/test_popup.py`)

- Offline, no emulator. Load saved screenshots from `tests/fixtures/`.
- Include the validated `Charm Master Pack` shot; assert `card_frac` in `[0.4, 0.7]`,
  `scrim_sharp < 0.01`, `overlay_present is True`, `kind == PopupKind.PURCHASE`
  (price present), and `close_point` falls inside the top-right slice of the bbox.
- Add at least one negative fixture (a normal, unblurred city/map screen): assert
  `kind == PopupKind.NONE`.
- If fixtures are missing, skip with a clear message rather than fail hard.

---

## Constraints

- Python 3.13, `ruff`-clean, full type hints, no `TODO`/stub except `close_model.py`.
- cv2 work is synchronous and fast; keep it off the event loop only if it measurably
  blocks (it doesn't here — sub-ms). OCR calls are `async`.
- No hardcoded pixel coordinates anywhere — all geometry relative to image or bbox.
- Pydantic not required for these models; `@dataclass(frozen=True)` is fine and
  matches the existing perception models.
- Public API surface is exactly: `PopupDetector`, `PopupState`, `PopupKind`.
