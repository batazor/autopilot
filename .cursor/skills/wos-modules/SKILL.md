---
name: wos-modules
description: >-
  Add or change feature modules and core modules under modules/. Covers
  module.yaml, scenarios/, analyze/analyze.yaml, exec handlers, UI pages, wiki
  contributions, modules/core/* overlay pages, and module scope (All / Core /
  feature). Use when creating a module, moving overlay rules out of analyze/,
  wiring module scenarios, or debugging why module rules/scenarios do not load.
  Trigger words — module, module.yaml, modules/core, feature module, overlay
  module, wiki module, module scope, iter_module_dirs.
---

# WOS modules — layout & wiring

The repo splits automation into **core** (`scenarios/`, root `area.json`) plus
**modules** that plug in scenarios, overlay rules, exec handlers, Streamlit UI,
and optional wiki DB rows — without copying everything into the core tree.

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
  scenarios/**/*.yaml            # optional — resolved like core scenarios/
  analyze/analyze.yaml           # optional — overlay rules (findIcon, text, isRedDot, pushScenario)
  exec.py                        # optional — DSL exec: handlers (or path in module.yaml)
  ui/page.py                     # optional — Streamlit page (see module.yaml `ui:`)
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
| `ui` | Streamlit page spec — str, dict, or list (see `config/module_ui_registry.py`) |
| `wiki: false` | Exclude from wiki module picker (overlay-only core pages) |

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
| Scenario YAML paths | `scenarios/registry.py` → `scenario_roots()`, `iter_scenario_yaml_files()` | Core scope = `scenarios/` + `modules/core/*/scenarios` only |
| Overlay rules | `load_merged_analyze_yaml()` | Merged from all `modules/*/analyze/analyze.yaml` |
| DSL `exec:` handlers | `config/module_exec_registry.load_module_exec_handlers()` | Export `DSL_EXEC_HANDLERS` dict |
| Streamlit pages | `config/module_ui_registry.iter_module_ui_page_specs()` | Declared in `module.yaml` `ui:` |
| Wiki DB tiles | `config/wiki_sources.load_merged_entries()` | See `modules/README_WIKI.md` |
| Labeling / Gallery scope | `config/module_registry.list_wiki_modules()` | Skips `wiki: false` modules |

Scenario keys resolve the same as core: filename without `.yaml`, including
`scenarios/by_cron/` and template `{placeholder}` files under the module tree.

## Module scope (UI filter)

Sidebar **Module scope** (`ui/module_scope.py`): **All** | **Core** | feature ids.

| Scope | Scenarios | Overlay manifests |
|-------|-----------|-------------------|
| `all` | Core `scenarios/` + every module | All module manifests |
| `core` | `scenarios/` + `modules/core/*/scenarios` | `modules/core/*/analyze` only |
| `mail` (etc.) | That module's `scenarios/` | That module's `analyze/` |

`path_matches_module_scope()` treats `modules/core/...` as **Core** scope.
Storage key for nested core modules: `core/<name>` (`module_storage_key()`).

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

1. `modules/<id>/module.yaml` with `id`, `title`, `scenarios`, optional `analyze` / `area` / `references` / `exec` / `ui`
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

## Related files

| File | Role |
|------|------|
| `config/module_discovery.py` | `iter_module_dirs`, scope matching |
| `config/module_registry.py` | Wiki contexts, scope options |
| `scenarios/registry.py` | Scenario + analyze manifest iteration |
| `analysis/overlay_manifest.py` | `load_merged_analyze_yaml` |
| `modules/README_WIKI.md` | Wiki DB contributions only |
| `.cursor/rules/wos-overlay-actions.mdc` | Overlay YAML vs `area.json` actions |

## Related skills

- `dsl-scenarios` — scenario YAML authoring & debug
- `redis-debug` — why a module scenario did / didn't run at runtime
