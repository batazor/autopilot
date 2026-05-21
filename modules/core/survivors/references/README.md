Survivors reference screenshots and crops.

## Layout

- **`page.*.png`**, **`isNewPeople*.png`**, **`survivors.intro.png`** — committed screen references for labeling and overlay.
- **`crop/`** — template tiles from labeling.
- **`rehearsal/`** — live MCP step dumps (gitignored).
- **`rehearsal/fixtures/<scenario>/`** — minimal committed frames for pytest.

## Pytest fixtures

`welcome_new_survivors`:

| File | Step |
|------|------|
| `01.main_city_before.png` | `main_city` with new-survivor badge |
| `02.welcome_in.png` | Welcome-in popup |
| `03.after_welcome_in.png` | `main_city` after dismiss (same image as `page.main_city.after_welcome.png`) |
