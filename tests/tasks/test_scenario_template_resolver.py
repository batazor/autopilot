"""Coverage for ``scenarios.template_resolver``: literal vs template lookup,
hero-id validation against ``db/heroes/index.yaml``, and ``${...}`` body
substitution."""

from __future__ import annotations

from pathlib import Path

from scenarios import template_resolver as _tmpl

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_literal_match_wins_over_template() -> None:
    """``mail.claim.system`` is rendered from the mail tab template."""
    resolved = _tmpl.resolve(REPO_ROOT, "mail.claim.system")
    assert resolved is not None
    assert resolved.path.name == "mail.claim.{tab}.yaml"
    assert resolved.context == {"tab": "system", "tab_name": "System"}


def test_template_resolves_known_hero() -> None:
    """``level_up_ahmose`` matches ``level_up_{hero}.yaml`` — ahmose is a real hero."""
    resolved = _tmpl.resolve(REPO_ROOT, "level_up_ahmose")
    assert resolved is not None
    assert resolved.path.name == "level_up_{hero}.yaml"
    assert resolved.context == {"hero_id": "ahmose", "hero_name": "Ahmose"}


def test_template_rejects_unknown_hero() -> None:
    """A hero id not in ``db/heroes/index.yaml`` is not a valid template fill."""
    assert _tmpl.resolve(REPO_ROOT, "level_up_doesnotexist") is None


def test_bare_hero_template_resolves() -> None:
    """``ahmose`` matches ``{hero}.yaml`` with ahmose's display name."""
    resolved = _tmpl.resolve(REPO_ROOT, "ahmose")
    assert resolved is not None
    assert resolved.path.name == "{hero}.yaml"
    assert resolved.context == {"hero_id": "ahmose", "hero_name": "Ahmose"}


def test_load_doc_substitutes_placeholders(snapshot) -> None:
    """``${hero_id}`` / ``${hero_name}`` in the body are rendered before parse."""
    loaded = _tmpl.load_doc(REPO_ROOT, "level_up_bahiti")
    assert loaded is not None
    _path, doc = loaded
    assert doc == snapshot


def test_render_keeps_unknown_placeholders() -> None:
    """Unknown ``${...}`` is not stripped — kept verbatim for callers like the
    skill-up scaffold whose ``${region}`` is wired by a future param-expansion."""
    text = "x=${hero_id} y=${region}"
    out = _tmpl.render(text, {"hero_id": "ahmose"})
    assert out == "x=ahmose y=${region}"


def test_resolve_returns_none_for_missing_key() -> None:
    assert _tmpl.resolve(REPO_ROOT, "definitely_not_a_scenario_xyz") is None
    assert _tmpl.resolve(REPO_ROOT, "") is None


def test_display_name_renders_template_keys() -> None:
    """``display_name`` substitutes ``${hero_name}`` so UI surfaces show the
    pretty label for runtime keys like ``level_up_ahmose``."""
    assert _tmpl.display_name(REPO_ROOT, "level_up_ahmose") == "⬆️ Level up · Ahmose"
    assert _tmpl.display_name(REPO_ROOT, "skill_up_lumak_bokan") == "📘 Skill up · Lumak Bokan"
    assert _tmpl.display_name(REPO_ROOT, "mail.claim.system") == "Mail System: Claim Rewards"
    # Unknown keys fall back to the key itself (so the UI never shows ``None``).
    assert _tmpl.display_name(REPO_ROOT, "definitely_not_a_scenario") == "definitely_not_a_scenario"
    assert _tmpl.display_name(REPO_ROOT, "") == ""


def test_iter_resolved_keys_expands_templates_per_hero() -> None:
    """``iter_resolved_keys`` yields one entry per hero for each template,
    so UI listings (Debug runner picker) can show all concrete keys."""
    keys = _tmpl.iter_resolved_keys(REPO_ROOT)
    by_key = {rk.key: rk for rk in keys}
    # Sample literal + sample expansions
    assert "mail.claim.system" in by_key
    assert "level_up_ahmose" in by_key
    assert "level_up_bahiti" in by_key
    assert "skill_up_lumak_bokan" in by_key
    # Template entries carry axis context.
    assert by_key["level_up_ahmose"].context == {"hero_id": "ahmose", "hero_name": "Ahmose"}
    assert by_key["mail.claim.system"].context == {"tab": "system", "tab_name": "System"}
    # Same template path is shared by multiple keys.
    assert by_key["level_up_ahmose"].path == by_key["level_up_bahiti"].path


def test_template_resolves_known_mail_tab() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "mail.claim.alliance")
    assert resolved is not None
    assert resolved.path.name == "mail.claim.{tab}.yaml"
    assert resolved.context == {"tab": "alliance", "tab_name": "Alliance"}


def test_template_rejects_unknown_mail_tab() -> None:
    assert _tmpl.resolve(REPO_ROOT, "mail.claim.inbox") is None


def test_template_resolves_known_backpack_tab() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "backpack.tab.speedup")
    assert resolved is not None
    assert resolved.path.name == "backpack.tab.{tab}.yaml"
    assert resolved.context == {"tab": "speedup", "tab_name": "Speedup"}


def test_template_rejects_unknown_backpack_tab() -> None:
    assert _tmpl.resolve(REPO_ROOT, "backpack.tab.inbox") is None


def test_iter_resolved_keys_uses_navigation_nodes_for_tab_templates() -> None:
    keys = _tmpl.iter_resolved_keys(REPO_ROOT)
    by_key = {rk.key: rk for rk in keys}
    assert "backpack.tab.resources" in by_key
    assert "mail.claim.wars" in by_key
    assert "backpack.tab.wars" not in by_key
    assert "mail.claim.resources" not in by_key
    assert _tmpl.resolve(REPO_ROOT, "mail.claim.inbox") is None


def test_template_resolves_known_onboarding_pointer() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "onboarding.click.hand_pointer_small_reverse")
    assert resolved is not None
    assert resolved.path.name == "onboarding.click.{pointer}.yaml"
    assert resolved.context == {
        "pointer": "hand_pointer_small_reverse",
        "pointer_name": "Small reverse hand pointer",
    }


def test_template_rejects_unknown_onboarding_pointer() -> None:
    assert _tmpl.resolve(REPO_ROOT, "onboarding.click.not_a_pointer") is None


def test_load_doc_substitutes_onboarding_pointer(snapshot) -> None:
    loaded = _tmpl.load_doc(REPO_ROOT, "onboarding.click.hand_pointer")
    assert loaded is not None
    _path, doc = loaded
    assert doc == snapshot


def test_template_resolves_known_trials_day() -> None:
    resolved = _tmpl.resolve(REPO_ROOT, "claim_trials.3")
    assert resolved is not None
    assert resolved.path.name == "claim_trials.{day}.yaml"
    assert resolved.context == {"day": "3", "day_name": "Day 3"}


def test_template_rejects_unknown_trials_day() -> None:
    assert _tmpl.resolve(REPO_ROOT, "claim_trials.6") is None


def test_load_doc_substitutes_trials_day() -> None:
    loaded = _tmpl.load_doc(REPO_ROOT, "claim_trials.4")
    assert loaded is not None
    _path, doc = loaded

    assert doc["name"] == "Claim Trials Day 4"
    assert doc["node"] == "event.trials.day.4"
    assert doc["steps"][1]["while_match"] == "trial.day.4"
    assert doc["steps"][1]["steps"][0]["click"] == "trial.day.4"


def test_trials_event_opener_searches_current_icon_position() -> None:
    loaded = _tmpl.load_doc(REPO_ROOT, "event.trials")
    assert loaded is not None
    _path, doc = loaded

    assert doc["node"] == "main_city"
    assert doc["steps"][0]["while_match"] == "main_city.icon_search"
    assert doc["steps"][0]["template"] == "modules/events/trials/references/event.trials.png"
    assert doc["steps"][0]["steps"][0]["click"] == "main_city.icon_search"
