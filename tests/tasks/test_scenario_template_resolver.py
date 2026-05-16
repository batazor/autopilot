"""Coverage for ``scenarios.template_resolver``: literal vs template lookup,
hero-id validation against ``db/heroes/index.yaml``, and ``${...}`` body
substitution."""

from __future__ import annotations

from pathlib import Path

from scenarios import template_resolver as _tmpl

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_literal_match_wins_over_template() -> None:
    """``read_mail_gifts.yaml`` is a literal scenario — must resolve as-is."""
    resolved = _tmpl.resolve(REPO_ROOT, "read_mail_gifts")
    assert resolved is not None
    assert resolved.path.name == "read_mail_gifts.yaml"
    assert resolved.context == {}


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


def test_load_doc_substitutes_placeholders() -> None:
    """``${hero_id}`` / ``${hero_name}`` in the body are rendered before parse."""
    loaded = _tmpl.load_doc(REPO_ROOT, "level_up_bahiti")
    assert loaded is not None
    _path, doc = loaded
    assert doc["name"] == "⬆️ Level up · Bahiti"
    assert doc["node"] == "page.heroes.bahiti"
    assert isinstance(doc["steps"], list) and doc["steps"]


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
    # Literal scenario falls through to its own ``name:`` field.
    assert _tmpl.display_name(REPO_ROOT, "read_mail_gifts").strip() != ""
    # Unknown keys fall back to the key itself (so the UI never shows ``None``).
    assert _tmpl.display_name(REPO_ROOT, "definitely_not_a_scenario") == "definitely_not_a_scenario"
    assert _tmpl.display_name(REPO_ROOT, "") == ""


def test_iter_resolved_keys_expands_templates_per_hero() -> None:
    """``iter_resolved_keys`` yields one entry per hero for each template,
    so UI listings (Debug runner picker) can show all concrete keys."""
    keys = _tmpl.iter_resolved_keys(REPO_ROOT)
    by_key = {rk.key: rk for rk in keys}
    # Sample literal + sample expansions
    assert "read_mail_gifts" in by_key
    assert "level_up_ahmose" in by_key
    assert "level_up_bahiti" in by_key
    assert "skill_up_lumak_bokan" in by_key
    # Template entries carry hero context; literal entries don't.
    assert by_key["level_up_ahmose"].context == {"hero_id": "ahmose", "hero_name": "Ahmose"}
    assert by_key["read_mail_gifts"].context == {}
    # Same template path is shared by multiple keys.
    assert by_key["level_up_ahmose"].path == by_key["level_up_bahiti"].path
