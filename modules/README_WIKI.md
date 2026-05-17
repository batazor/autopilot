# Module-contributed wiki entries

The **DB · Wiki reference** UI (`ui/views/wiki_db.py`) merges `db/<entity>/`
with optional `modules/<id>/wiki/<entity>/` contributions via
`config/wiki_sources.py`. Modules can ship hand-authored YAML for any of the
three merge-aware sections — **buildings**, **heroes**, **items**.

> Gear and FAQ stay core-only for now (no per-tier override use-case yet).

## Layout

Mirror the core `db/` shape under `modules/<id>/wiki/`:

```
modules/<id>/wiki/
  heroes/
    index.yaml           # {heroes: [{id, name, wiki_url?, file?}]}
    <hero_id>.yaml       # same schema as db/heroes/<id>.yaml
    assets/<hero_id>/    # optional: icon.png / .webp / .jpg for tiles
  buildings/
    index.yaml           # {buildings: [...]}
    <id>.yaml
    assets/<id>/
  items/
    index.yaml           # {items: [...]}
    <id>.yaml
    assets/<id>/
```

`file` is optional in `index.yaml` rows — when omitted the loader falls back to
`<id>.yaml`. Module rows with the same `id` as a core entry **override** the
core copy; new `id` values are appended at the end of the section list.

## Provenance in UI

Every merged row carries `_source` (`"core"` or the module id). The UI shows a
`📦 owned by module <id>` badge on tiles and a caption above the entity card so
the data origin is never ambiguous. Module-supplied icons live under
`modules/<id>/wiki/<entity>/assets/<entity_id>/<image>` — if absent the loader
falls back to `db/assets/wiki/<entity>/<entity_id>/`.

## Loading entries from code

```python
from config.wiki_sources import load_merged_entries

for entry in load_merged_entries("items"):
    print(entry.id, entry.source, entry.yaml_path)
```

`config.wiki_sources.find_entry(entity, entity_id)` returns a single merged
`WikiEntry` (or `None`) — handy when navigating from a deep-link like
`?section=items&item=<id>`.
