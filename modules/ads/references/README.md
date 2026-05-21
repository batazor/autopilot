Ad popup reference screenshots and template crops for the **ads** module only.

Backpack, shop, VIP, and other game features live in their own modules (`modules/backpack/`, etc.) — not here.

## Layout

- **`../area.yaml`** — module-owned regions (merged into runtime `area.json` via `load_area_doc`).
- **`*.png`** — full-screen references for labeling and overlay matching.
- **`crop/`** — template tiles (`<ref_stem>_<region>.png`).

## Labeling

Module-scoped labeling (Streamlit legacy):

```
http://127.0.0.1:8501/labeling?module=ads&ref=modules/ads/references/ads.natalia.png
```

Next.js labeling with `uv run play`:

```
http://127.0.0.1:3000/labeling?ref=modules/ads/references/ads.natalia.png
```

Survivor Status intro tips (`survivors.intro`) live in **survivors** (`survivors.intro.png`).

Do not hand-edit root `area.json`; save regions and crops from the labeling UI.
