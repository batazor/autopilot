# WOS Beta Module Overlay

This subtree is merged on top of `games/wos` for the WOS beta Android package
(`com.xyz.gof`).

Keep this tree thin:

- add beta-only modules here;
- add partial module overlays here when beta screens/routes differ;
- add `module.yaml` with `enabled: false` at the same relative module path to
  disable a base `games/wos` module only for beta.

Discovery order for the `wos_beta` catalog is `games/wos` first, then
`games/wos/beta`.
