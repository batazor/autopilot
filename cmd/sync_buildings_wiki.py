from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

_BASE = "https://www.whiteoutsurvival.wiki"
_INDEX = f"{_BASE}/buildings/"


@dataclass(frozen=True)
class CostItem:
    item: str
    amount: str


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
    s = s.strip().lower()
    s = s.replace("’", "'")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _extract_building_links(index_html: str) -> dict[str, str]:
    """Return mapping of building name -> absolute url."""
    soup = BeautifulSoup(index_html, "html.parser")
    links: dict[str, str] = {}

    # Heuristic: under the "All Buildings" area there are many <a> links.
    # We accept any /buildings/<slug>/ link and use anchor text as display name.
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = str(a.get("href") or "").strip()
        text = (a.get_text(" ", strip=True) or "").strip()
        if not href or not text:
            continue
        if "/buildings/" not in href:
            continue
        # Skip the index itself and obvious nav entries
        if href.rstrip("/").endswith("/buildings"):
            continue

        # Normalize href -> absolute
        if href.startswith("/"):
            url = f"{_BASE}{href}"
        elif href.startswith("http"):
            url = href
        else:
            url = f"{_BASE}/{href.lstrip('/')}"

        # Keep only canonical building detail pages
        if not re.search(r"/buildings/[^/]+/?$", url):
            continue

        links[text] = url

    return links


def _ensure_registry_from_index(
    *,
    buildings_dir: Path,
    index_html: str,
) -> tuple[Path, dict[str, Any]]:
    """Ensure `db/buildings/index.yaml` exists. If missing, create from wiki index."""
    buildings_dir.mkdir(parents=True, exist_ok=True)
    index_path = buildings_dir / "index.yaml"
    if index_path.exists():
        return index_path, _load_yaml(index_path)

    links = _extract_building_links(index_html)
    buildings: list[dict[str, str]] = []
    for name, url in sorted(links.items(), key=lambda x: x[0].lower()):
        bid = _slugify_id(name)
        buildings.append(
            {
                "id": bid,
                "name": name,
                "category": "unknown",
                "wiki_url": url,
                "file": f"{bid}.yaml",
            }
        )
        # Create a stub file so the registry is immediately usable.
        bpath = buildings_dir / f"{bid}.yaml"
        if not bpath.exists():
            _save_yaml(
                bpath,
                {
                    "id": bid,
                    "name": name,
                    "category": "unknown",
                    "wiki_url": url,
                    "requirements_by_level": {},
                },
            )

    fetched_at = time.strftime("%Y-%m-%d")
    idx: dict[str, Any] = {
        "source": {"name": "whiteoutsurvival.wiki", "url": _INDEX, "fetched_at": fetched_at},
        "buildings": buildings,
    }
    _save_yaml(index_path, idx)
    return index_path, idx


def _parse_amount(raw: str) -> str:
    s = (raw or "").strip()
    s = s.replace("\xa0", " ")
    return re.sub(r"\s+", " ", s)


def _parse_build_cost_cell(td: Tag) -> list[CostItem]:
    """Parse the Build Cost cell which uses icons + text.

    We preserve items as identifiers like "item_icon_103" when a label is not available.
    """
    items: list[CostItem] = []

    current_item: str | None = None

    for node in td.descendants:
        if isinstance(node, Tag) and node.name == "img":
            src = str(node.get("src") or "").strip()
            # Use filename stem as item id (e.g. item_icon_103)
            m = re.search(r"/([^/]+?)\.(png|webp|jpg|jpeg)$", src, flags=re.IGNORECASE)
            current_item = m.group(1) if m else (src or "unknown_item")
            continue

        if isinstance(node, str):
            txt = _parse_amount(node)
            if not txt:
                continue
            # Filter out stray separators
            if txt in {"|", ","}:
                continue

            # Numbers like 2.2k, 130k etc.
            if re.fullmatch(r"[0-9]+(\.[0-9]+)?k?", txt, flags=re.IGNORECASE):
                if current_item is None:
                    # Some rows omit icons (e.g. level 1) and only have time in the markdown conversion.
                    # We keep an explicit unknown item in that case.
                    current_item = "unknown_item"
                items.append(CostItem(item=current_item, amount=txt))
                current_item = None

    # Sometimes the cell is just plain text (no icons). Handle that too.
    if not items:
        txt = _parse_amount(td.get_text(" ", strip=True))
        if txt and re.fullmatch(r"[0-9]+(\.[0-9]+)?k?(\s+[0-9]+(\.[0-9]+)?k?)*", txt, flags=re.IGNORECASE):
            items.extend(CostItem(item="unknown_item", amount=p) for p in txt.split())

    return items


def _find_requirements_table(soup: BeautifulSoup) -> Tag | None:
    # Find a table containing the expected headers.
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        header = table.find("tr")
        if not isinstance(header, Tag):
            continue
        head_txt = " ".join(th.get_text(" ", strip=True) for th in header.find_all(["th", "td"]))
        head_txt = head_txt.lower()
        if "level" in head_txt and "prerequisites" in head_txt and "construction time" in head_txt:
            return table
    return None


def _parse_building_page(html: str) -> dict[int, dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    table = _find_requirements_table(soup)
    if table is None:
        return {}

    rows = table.find_all("tr")
    if not rows:
        return {}

    # Determine column indices from header row
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(" ", strip=True).strip().lower() for c in header_cells]

    def _col(name: str) -> int | None:
        for i, h in enumerate(headers):
            if h == name:
                return i
        return None

    i_level = _col("level")
    i_pre = _col("prerequisites")
    i_cost = _col("build cost")
    i_time = _col("construction time")
    i_power = _col("building power")

    if i_level is None:
        return {}

    out: dict[int, dict[str, object]] = {}
    for tr in rows[1:]:
        if not isinstance(tr, Tag):
            continue
        tds = tr.find_all(["td", "th"])
        if len(tds) <= i_level:
            continue

        level_s = tds[i_level].get_text(" ", strip=True).strip()
        if not level_s.isdigit():
            continue
        level = int(level_s)

        prereq = ""
        if i_pre is not None and len(tds) > i_pre:
            prereq = tds[i_pre].get_text(" ", strip=True).strip()

        build_cost: list[dict[str, str]] = []
        if i_cost is not None and len(tds) > i_cost:
            build_cost = [ci.__dict__ for ci in _parse_build_cost_cell(tds[i_cost])]

        ctime = ""
        if i_time is not None and len(tds) > i_time:
            ctime = tds[i_time].get_text(" ", strip=True).strip()

        power: int | None = None
        if i_power is not None and len(tds) > i_power:
            p = tds[i_power].get_text(" ", strip=True).strip()
            try:
                power = int(p)
            except Exception:
                power = None

        out[level] = {
            "prerequisites": prereq,
            "build_cost": build_cost,
            "construction_time": ctime,
            "building_power": power,
        }

    return out


def main(argv: list[str]) -> int:
    repo = _repo_root()
    index_html = _http_get(_INDEX)
    buildings_dir = repo / "db" / "buildings"
    index_path, index = _ensure_registry_from_index(buildings_dir=buildings_dir, index_html=index_html)

    index_buildings = index.get("buildings")
    if not isinstance(index_buildings, list):
        print("db/buildings/index.yaml missing buildings list", file=sys.stderr)
        return 2

    updated = 0
    for it in index_buildings:
        if not isinstance(it, dict):
            continue
        bid = str(it.get("id") or "").strip()
        name = str(it.get("name") or "").strip()
        file_rel = str(it.get("file") or "").strip() or f"{bid}.yaml"
        if not bid or not name:
            continue

        url = str(it.get("wiki_url") or "").strip()
        if not url:
            # Fallback: build from name
            slug = name.strip().lower().replace("’", "").replace("'", "")
            slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
            url = f"{_BASE}/buildings/{slug}/"
            it["wiki_url"] = url

        bpath = buildings_dir / file_rel
        building = _load_yaml(bpath) if bpath.exists() else {}
        building.setdefault("id", bid)
        building.setdefault("name", name)
        building.setdefault("category", str(it.get("category") or "unknown") or "unknown")
        building["wiki_url"] = url

        try:
            html = _http_get(url)
        except Exception as exc:
            print(f"skip {bid}: fetch failed {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        req = _parse_building_page(html)
        if not req:
            print(f"skip {bid}: no requirements table parsed from {url}", file=sys.stderr)
            continue

        # YAML keys as strings (stable), loader converts to int later.
        building["requirements_by_level"] = {str(k): v for k, v in sorted(req.items())}
        _save_yaml(bpath, building)
        updated += 1

    # Keep index file consistent (in case we filled missing wiki_url).
    if isinstance(index.get("source"), dict):
        index["source"]["url"] = _INDEX
    _save_yaml(index_path, index)

    print(f"updated {updated} buildings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

