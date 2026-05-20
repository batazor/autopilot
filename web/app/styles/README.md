# Web UI styles

Entry point: [`../globals.css`](../globals.css) — Tailwind v4 import, `@source` scan paths, and `@import` of partials below.

| File | Contents |
|------|----------|
| `tokens.css` | `@theme` Tailwind colors, `--wos-*` CSS variables (dark + light) |
| `base.css` | `body`, links, `code` |
| `layout.css` | App shell layout, theme toggle, spacing utilities |
| `ui-core.css` | Icons, spinner, empty state, panels, toolbars, buttons, toasts |
| `tables-status.css` | Data tables, status pills, fleet row states |
| `queue.css` | Queue page metrics, cards, table |
| `forms-misc.css` | YAML editor, code blocks |
| `labeling.css` | Konva / labeling workflow |
| `wiki-player.css` | Wiki tiles, player-state |
| `routes-instance.css` | Routes planner, instance history |
| `approvals.css` | Approvals gate, preview, probe |
| `gallery-edit.css` | Gallery, edit-dsl / edit-scenarios |
| `forms-headless.css` | Form grids, scenario tree, app-select |
| `headless.css` | Headless UI listbox, checkbox, dialog |
| `navigation.css` | Sidebar nav, API status |
| `tabs-wiki.css` | Section tabs, wiki FAQ sync |
| `theme-light.css` | Light-theme overrides for dark-tinted semantic surfaces |

Add new component styles to the most specific file; shared tokens go in `tokens.css`.
