VIP reference screenshots and crops live here when the VIP module owns them.

## Layout

- **`area.yaml`** — module-owned regions (merged into runtime `area.json` via `load_area_doc`).
- **`page.*.png`** — committed screen references used by labeling and the overlay engine.
- **`crop/`** — template tiles exported from labeling (`<ref_stem>_<region>.png`).
- **`rehearsal/`** — live MCP step dumps (gitignored). Safe to delete locally; re-capture via MCP rehearsal.
- **`rehearsal/fixtures/<scenario>/`** — minimal committed frames for pytest (`01.main_city_before.png`, …).

## Pytest fixtures

`vip.daily` rehearsal fixtures:

| File | Step |
|------|------|
| `01.main_city_before.png` | `main_city` before navigating to VIP |
| `02.vip_page.png` | VIP screen after entry |
| `03.rewards_popup.png` | Rewards popup after daily box (duplicate of `page.rewards_popup.png` for tests) |

Use the **Labeling** UI to add or update production captures:

- http://127.0.0.1:3000/labeling?ref=modules/vip/references/page.vip.png (with `uv run play` or `uv run api` + Next dev server)
- Module-scoped `area.yaml` edits: `WOS_PLAY_STREAMLIT=1 uv run play` → http://127.0.0.1:8501/labeling?module=vip

Do not hand-edit `area.json`; export regions and crops from Labeling.
