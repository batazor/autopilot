from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

_UA = {"User-Agent": "wos-autopilot/0.1"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.U)
    return name.strip("_") or "asset"


def _download(client: httpx.Client, url: str, out_path: Path) -> bool:
    try:
        r = client.get(url, headers=_UA, follow_redirects=True, timeout=30)
        r.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return True
    except Exception as exc:
        print(f"skip download {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def _extract_requirements_table_images(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        # same heuristic as the requirements parser: look for key headers
        header = table.find("tr")
        if not isinstance(header, Tag):
            continue
        head_txt = " ".join(th.get_text(" ", strip=True) for th in header.find_all(["th", "td"]))
        head_txt = head_txt.lower()
        if "level" not in head_txt or "construction time" not in head_txt:
            continue
        for img in table.find_all("img"):
            if not isinstance(img, Tag):
                continue
            src = (img.get("src") or "").strip()
            if src.startswith("//"):
                src = "https:" + src
            if src.startswith("/"):
                # best effort: relative to site root is unknown here; skip
                continue
            if src.startswith("http"):
                urls.append(src)
    return urls


def _extract_page_images(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    # Many pages have a main image (post_image). We just collect all http(s) images as a superset.
    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = (img.get("src") or "").strip()
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http"):
            urls.append(src)
    return urls


def main(argv: list[str]) -> int:
    repo = _repo_root()
    assets_dir = repo / "db" / "assets" / "wiki"
    buildings_dir = repo / "db" / "buildings"
    items_dir = repo / "db" / "items"

    mode = (argv[0] if argv else "buildings").strip().lower()
    include_items = mode in {"items", "all"}
    include_buildings = mode in {"buildings", "all"}

    pages: list[tuple[str, str]] = []

    if include_buildings and buildings_dir.is_dir():
        for p in buildings_dir.glob("*.yaml"):
            if p.name == "index.yaml":
                continue
            doc = _load_yaml(p)
            url = str(doc.get("wiki_url") or "").strip()
            bid = str(doc.get("id") or p.stem).strip()
            if url:
                pages.append((f"buildings/{bid}", url))

    if include_items and items_dir.is_dir():
        for p in items_dir.glob("*.yaml"):
            if p.name == "index.yaml":
                continue
            doc = _load_yaml(p)
            url = str(doc.get("wiki_url") or "").strip()
            iid = str(doc.get("id") or p.stem).strip()
            if url:
                pages.append((f"items/{iid}", url))

    if not pages:
        print("no pages found to download from", file=sys.stderr)
        return 2

    downloaded = 0
    seen: set[str] = set()
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        for prefix, url in pages:
            try:
                html = client.get(url, headers=_UA).text
            except Exception as exc:
                print(f"skip page {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue

            # Prefer requirements table icons when present; else download the first few page images.
            img_urls = _extract_requirements_table_images(html)
            if not img_urls:
                img_urls = _extract_page_images(html)[:10]

            for src in img_urls:
                if src in seen:
                    continue
                seen.add(src)
                m = re.search(r"/([^/]+)$", src)
                fname = _safe_name(m.group(1) if m else "image")
                out = assets_dir / prefix / fname
                if out.exists() and out.stat().st_size > 0:
                    continue
                if _download(client, src, out):
                    downloaded += 1

    print(f"downloaded {downloaded} images into {assets_dir.relative_to(repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

