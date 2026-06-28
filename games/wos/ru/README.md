# WOS RU Module Overlay — «Белая мгла»

This subtree is merged on top of `games/wos` for the Russian-segment Whiteout
Survival build, "Белая мгла" (`com.gof.globalru`). Gameplay is identical to the
global build, so the bot reuses every `games/wos` scenario, route, and analyzer
rule unchanged — this overlay only exists for assets that differ because the UI
is in Russian.

Keep this tree thin:

- add Russian-localized reference crops here when a screen's text-based template
  no longer matches (the icon/red-dot regions are language-independent and stay
  in `games/wos`);
- add partial module overlays here when a Russian screen/route differs;
- add `module.yaml` with `enabled: false` at the same relative module path to
  disable a base `games/wos` module only for the RU build.

Discovery order for the `wos_ru` catalog is `games/wos` first, then
`games/wos/ru`. An empty overlay (this README only) behaves exactly like the
base `wos` catalog.

## OCR (text reading) — handled automatically

When this build is the active one on a worker, OCR switches to Russian on its
own: `config.ocr.catalog_lang` maps `wos_ru → rus+eng`, and `OcrClient` resolves
the Tesseract language per call from the active module catalog. The `rus`
traineddata is installed in the bot image (`Dockerfile.bot`); locally, install it
once (`brew install tesseract-lang`, or drop `rus.traineddata` into the tessdata
dir). If `rus` is missing, OCR logs a warning once and falls back to English
instead of erroring. The `title_line` text cleaner is Unicode-aware, so Cyrillic
titles/names survive. No per-scenario change is needed for text reading.

What still differs and needs work here is **template matching**, not OCR:
icon/red-dot regions are language-independent, but any reference crop of Russian
*text* (a button label, a screen title used as a `findIcon` landmark) won't match
the English crop in `games/wos`. Re-crop those under this overlay as they surface.

Note: the RU build lives on Century's Russian shard, so it is treated as a
non-canonical alias for the global gift-code / identity APIs (Century-blind),
the same as the beta build. Identity comes from in-game OCR, not the Century
player API.
