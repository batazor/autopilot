"""Editor for ``config/balance/*.yaml`` — solver weights, hero priors, F2P profiles.

Three tabs:

* **Hero meta** — per-hero ``role_tags`` / ``mode_weights`` / ``skill_priority`` /
  ``general_shard_policy`` / ``manual_level_cap_*`` overrides on top of defaults.
* **Profiles** — top-level scoring profiles (objective weights per mode,
  wheel policy, general-shard rules). One is marked ``active``.
* **Defaults** — global ``sunkness`` / ``scarcity`` / ``threshold_bonuses`` /
  ``solver`` settings (sliders + numeric inputs).

Saves dump fresh YAML — comments inside the file are not preserved, but each
file's top-of-file header (sourcing notes, edit-via instructions) is
re-injected from constants below so they don't drift away.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from config.heroes import get_hero_registry
from config.paths import balance_config_dir

_BALANCE = balance_config_dir()
_DEFAULTS_PATH = _BALANCE / "defaults.yaml"
_PROFILES_PATH = _BALANCE / "profiles.yaml"
_HERO_META_PATH = _BALANCE / "hero_meta.yaml"

_HEADER_DEFAULTS = """# Solver-wide defaults. Edited from the UI (Config → Balance) and read by
# the (future) scorer. Source priors: deep-research-report.md.
"""

_HEADER_PROFILES = """# Top-level scoring profiles. ``active`` picks the one the scorer mixes
# with hero mode_weights. Edited from UI (Config → Balance → Profiles).
"""

_HEADER_HERO_META = """# Per-hero priors for the upgrade scorer. Heroes not under ``overrides``
# inherit ``defaults``. Edited from UI (Config → Balance → Hero meta).
"""

_MODES = ["expedition", "exploration", "arena", "bear_join"]
_SHARD_POLICIES = [
    "deny_by_default",
    "allow_core_only",
    "allow_threshold_only",
    "allow_if_blocked",
]
_WHEEL_POLICIES = [
    "reserve_for_next_gen",
    "current_gen_if_blocked_progress",
    "no_reserve",
]


# ---------------------------------------------------------------------------
# IO helpers — read once per tab, write with header re-injected.
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        st.error(f"Failed to parse `{path.name}`: {exc}")
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(path: Path, header: str, data: dict[str, Any]) -> None:
    body = yaml.dump(data, allow_unicode=True, sort_keys=False)
    path.write_text(header + "\n" + body, encoding="utf-8")


def _load_hero_ids() -> list[tuple[str, str]]:
    """``[(hero_id, display_name)]`` sorted by display name."""
    out = [(h.id, h.name) for h in get_hero_registry().heroes if h.id]
    out.sort(key=lambda x: x[1].lower())
    return out


# ---------------------------------------------------------------------------
# Hero meta tab
# ---------------------------------------------------------------------------


def _render_hero_meta_tab() -> None:
    meta = _load(_HERO_META_PATH)
    defaults = dict(meta.get("defaults") or {})
    overrides = dict(meta.get("overrides") or {})
    heroes = _load_hero_ids()
    if not heroes:
        st.warning(
            "No heroes in the wiki registry. Check "
            "`modules/core/heroes/wiki/heroes/index.yaml`."
        )
        return

    st.markdown(
        "Pick a hero to override. Unlisted heroes inherit the **Defaults** "
        "block below. Saves write to `config/balance/hero_meta.yaml`."
    )

    col_pick, col_action = st.columns([3, 1], vertical_alignment="bottom")
    with col_pick:
        labels = [f"{name}  ·  `{hid}`" for hid, name in heroes]
        idx = st.selectbox(
            "Hero",
            options=range(len(heroes)),
            format_func=lambda i: labels[i],
            key="balance_hero_pick",
        )
        if idx is None:
            return
        hid, _name = heroes[int(idx)]
    with col_action:
        is_override = hid in overrides
        if is_override:
            st.caption(f"`{hid}` is overridden")
        else:
            st.caption(f"`{hid}` uses defaults")

    cur = dict(overrides.get(hid) or {})
    base = cur or defaults

    role_tags_default = list(base.get("role_tags") or [])
    role_tags_input = st.text_input(
        "role_tags (comma-separated)",
        value=", ".join(role_tags_default),
        key=f"balance_hero_tags_{hid}",
    )
    role_tags = [t.strip() for t in role_tags_input.split(",") if t.strip()]

    mode_weights = dict(base.get("mode_weights") or {})
    st.markdown("**Mode weights** (0–100, higher = more value in that mode)")
    mw_cols = st.columns(len(_MODES))
    new_mw: dict[str, int] = {}
    for c, mode in zip(mw_cols, _MODES, strict=False):
        with c:
            new_mw[mode] = int(
                st.slider(
                    mode,
                    min_value=0,
                    max_value=100,
                    value=int(mode_weights.get(mode, 0)),
                    step=1,
                    key=f"balance_hero_mw_{hid}_{mode}",
                )
            )

    st.markdown("**Skill priority** (per-mode skill slots in order)")
    sp_cols = st.columns(len(_MODES))
    skill_priority = dict(base.get("skill_priority") or {})
    new_sp: dict[str, list[int]] = {}
    for c, mode in zip(sp_cols, _MODES, strict=False):
        with c:
            cur_list = ",".join(str(s) for s in (skill_priority.get(mode) or []))
            entered = st.text_input(
                mode,
                value=cur_list,
                key=f"balance_hero_sp_{hid}_{mode}",
                help="Slot numbers, e.g. `1,2`",
            )
            slots: list[int] = []
            for tok in entered.split(","):
                tok = tok.strip()
                if tok.isdigit():
                    slots.append(int(tok))
            if slots:
                new_sp[mode] = slots

    col_gp, col_pre, col_post = st.columns(3)
    with col_gp:
        gsp_default = str(base.get("general_shard_policy") or _SHARD_POLICIES[0])
        if gsp_default not in _SHARD_POLICIES:
            _SHARD_POLICIES.append(gsp_default)
        gsp = st.selectbox(
            "general_shard_policy",
            options=_SHARD_POLICIES,
            index=_SHARD_POLICIES.index(gsp_default),
            key=f"balance_hero_gsp_{hid}",
        )
    with col_pre:
        cap_pre = int(
            st.number_input(
                "manual_level_cap_pre_drill",
                min_value=0,
                max_value=200,
                value=int(base.get("manual_level_cap_pre_drill", 30)),
                key=f"balance_hero_pre_{hid}",
            )
        )
    with col_post:
        cap_post = int(
            st.number_input(
                "manual_level_cap_post_drill",
                min_value=0,
                max_value=200,
                value=int(base.get("manual_level_cap_post_drill", 60)),
                key=f"balance_hero_post_{hid}",
            )
        )

    new_entry: dict[str, Any] = {
        "role_tags": role_tags,
        "mode_weights": new_mw,
    }
    if new_sp:
        new_entry["skill_priority"] = new_sp
    new_entry["general_shard_policy"] = gsp
    new_entry["manual_level_cap_pre_drill"] = cap_pre
    new_entry["manual_level_cap_post_drill"] = cap_post

    # Preserve fields we don't expose in the form (e.g. replacement_risk_curve).
    for k, v in (overrides.get(hid) or {}).items():
        if k not in new_entry:
            new_entry[k] = v

    btn_save, btn_reset = st.columns([1, 1])
    with btn_save:
        if st.button("💾 Save override", use_container_width=True, key=f"balance_hero_save_{hid}"):
            overrides[hid] = new_entry
            meta["defaults"] = defaults
            meta["overrides"] = overrides
            _save(_HERO_META_PATH, _HEADER_HERO_META, meta)
            st.success(f"Saved override for `{hid}`.")
    with btn_reset:
        if is_override and st.button(
            "🗑 Reset to defaults", use_container_width=True, key=f"balance_hero_reset_{hid}"
        ):
            overrides.pop(hid, None)
            meta["overrides"] = overrides
            _save(_HERO_META_PATH, _HEADER_HERO_META, meta)
            st.success(f"`{hid}` will inherit defaults on next read.")

    with st.expander("Defaults (apply to unlisted heroes)", expanded=False):
        st.json(defaults)

    with st.expander("All overrides (raw)", expanded=False):
        st.json(overrides)


# ---------------------------------------------------------------------------
# Profiles tab
# ---------------------------------------------------------------------------


def _render_profiles_tab() -> None:
    doc = _load(_PROFILES_PATH)
    profiles = dict(doc.get("profiles") or {})
    active = str(doc.get("active") or "")
    if not profiles:
        st.warning("No profiles found in `config/balance/profiles.yaml`.")
        return

    names = list(profiles.keys())
    chosen_idx = names.index(active) if active in names else 0
    new_active = st.selectbox(
        "Active profile",
        options=names,
        index=chosen_idx,
        key="balance_profile_active",
    )
    if new_active != active and st.button("Set as active", key="balance_profile_set_active"):
        doc["active"] = new_active
        _save(_PROFILES_PATH, _HEADER_PROFILES, doc)
        st.success(f"Active profile → `{new_active}`")

    st.divider()
    for pname, pdata in profiles.items():
        if not isinstance(pdata, dict):
            continue
        with st.expander(f"`{pname}`" + (" — active" if pname == active else ""), expanded=False):
            desc = str(pdata.get("description") or "").strip()
            if desc:
                st.caption(desc)
            ow = dict(pdata.get("objective_weights") or {})
            st.markdown("**objective_weights**")
            cols = st.columns(len(_MODES))
            new_ow: dict[str, int] = {}
            for c, mode in zip(cols, _MODES, strict=False):
                with c:
                    new_ow[mode] = int(
                        st.number_input(
                            mode,
                            min_value=0,
                            max_value=100,
                            value=int(ow.get(mode, 0)),
                            step=1,
                            key=f"balance_profile_ow_{pname}_{mode}",
                        )
                    )

            wheel_default = str(pdata.get("wheel_policy") or _WHEEL_POLICIES[0])
            if wheel_default not in _WHEEL_POLICIES:
                _WHEEL_POLICIES.append(wheel_default)
            wp = st.selectbox(
                "wheel_policy",
                options=_WHEEL_POLICIES,
                index=_WHEEL_POLICIES.index(wheel_default),
                key=f"balance_profile_wp_{pname}",
            )

            st.markdown("**general_shard_policy** (raw YAML — nested allowlists)")
            gsp_yaml = yaml.dump(
                pdata.get("general_shard_policy") or {},
                allow_unicode=True,
                sort_keys=False,
            )
            new_gsp_raw = st.text_area(
                "general_shard_policy",
                value=gsp_yaml,
                height=180,
                key=f"balance_profile_gsp_{pname}",
                label_visibility="collapsed",
            )

            if st.button(
                "💾 Save profile",
                use_container_width=True,
                key=f"balance_profile_save_{pname}",
            ):
                try:
                    gsp_parsed = yaml.safe_load(new_gsp_raw) or {}
                except yaml.YAMLError as exc:
                    st.error(f"general_shard_policy is not valid YAML: {exc}")
                else:
                    profiles[pname] = {
                        "description": desc,
                        "objective_weights": new_ow,
                        "wheel_policy": wp,
                        "general_shard_policy": gsp_parsed,
                    }
                    doc["profiles"] = profiles
                    _save(_PROFILES_PATH, _HEADER_PROFILES, doc)
                    st.success(f"Saved `{pname}`.")


# ---------------------------------------------------------------------------
# Defaults tab
# ---------------------------------------------------------------------------


def _render_defaults_tab() -> None:
    doc = _load(_DEFAULTS_PATH)
    if not doc:
        st.warning("No defaults found in `config/balance/defaults.yaml`.")
        return

    sunkness = dict(doc.get("sunkness") or {})
    scarcity = dict(doc.get("scarcity") or {})
    thresholds = dict(doc.get("threshold_bonuses") or {})
    solver = dict(doc.get("solver") or {})

    st.markdown("### Sunkness (0–1, higher = harder to undo)")
    new_sunk: dict[str, float] = {}
    for k, v in sunkness.items():
        new_sunk[k] = float(
            st.slider(
                k,
                min_value=0.0,
                max_value=1.0,
                value=float(v),
                step=0.05,
                key=f"balance_def_sunk_{k}",
            )
        )

    st.markdown("### Scarcity (relative resource value)")
    new_scarc: dict[str, float] = {}
    for k, v in scarcity.items():
        new_scarc[k] = float(
            st.slider(
                k,
                min_value=0.0,
                max_value=1.0,
                value=float(v),
                step=0.05,
                key=f"balance_def_scarc_{k}",
            )
        )

    st.markdown("### Threshold bonuses (discrete jumps in value)")
    new_thr: dict[str, int] = {}
    for k, v in thresholds.items():
        new_thr[k] = int(
            st.number_input(
                k,
                min_value=0,
                max_value=100000,
                value=int(v),
                step=50,
                key=f"balance_def_thr_{k}",
            )
        )

    st.markdown("### Solver budgets")
    new_solver: dict[str, Any] = {}
    for mode_key in ("online", "batch"):
        sub = solver.get(mode_key) or {}
        st.markdown(f"**{mode_key}**")
        cols = st.columns(2)
        with cols[0]:
            t = st.number_input(
                "max_time_in_seconds",
                min_value=0.05,
                max_value=120.0,
                value=float(sub.get("max_time_in_seconds", 0.3)),
                step=0.05,
                key=f"balance_def_solver_{mode_key}_t",
            )
        with cols[1]:
            w = st.number_input(
                "num_search_workers",
                min_value=1,
                max_value=16,
                value=int(sub.get("num_search_workers", 3)),
                step=1,
                key=f"balance_def_solver_{mode_key}_w",
            )
        new_solver[mode_key] = {
            "max_time_in_seconds": float(t),
            "num_search_workers": int(w),
        }
    new_solver["random_seed"] = int(
        st.number_input(
            "random_seed",
            min_value=0,
            max_value=2_000_000_000,
            value=int(solver.get("random_seed", 42)),
            key="balance_def_solver_seed",
        )
    )

    if st.button("💾 Save defaults", use_container_width=True, key="balance_def_save"):
        _save(
            _DEFAULTS_PATH,
            _HEADER_DEFAULTS,
            {
                "sunkness": new_sunk,
                "scarcity": new_scarc,
                "threshold_bonuses": new_thr,
                "solver": new_solver,
            },
        )
        st.success("Saved defaults.")


# ---------------------------------------------------------------------------
# Page entry
# ---------------------------------------------------------------------------


st.title("Balance · upgrade-optimizer weights")
st.caption(
    "Edits `config/balance/*.yaml`. The (future) scorer reads these files "
    "to rank upgrade actions per hero/mode/profile."
)

tab_hero, tab_profile, tab_defaults = st.tabs(["Hero meta", "Profiles", "Defaults"])
with tab_hero:
    _render_hero_meta_tab()
with tab_profile:
    _render_profiles_tab()
with tab_defaults:
    _render_defaults_tab()
