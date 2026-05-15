# Mail Module

Owns the in-game **Mail** screen automation: claims gift mails across all five
tabs (wars / alliance / system / reports / starred) and runs the list-level
*Read & Claim All* + *Delete All Read Mail* bulk actions when they surface.

Current module scope:

- `read_mail_gifts` — single scenario fans the same claim-loop over every tab.
  Triggered by the overlay rules in `analyze/analyze.yaml`:

  - `mail_gift.visible` fires whenever a gift icon is in the list (no red-dot
    needed — the active tab already cleared its dot on entry);
  - `mail.tab.<name>.has_red_dot` (×5) pushes the scenario when an inactive
    tab still flags unread mail. Redis dedupes overlapping enqueues.

Components can move in gradually:

- `area.yaml` may define module screens/regions. Relative `ocr` paths resolve from this module root.
- `references/` may hold module screenshots.
- `references/crop/` may hold module crop templates for those screenshots.
- `wiki/{heroes,buildings,items}/` may contribute hand-authored entries that
  merge into the **DB · Wiki reference** UI alongside `db/<entity>/`. See
  [`modules/README_WIKI.md`](../README_WIKI.md) for the schema.

Navigation edges are still global in `navigation/`.
