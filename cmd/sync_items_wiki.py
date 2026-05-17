from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

_BASE = "https://www.whiteoutsurvival.wiki"
_INDEX = f"{_BASE}/items/"


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


def _clean_text(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_item_links(index_html: str) -> dict[str, str]:
    soup = BeautifulSoup(index_html, "html.parser")
    links: dict[str, str] = {}
    for a in soup.find_all("a"):
        if not isinstance(a, Tag):
            continue
        href = str(a.get("href") or "").strip()
        text = (a.get_text(" ", strip=True) or "").strip()
        if not href or not text:
            continue
        if "/items/" not in href:
            continue
        if href.rstrip("/").endswith("/items"):
            continue
        if href.startswith("/"):
            url = f"{_BASE}{href}"
        elif href.startswith("http"):
            url = href
        else:
            url = f"{_BASE}/{href.lstrip('/')}"
        if not re.search(r"/items/[^/]+/?$", url):
            continue
        links[text] = url
    return links


def _ensure_registry_from_index(*, items_dir: Path, index_html: str) -> tuple[Path, dict[str, Any]]:
    items_dir.mkdir(parents=True, exist_ok=True)
    index_path = items_dir / "index.yaml"
    if index_path.exists():
        return index_path, _load_yaml(index_path)

    links = _extract_item_links(index_html)
    items: list[dict[str, str]] = []
    for name, url in sorted(links.items(), key=lambda x: x[0].lower()):
        iid = _slugify_id(name)
        items.append(
            {
                "id": iid,
                "name": name,
                "wiki_url": url,
                "file": f"{iid}.yaml",
            }
        )
        ipath = items_dir / f"{iid}.yaml"
        if not ipath.exists():
            _save_yaml(
                ipath,
                {
                    "id": iid,
                    "name": name,
                    "wiki_url": url,
                    "category": "",
                    "description": "",
                    "sources": [],
                },
            )

    fetched_at = time.strftime("%Y-%m-%d")
    idx: dict[str, Any] = {
        "source": {"name": "whiteoutsurvival.wiki", "url": _INDEX, "fetched_at": fetched_at},
        "items": items,
    }
    _save_yaml(index_path, idx)
    return index_path, idx


def _extract_between_markers(lines: list[str], start: str, end_markers: tuple[str, ...]) -> list[str]:
    start_l = start.strip().lower()
    end_ls = {e.strip().lower() for e in end_markers}

    starts = [i for i, ln in enumerate(lines) if ln.strip().lower() == start_l]
    if not starts:
        return []
    for si in starts:
        out: list[str] = []
        for j in range(si + 1, len(lines)):
            t = lines[j].strip()
            if not t:
                continue
            if t.lower() in end_ls:
                break
            out.append(t)
        if out:
            return out
    return []


def _parse_item_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln for ln in (line.strip() for line in text.splitlines()) if ln]

    # Most pages look like:
    #   ## Name
    #   Description
    #   Sources
    #   Description
    #   <paragraph...>
    #   Sources
    #   <list...> (often missing in text dump)
    #
    # We'll take the longest chunk between the second "Description" and "Sources".
    desc_chunk = _extract_between_markers(lines, "Description", ("Sources", "#### Tips from Greg", "Tips from Greg"))
    # Sometimes there are multiple "Description" markers; take the longest window between markers.
    if desc_chunk and desc_chunk[0].lower() == "sources":
        desc_chunk = desc_chunk[1:]

    # Heuristic: find the paragraph-like lines after the LAST "Description" token.
    desc_candidates: list[list[str]] = []
    for i, ln in enumerate(lines):
        if ln.lower() == "description":
            chunk = _extract_between_markers(
                lines[i:],
                "Description",
                ("Sources", "#### Tips from Greg", "Tips from Greg", "#### Play the Game", "Play the Game"),
            )
            if chunk:
                # Filter marker words that might sneak in.
                chunk2 = [c for c in chunk if c.lower() not in {"description", "sources"}]
                if chunk2:
                    desc_candidates.append(chunk2)
    if desc_candidates:
        desc_chunk = max(desc_candidates, key=lambda c: sum(len(x) for x in c))

    description = "\n\n".join(desc_chunk).strip()
    description = description if len(description) >= 10 else ""

    # Sources are frequently a bullet list; in text dump they may not appear.
    # We attempt a marker-based parse and keep short lines.
    src_chunk = _extract_between_markers(
        lines,
        "Sources",
        (
            "#### Tips from Greg",
            "Tips from Greg",
            "#### Play the Game",
            "Play the Game",
            "#### Our Socials",
            "Our Socials",
        ),
    )
    sources: list[str] = []
    for s in src_chunk:
        t = _clean_text(s)
        if not t or t.lower() in {"sources", "description"}:
            continue
        if len(t) <= 80:
            sources.append(t)
    # Dedup preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for s in sources:
        if s in seen:
            continue
        seen.add(s)
        uniq.append(s)

    return {"description": description, "sources": uniq}


async def _http_get_async(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, headers={"User-Agent": "wos-autopilot/0.1"})
    r.raise_for_status()
    return r.text


async def _sync_one(
    *,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    items_dir: Path,
    item_row: dict[str, object],
) -> tuple[str, bool, str]:
    """Return (id, updated?, error_message)."""
    iid = str(item_row.get("id") or "").strip()
    name = str(item_row.get("name") or "").strip()
    file_rel = str(item_row.get("file") or "").strip() or f"{iid}.yaml"
    url = str(item_row.get("wiki_url") or "").strip()
    if not iid or not name or not url:
        return iid or "?", False, "missing id/name/wiki_url"

    ipath = items_dir / file_rel
    item = _load_yaml(ipath) if ipath.exists() else {}
    item.setdefault("id", iid)
    item.setdefault("name", name)
    item["wiki_url"] = url

    try:
        async with sem:
            html = await _http_get_async(client, url)
        parsed = _parse_item_page(html)
        item.update(parsed)
        _save_yaml(ipath, item)
        return iid, True, ""
    except Exception as exc:
        return iid, False, f"{type(exc).__name__}: {exc}"


def main(argv: list[str]) -> int:
    repo = _repo_root()
    items_dir = repo / "db" / "items"

    index_html = _http_get(_INDEX)
    index_path, index = _ensure_registry_from_index(items_dir=items_dir, index_html=index_html)
    index_items = index.get("items")
    if not isinstance(index_items, list):
        print("db/items/index.yaml missing items list", file=sys.stderr)
        return 2

    rows = [it for it in index_items if isinstance(it, dict)]
    total = len(rows)

    async def _run() -> tuple[int, int]:
        timeout = httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=30.0)
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
        sem = asyncio.Semaphore(12)
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, limits=limits) as client:
            tasks = [
                _sync_one(client=client, sem=sem, items_dir=items_dir, item_row=row) for row in rows
            ]
            ok = 0
            failed = 0
            for i, coro in enumerate(asyncio.as_completed(tasks), start=1):
                iid, did, err = await coro
                if did:
                    ok += 1
                else:
                    failed += 1
                    print(f"skip {iid}: {err}", file=sys.stderr)
                if i % 25 == 0 or i == total:
                    print(f"progress: {i}/{total} (ok={ok} failed={failed})")
            return ok, failed

    ok, failed = asyncio.run(_run())

    if isinstance(index.get("source"), dict):
        index["source"]["url"] = _INDEX
    _save_yaml(index_path, index)

    print(f"updated {ok} items (failed {failed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

