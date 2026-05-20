---
name: wos-modules
description: >-
  Add or change feature modules and core modules under modules/. Covers
  module.yaml, scenarios/, analyze/analyze.yaml, exec handlers, wiki
  contributions, modules/core/* overlay pages, and module scope (All / Core /
  feature). Use when creating a module, moving overlay rules out of analyze/,
  wiring module scenarios, or debugging why module rules/scenarios do not load.
  Trigger words — module, module.yaml, modules/core, feature module, overlay
  module, wiki module, module scope, iter_module_dirs.
---

# WOS modules — layout & wiring

The repo keeps automation in **modules** that provide scenarios, overlay rules,
exec handlers, optional legacy Streamlit UI, and optional wiki DB rows. Base game automation
lives in `modules/core/*`; feature automation lives in top-level `modules/*`.

## Two module trees

| Location | Role | Examples |
|----------|------|----------|
| `modules/core/<id>/` | Base game: overlay pages, identity/bootstrap | `pop-up`, `main_page`, `building`, `who_i_am` |
| `modules/<id>/` | Feature automation | `mail`, `vip`, `backpack`, `trials`, `gift_codes` (``exploration`` lives under ``modules/core/exploration/``) |

Discovery order (deterministic): **all `modules/core/*` first** (sorted by dir
name), then **top-level `modules/*`** (skipping the `core/` folder itself).
Implementation: `config/module_discovery.py` → `iter_module_dirs()`.

**Do not** put new overlay page YAML under `analyze/analyze_pages/` — that tree
is gone. One screen/page ≈ one core module with `analyze/analyze.yaml`.

Runtime merges overlays via `analysis.overlay_manifest.load_merged_analyze_yaml()`:
every module manifest from `scenarios.registry.iter_module_analyze_manifests()` (no root manifest).

## Standard module layout

```
modules/<id>/                    # or modules/core/<id>/
  module.yaml                    # required manifest
  scenarios/**/*.yaml            # optional — runnable DSL scenarios
  analyze/analyze.yaml           # optional — overlay rules (findIcon, text, isRedDot, pushScenario)
  exec.py                        # optional — DSL exec: handlers (or path in module.yaml)
  wiki/heroes|buildings|items/   # optional — DB Wiki contributions (see modules/README_WIKI.md)
  area.yaml                      # optional — module-local area (rare; most use core area.json)
  references/                    # optional — module-local crops
```

### `module.yaml` keys

| Key | Purpose |
|-----|---------|
| `id` | Stable module id (defaults to directory name) |
| `title` | Human label in UI selectors |
| `description` | Free text |
| `scenarios` | Relative path to scenario dir (usually `scenarios`) |
| `area` | Relative path to area file; feature modules often `../../area.json` (core). Under `modules/core/<id>/` use `../../../area.json` |
| `references` | Relative path to references tree (same depth as `area`) |
| `exec` | Relative path to exec module (default `exec.py`) |
| `wiki: false` | Exclude from **wiki** module picker only — does **not** affect the Labeling UI |

**Overlay-only core page** (no scenarios, no wiki row):

```yaml
id: main_page
title: Main page
wiki: false
```

**Feature module** using core `area.json` + `references/`:

```yaml
id: mail
title: Mail
scenarios: scenarios
area: ../../area.json
references: ../../references
```

Screen identity is maintained by the worker's rolling detector; do not add a
module just to run a one-shot "where am I" probe.

## What loads from modules

| Concern | Loader | Notes |
|---------|--------|-------|
| Scenario YAML paths | `dsl/registry.py` → `scenario_roots()`, `iter_scenario_yaml_files()` | Core scope = `modules/core/*/scenarios` only |
| Overlay rules | `load_merged_analyze_yaml()` | Merged from all `modules/*/analyze/analyze.yaml` |
| DSL `exec:` handlers | `config/module_exec_registry.load_module_exec_handlers()` | Export `DSL_EXEC_HANDLERS` dict |
| Wiki DB tiles | `config/wiki_sources.load_merged_entries()` | See `modules/README_WIKI.md` |
| Wiki module picker | `config/module_registry.list_wiki_modules()` | Skips `wiki: false` modules |
| Labeling / Gallery scope | `config/module_registry.list_labeling_modules()` | All modules, **ignores** `wiki: false` |

Scenario keys resolve from filename without `.yaml`, including `by_cron/`
subdirectories and template `{placeholder}` files under each module tree.

## Module scope (UI filter)

Sidebar **Module scope** (`ui/module_scope.py`): **All** | **Core** | feature ids.

| Scope | Scenarios | Overlay manifests |
|-------|-----------|-------------------|
| `all` | Every module | All module manifests |
| `core` | `modules/core/*/scenarios` | `modules/core/*/analyze` only |
| `mail` (etc.) | That module's `scenarios/` | That module's `analyze/` |

`path_matches_module_scope()` treats `modules/core/...` as **Core** scope.
Storage key for nested core modules: `core/<name>` (`module_storage_key()`).
The module-scoped overlay analyzer (click approvals rehearsal) stores its selected scope in Redis:

```bash
redis-cli SET wos:ui:ia_analyzer:scope:<instance_id> <module-scope>
```

For `modules/core/survivors`, use `survivors` (or `core/survivors`) and let the analyzer match `isWorkers` and push `assign_worker`; do not manually run that scenario when testing the overlay path.

## Labeling UI deep-link

**Primary (Next.js):** http://127.0.0.1:3000/labeling — `uv run play` or `uv run api` + `cd web && npm run dev`.

| Param | Value | Example |
|-------|-------|---------|
| `ref` | Repo-relative path to the reference PNG | `modules/core/shop/references/main_city.png` |
| `version` | Optional version id in the area doc | `v2` |

**Example (core module reference):**

```
http://127.0.0.1:3000/labeling?ref=modules/core/shop/references/main_city.png
```

**Module-scoped layout** (`module=` + per-module `area.yaml`, references tree filtered by module): legacy Streamlit only — `WOS_PLAY_STREAMLIT=1 uv run play`, then:

| Param | Value | Example |
|-------|-------|---------|
| `ref` | Repo-relative PNG path | `modules/core/shop/references/main_city.png` |
| `module` | Module storage key | `core/shop` for `modules/core/shop/`, `vip` for `modules/vip/` |

Storage key rules:
- `modules/core/<id>/` → storage key = `core/<id>` (e.g. `core/shop`, `core/survivors`)
- `modules/<id>/` → storage key = `<id>` (e.g. `vip`, `mail`)

```
http://127.0.0.1:8501/labeling?ref=modules/core/shop/references/main_city.png&module=core/shop
```

With `module=` on Streamlit, the page switches the module selector, loads that module's `area.yaml` (not global `area.json`), and shows only that module's `references/` tree.

`wiki: false` has **no effect** on module lists for labeling — `list_labeling_modules()` (not `list_wiki_modules()`), so all modules appear regardless of `wiki:`.

## Overlay rules in modules

Put rules in `modules/.../analyze/analyze.yaml` under top-level `overlay:` list.
Same schema as old `analyze_pages/*.yaml` — see `.cursor/rules/wos-overlay-actions.mdc`
and skill `dsl-scenarios` for `findIcon` / `text` / `isRedDot` / `pushScenario`.

After editing overlay YAML, startup validation checks region names against merged
`area.json` (`config/startup_validation.py`). Production overlay evaluation uses
`load_merged_analyze_yaml()` automatically (`analysis/overlay.py`).

**Adding a new overlay page module:**

1. `mkdir -p modules/core/<page>/analyze`
2. Add `module.yaml` with `wiki: false` (overlay manifest path is always `analyze/analyze.yaml`)
3. Add `analyze/analyze.yaml` with `overlay:` rules
4. Run tests touching overlay / `uv run pytest tests/test_module_overlay_merge.py`

## DSL `exec:` in modules

`modules/<id>/exec.py` (or custom path via `exec:` in `module.yaml`):

```python
DSL_EXEC_HANDLERS = {
    "my_action": my_async_handler,
}
```

Handlers merge into `tasks.dsl_exec.DSL_EXEC_REGISTRY` at startup. Duplicate
names: later module in discovery order wins (warning logged).

## Checklist — new **feature** module

1. `modules/<id>/module.yaml` with `id`, `title`, `scenarios`, optional `analyze` / `area` / `references` / `exec`
2. Scenario YAMLs under `modules/<id>/scenarios/`
3. If overlay-driven: `modules/<id>/analyze/analyze.yaml` + regions in `area.json` (via annotator)
4. If custom DSL actions: `exec.py` with `DSL_EXEC_HANDLERS`
5. `uv run pytest tests/test_module_overlay_merge.py tests/test_identity_module_scenarios.py` (if scenarios)
6. Confirm module appears in scope selector and scenarios resolve: `scenarios/template_resolver.resolve(repo, "<key>")`

## Checklist — new **core overlay page** module

1. `modules/core/<page>/module.yaml` with `wiki: false`
2. `modules/core/<page>/analyze/analyze.yaml`
3. Prefer grouping by screen (`main_page`, `building`, `pop-up` for global popups)

## Common pitfalls

- **Wrong `area` / `references` depth** — `modules/core/*` needs three `../` to repo root; feature modules need two.
- **`wiki: false` forgotten** on overlay-only modules — pollutes wiki module dropdown.
- **Expecting `analyze/analyze_pages/`** — removed; use `modules/core/<page>/analyze/`.
- **CORE scope missing feature overlays** — by design; use **All** or the feature scope to see mail/vip rules.
- **Tests calling `load_analyze_yaml()` on a single file only** — use `load_merged_analyze_yaml(repo_root=REPO)` or load the specific module manifest.
- **Hand-editing `area.json`** — still forbidden; use labeling UI (see `dsl-scenarios` skill).
- **Labeling shows all regions** — check `?module=` param; use `core/<id>` for core modules, `<id>` for feature modules.

## Related files

| File | Role |
|------|------|
| `config/module_discovery.py` | `iter_module_dirs`, scope matching |
| `config/module_registry.py` | Wiki contexts, scope options, `list_labeling_modules` |
| `dsl/registry.py` | Scenario + analyze manifest iteration |
| `analysis/overlay_manifest.py` | `load_merged_analyze_yaml` |
| `modules/README_WIKI.md` | Wiki DB contributions only |
| `.cursor/rules/wos-overlay-actions.mdc` | Overlay YAML vs `area.json` actions |

## Related skills

- `dsl-scenarios` — scenario YAML authoring & debug
- `redis-debug` — why a module scenario did / didn't run at runtime
