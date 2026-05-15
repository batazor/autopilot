from __future__ import annotations

import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

_BASE = "https://www.whiteoutsurvival.wiki"
_INDEX = f"{_BASE}/heroes/"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _http_get(url: str) -> str:
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        r = client.get(url, headers={"User-Agent": "wos-autopilot/0.1"})
        r.raise_for_status()
        return r.text


def _slugify_id(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _extract_hero_links(index_html: str) -> dict[str, str]:
    """Return mapping hero name -> absolute url."""
    soup = BeautifulSoup(index_html, "html.parser")
    links: dict[str, str] = {}
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = (a.get("href") or "").strip()
        text = (a.get_text(" ", strip=True) or "").strip()
        if not href or not text:
            continue
        if "/heroes/" not in href:
            continue
        if href.rstrip("/").endswith("/heroes"):
            continue
        # normalize
        if href.startswith("/"):
            url = f"{_BASE}{href}"
        elif href.startswith("http"):
            url = href
        else:
            url = f"{_BASE}/{href.lstrip('/')}"
        if not re.search(r"/heroes/[^/]+/?$", url):
            continue
        links[text] = url
    return links


def _ensure_registry_from_index(*, heroes_dir: Path, index_html: str) -> tuple[Path, dict[str, Any]]:
    heroes_dir.mkdir(parents=True, exist_ok=True)
    index_path = heroes_dir / "index.yaml"
    if index_path.exists():
        return index_path, _load_yaml(index_path)

    links = _extract_hero_links(index_html)
    heroes: list[dict[str, str]] = []
    for name, url in sorted(links.items(), key=lambda x: x[0].lower()):
        hid = _slugify_id(name)
        heroes.append(
            {
                "id": hid,
                "name": name,
                "wiki_url": url,
                "file": f"{hid}.yaml",
            }
        )
        hpath = heroes_dir / f"{hid}.yaml"
        if not hpath.exists():
            _save_yaml(
                hpath,
                {
                    "id": hid,
                    "name": name,
                    "wiki_url": url,
                    "rarity": "",
                    "class": "",
                    "sub_class": "",
                    "stats": {},
                    "story": "",
                    "shards": {},
                    "sources": [],
                    "skills": [],
                },
            )

    fetched_at = time.strftime("%Y-%m-%d")
    idx: dict[str, Any] = {
        "source": {"name": "whiteoutsurvival.wiki", "url": _INDEX, "fetched_at": fetched_at},
        "heroes": heroes,
    }
    _save_yaml(index_path, idx)
    return index_path, idx


def _clean_text(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _text_after_label(lines: list[str], label: str) -> str:
    """Find label and return next non-empty line."""
    label_l = label.strip().lower()
    for i, ln in enumerate(lines):
        if ln.strip().lower() == label_l:
            for j in range(i + 1, min(i + 6, len(lines))):
                v = lines[j].strip()
                if v:
                    return v
    return ""


def _parse_stats_block(lines: list[str]) -> dict[str, Any]:
    """Parse the simple stats section present on hero pages.

    We keep values as strings because some are percentages.
    """
    out: dict[str, Any] = {}
    # Look for 'Stats' marker then parse Exploration/Expedition pairs
    try:
        i_stats = next(i for i, ln in enumerate(lines) if ln.strip().lower() == "stats")
    except StopIteration:
        return {}

    window = lines[i_stats : i_stats + 80]

    def _read_triplet(start_label: str) -> dict[str, str]:
        try:
            i = next(i for i, ln in enumerate(window) if ln.strip().lower() == start_label.lower())
        except StopIteration:
            return {}
        # expect Attack/Def/Health or Attack/Defense
        d: dict[str, str] = {}
        for k in range(i + 1, min(i + 20, len(window))):
            key = window[k].strip()
            if key.lower() in {"exploration", "expedition", "story", "shards", "skills", "sources"}:
                break
            if key.lower() in {"attack", "def", "defense", "health"}:
                val = ""
                if k + 1 < len(window):
                    val = window[k + 1].strip()
                if val:
                    d[key.lower()] = val
        return d

    exploration = _read_triplet("exploration")
    expedition = _read_triplet("expedition")
    if exploration:
        out["exploration"] = exploration
    if expedition:
        out["expedition"] = expedition
    return out


def _extract_between_markers(lines: list[str], start: str, end: str) -> list[str]:
    start_l = start.strip().lower()
    end_l = end.strip().lower()
    starts = [i for i, ln in enumerate(lines) if ln.strip().lower() == start_l]
    if not starts:
        return []
    for si in starts:
        for ei in range(si + 1, len(lines)):
            if lines[ei].strip().lower() == end_l:
                chunk = [ln.strip() for ln in lines[si + 1 : ei] if ln.strip()]
                if chunk:
                    return chunk
                break
    return []


def _parse_story(lines: list[str]) -> str:
    # Many pages use tabs / non-heading markup; use text markers.
    chunk = _extract_between_markers(lines, "Story", "Shards")
    # Fallback: sometimes "Shards" is missing; stop at "Skills".
    if not chunk:
        chunk = _extract_between_markers(lines, "Story", "Skills")
    # Filter out the short tab label group "Story Shards Skills".
    chunk = [c for c in chunk if c.lower() not in {"story", "shards", "skills"}]
    text = "\n\n".join(chunk).strip()
    # Ignore suspiciously tiny extracts.
    return text if len(text) >= 40 else ""


def _parse_sources(lines: list[str]) -> list[str]:
    # Prefer the explicit "Sources" block on hero pages.
    chunk = _extract_between_markers(lines, "Sources", "Skills")
    if not chunk:
        return []
    out: list[str] = []
    for c in chunk:
        t = _clean_text(c)
        if not t:
            continue
        # Filter common junk if the page includes menus in the text stream.
        if t.lower() in {
            "home",
            "basic info",
            "heroes",
            "research",
            "buildings",
            "events",
            "items",
            "gears",
            "alliance",
            "other",
            "english",
            "search for:",
            "terms of service",
            "privacy policy",
            "impressum",
            "legal terms",
            ".",
        }:
            continue
        if "tips from greg" in t.lower():
            continue
        # Keep the short canonical sources.
        if len(t) <= 60:
            out.append(t)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    return uniq


def _parse_shards_table(soup: BeautifulSoup) -> dict[str, Any]:
    # Find the shards table by header names.
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [_clean_text(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        head = " ".join(h.lower() for h in headers if h)
        if "stars" not in head or "tiers" not in head:
            continue

        out_rows: list[dict[str, str]] = []
        for tr in rows[1:]:
            cells = [_clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue
            row: dict[str, str] = {}
            for i, v in enumerate(cells):
                if i < len(headers) and headers[i]:
                    row[headers[i]] = v
            if row:
                out_rows.append(row)
        return {"headers": headers, "rows": out_rows}
    return {}


def _parse_skills(soup: BeautifulSoup) -> list[dict[str, str]]:
    # Skills are usually rendered as headings (h5) regardless of tab/section markup.
    out: list[dict[str, str]] = []

    def _collect(head_tag: str) -> list[dict[str, str]]:
        res: list[dict[str, str]] = []
        for hh in soup.find_all(head_tag):
            if not isinstance(hh, Tag):
                continue
            name = _clean_text(hh.get_text(" ", strip=True))
            if not name or name.lower() in {"story", "shards", "skills", "sources"}:
                continue
            # Find the next paragraph-ish node for description.
            desc = ""
            for nxt in hh.next_elements:
                if nxt is hh:
                    continue
                if isinstance(nxt, Tag) and nxt.name in {"h2", "h3", "h4", "h5"}:
                    break
                if isinstance(nxt, Tag) and nxt.name == "p":
                    desc = _clean_text(nxt.get_text(" ", strip=True))
                    if desc:
                        break
            if desc:
                res.append({"name": name, "description": desc})
        return res

    out = _collect("h5")
    if not out:
        out = _collect("h4")
    return out


def _iter_section_nodes(start: Tag) -> Iterator[Any]:
    """Yield nodes after `start` until the next major heading (h2/h3/h4)."""
    for node in start.next_elements:
        if node is start:
            continue
        if isinstance(node, Tag) and node.name in {"h2", "h3", "h4"}:
            break
        yield node


def _parse_hero_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln for ln in (line.strip() for line in text.splitlines()) if ln]

    rarity = _text_after_label(lines, "Rarity")
    hclass = _text_after_label(lines, "Class")
    sub_class = _text_after_label(lines, "Sub Class")
    stats = _parse_stats_block(lines)

    story = _parse_story(lines)
    shards = _parse_shards_table(soup)
    sources = _parse_sources(lines)
    skills = _parse_skills(soup)

    return {
        "rarity": rarity,
        "class": hclass,
        "sub_class": sub_class,
        "stats": stats,
        "story": story,
        "shards": shards,
        "sources": sources,
        "skills": skills,
    }


def main(argv: list[str]) -> int:
    repo = _repo_root()
    from config.heroes import heroes_wiki_dir

    heroes_dir = heroes_wiki_dir(repo)

    index_html = _http_get(_INDEX)
    index_path, index = _ensure_registry_from_index(heroes_dir=heroes_dir, index_html=index_html)
    index_heroes = index.get("heroes")
    if not isinstance(index_heroes, list):
        print("heroes wiki index missing heroes list", file=sys.stderr)
        return 2

    updated = 0
    for it in index_heroes:
        if not isinstance(it, dict):
            continue
        hid = str(it.get("id") or "").strip()
        name = str(it.get("name") or "").strip()
        file_rel = str(it.get("file") or "").strip() or f"{hid}.yaml"
        url = str(it.get("wiki_url") or "").strip()
        if not hid or not name or not url:
            continue

        hpath = heroes_dir / file_rel
        hero = _load_yaml(hpath) if hpath.exists() else {}
        hero.setdefault("id", hid)
        hero.setdefault("name", name)
        hero["wiki_url"] = url

        try:
            html = _http_get(url)
        except Exception as exc:
            print(f"skip {hid}: fetch failed {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        parsed = _parse_hero_page(html)
        hero.update(parsed)
        _save_yaml(hpath, hero)
        updated += 1

    if isinstance(index.get("source"), dict):
        index["source"]["url"] = _INDEX
    _save_yaml(index_path, index)

    print(f"updated {updated} heroes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

