#!/usr/bin/env python3
"""One-shot: migrate clean-text `match: <…>.title` screen-verify rules to OCR.

The game reskins title banners (recolour/rename), which silently staled the
template crops behind `match:` title rules. For titles that OCR reads as clean
text, an `ocr: <region>` + `contains: "<token>"` rule is reskin-proof.

Scope guard (keeps tuned configs intact): only rewrites a rule whose ONLY keys
are `match` (+ optional `threshold`). Any rule that also carries `tab_active`,
colour gates, etc. is left on template — those need the pixels. Crops are kept
(other consumers — analyze.yaml, scenarios, area.yaml — still reference them).

Run once from repo root: `uv run python scripts/migrate_title_ocr.py`
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# region  ->  contains token (substring of the OCR'd title, case-insensitive).
# Tokens chosen to be distinctive vs sibling screens; multi-word where the OCR
# captured it cleanly, single word where the title wraps a newline.
MIGRATIONS: dict[str, str] = {
    "alliance.tech.contribute.title": "Engineering",
    "alliance.war.auto_join.title": "Auto-Join",
    "artisans_trove.title": "Artisan",
    "bank.deposit_dialog.title": "Storage Duration",
    "bank.title": "Bank",
    "bear_hunt.info.title": "Bear Hunt",
    "chapter.daily_missions.title": "Daily Missions",
    "chapter.growth_missions.title": "Growth Missions",
    "chapter.title": "Chapter",
    "chief_order.title": "Chief Order",
    "chief_profile.title": "Chief Profile",
    "deals.sign_in.title": "Sign-in",
    "exploration.defeat.title": "Defeat",
    "page.exploration.victory.title": "Victory",
    "hero.recruitment.title": "Recruitment",
    "mail.title": "Mail",
    "mercenary_prestige.title": "Mercenary",
    "mia_fortune_hut.title": "Fortune Hut",
    "myriad_bazaar.title": "Myriad Bazaar",
    "page.shop.dawn_market.title": "Market",
    "page.shop.mix_match.title": "Mix",
    "pet.skill.title": "Pet Skill",
    "rewards.title.v2": "Rewards",
    "romance_season.title": "Romance Season",
    "rosarion.title": "Rosarion",
    "survivor_status.title": "Survivor Status",
    "tundra_trek.title": "Tundra",
    "vault_of_enigma.title": "Vault",
    "vip.title": "VIP",
}

# Deliberately NOT migrated (left on template) — recorded so the decision is
# visible rather than silent:
#   page.shop.dawn_fund.title   — OCR caught only "Dawn", collides with dawn_market
#   deals.home_and_beyound.title — OCR caught only generic "Home" (lost "& Beyond")
#   alliance.title (alliance.invitation, terminal) — "Alliance" too generic


def rule_indent_keys(lines: list[str], i: int, dash_indent: str) -> tuple[list[int], set[str]]:
    """Return (line indices, key names) of the rule whose `- match:` is at line i."""
    key_indent = dash_indent + "  "  # keys align two past the dash
    idxs = [i]
    keys: set[str] = set()
    j = i + 1
    while j < len(lines):
        ln = lines[j]
        if not ln.strip():  # blank line ends the rule block here
            break
        # A new list item or a dedent ends this rule.
        stripped_indent = ln[: len(ln) - len(ln.lstrip())]
        if ln.lstrip().startswith("- "):
            break
        if len(stripped_indent) < len(key_indent):
            break
        m = re.match(r"\s*([A-Za-z_][\w]*):", ln)
        if m:
            keys.add(m.group(1))
        idxs.append(j)
        j += 1
    return idxs, keys


def migrate_file(path: str) -> list[str]:
    """Rewrite eligible rules in `path`. Returns list of human-readable changes."""
    text = Path(path).read_text()
    lines = text.splitlines(keepends=False)
    changes: list[str] = []
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(\s*)-\s+match:\s*(\S+)\s*$", ln)
        region = m.group(2) if m else None
        if region in MIGRATIONS:
            dash_indent = m.group(1)
            idxs, keys = rule_indent_keys(lines, i, dash_indent)
            extra = keys - {"threshold"}
            if extra:
                changes.append(f"  SKIP {region}: extra keys {sorted(extra)} (kept template)")
                out.append(ln)
                i += 1
                continue
            token = MIGRATIONS[region]
            out.append(f"{dash_indent}- ocr: {region}")
            out.append(f'{dash_indent}  contains: "{token}"')
            changes.append(f"  ok   {region} -> contains \"{token}\"")
            i = idxs[-1] + 1  # skip the whole old rule block (match + threshold)
            continue
        out.append(ln)
        i += 1
    if changes and any(c.strip().startswith("ok") for c in changes):
        newtext = "\n".join(out) + ("\n" if text.endswith("\n") else "")
        Path(path).write_text(newtext)
    return changes


def main() -> int:
    files = sorted(str(p) for p in Path().glob("games/**/routes/screen_verify.yaml"))
    seen: set[str] = set()
    total_ok = 0
    for f in files:
        changes = migrate_file(f)
        if changes:
            print(f"{f}")
            for c in changes:
                print(c)
                if c.strip().startswith("ok"):
                    total_ok += 1
                    seen.add(c.split()[1])
    missing = set(MIGRATIONS) - seen
    print(f"\nConverted {total_ok} rule(s).")
    if missing:
        print(f"NOT FOUND / not converted: {sorted(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
