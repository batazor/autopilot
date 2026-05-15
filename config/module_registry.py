"""Wiki / labeling module contexts (core ``area.json`` + optional ``modules/<id>/``)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CORE_MODULE_KEY = "core"


@dataclass(frozen=True)
class WikiModuleContext:
    """Paths used by Gallery and Labeling for one editable wiki scope."""

    module_id: str | None
    title: str
    repo_root: Path
    module_dir: Path | None
    references_dir: Path
    references_prefix: str
    area_path: Path

    @property
    def storage_key(self) -> str:
        return CORE_MODULE_KEY if self.module_id is None else self.module_id

    @property
    def query_value(self) -> str:
        return self.storage_key


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_module_yaml(module_dir: Path) -> dict[str, Any]:
    path = module_dir / "module.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _resolve_under_module(module_dir: Path, raw: str, default: str) -> Path:
    value = (raw or default).strip()
    return (module_dir / value).resolve()


def _references_repo_prefix(repo_root: Path, references_dir: Path) -> str:
    ref = references_dir.resolve()
    root = repo_root.resolve()
    return ref.relative_to(root).as_posix()


def _default_module_area_path(module_dir: Path) -> Path:
    for name in ("area.yaml", "area.yml", "area.json"):
        candidate = module_dir / name
        if candidate.is_file():
            return candidate
    return module_dir / "area.yaml"


def _module_context(repo_root: Path, module_dir: Path) -> WikiModuleContext:
    repo_root = repo_root.resolve()
    meta = _load_module_yaml(module_dir)
    module_id = str(meta.get("id") or module_dir.name).strip() or module_dir.name
    title = str(meta.get("title") or module_id).strip() or module_id

    references_dir = _resolve_under_module(module_dir, str(meta.get("references") or ""), "references")
    area_decl = str(meta.get("area") or "").strip()
    area_path = _default_module_area_path(module_dir)
    if area_decl:
        resolved_area = _resolve_under_module(module_dir, area_decl, "area.yaml")
        if resolved_area.resolve() != (repo_root / "area.json").resolve():
            area_path = resolved_area
        elif area_path.is_file():
            pass
        else:
            area_path = module_dir / "area.yaml"

    return WikiModuleContext(
        module_id=module_id,
        title=title,
        repo_root=repo_root,
        module_dir=module_dir,
        references_dir=references_dir,
        references_prefix=_references_repo_prefix(repo_root, references_dir),
        area_path=area_path,
    )


def core_module_context(repo_root: Path | None = None) -> WikiModuleContext:
    root = (repo_root or _repo_root()).resolve()
    refs = root / "references"
    return WikiModuleContext(
        module_id=None,
        title="Core",
        repo_root=root,
        module_dir=None,
        references_dir=refs,
        references_prefix="references",
        area_path=root / "area.json",
    )


def list_wiki_modules(repo_root: Path | None = None) -> list[WikiModuleContext]:
    """Core first, then ``modules/*/module.yaml`` in sorted order."""
    root = (repo_root or _repo_root()).resolve()
    out: list[WikiModuleContext] = [core_module_context(root)]
    modules_dir = root / "modules"
    if not modules_dir.is_dir():
        return out
    for module_dir in sorted(modules_dir.iterdir(), key=lambda p: p.name.lower()):
        if not module_dir.is_dir() or module_dir.name.startswith("."):
            continue
        if not (module_dir / "module.yaml").is_file():
            continue
        out.append(_module_context(root, module_dir))
    return out


def get_wiki_module(repo_root: Path | None, module_key: str | None) -> WikiModuleContext:
    key = (module_key or CORE_MODULE_KEY).strip().lower()
    for ctx in list_wiki_modules(repo_root):
        if ctx.storage_key == key:
            return ctx
    return core_module_context(repo_root)


def ocr_path_belongs_to_context(ocr: str, ctx: WikiModuleContext) -> bool:
    raw = str(ocr or "").replace("\\", "/").strip()
    if not raw:
        return False
    prefix = ctx.references_prefix.rstrip("/") + "/"
    if ctx.module_id is None:
        if raw.startswith("modules/"):
            return False
        return raw.startswith("references/")
    return raw.startswith(prefix) or raw.startswith(f"modules/{ctx.module_id}/")


def filter_area_doc_for_context(doc: dict[str, Any], ctx: WikiModuleContext) -> dict[str, Any]:
    """Return a shallow copy with only screens belonging to ``ctx``."""
    out = copy.deepcopy(doc)
    screens = out.get("screens")
    if not isinstance(screens, list):
        out["screens"] = []
        return out
    kept: list[dict[str, Any]] = []
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        ocr = str(screen.get("ocr") or "")
        if ocr_path_belongs_to_context(ocr, ctx):
            kept.append(screen)
            continue
        versions = screen.get("versions")
        if not isinstance(versions, list):
            continue
        for ver in versions:
            if not isinstance(ver, dict):
                continue
            if ocr_path_belongs_to_context(str(ver.get("ocr") or ""), ctx):
                kept.append(screen)
                break
    out["screens"] = kept
    return out


def collect_reference_rels_from_doc(doc: dict[str, Any], ctx: WikiModuleContext) -> set[str]:
    """Repository-relative paths under ``references_prefix`` for screens in ``doc``."""
    refs: set[str] = set()
    prefix = ctx.references_prefix.rstrip("/")
    screens = doc.get("screens") if isinstance(doc, dict) else None
    if not isinstance(screens, list):
        return refs

    def add_ocr(raw: str) -> None:
        ocr = str(raw or "").replace("\\", "/").strip()
        if not ocr:
            return
        if ocr == prefix or ocr.startswith(f"{prefix}/"):
            rel = ocr[len(prefix) :].lstrip("/")
        else:
            return
        if rel and not rel.startswith(".."):
            refs.add(rel)

    for screen in screens:
        if not isinstance(screen, dict):
            continue
        add_ocr(str(screen.get("ocr") or ""))
        for ver in screen.get("versions") or []:
            if isinstance(ver, dict):
                add_ocr(str(ver.get("ocr") or ""))
    return refs
