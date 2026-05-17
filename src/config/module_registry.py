"""Wiki / labeling module contexts (core ``area.json`` + optional ``modules/<id>/``)."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

import config.module_discovery as _module_discovery
from config.paths import repo_root as default_repo_root

CORE_MODULE_KEY = "core"
ALL_MODULES_KEY = "all"


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
    default_ref: str | None = None
    is_all: bool = False
    storage_key_override: str | None = None

    @property
    def storage_key(self) -> str:
        if self.is_all:
            return ALL_MODULES_KEY
        if self.module_id is None:
            return CORE_MODULE_KEY
        return self.storage_key_override or self.module_id

    @property
    def query_value(self) -> str:
        return self.storage_key


def normalize_module_scope(key: str | None) -> str:
    """``all`` | ``core`` | ``<module_id>`` (unknown ids fall back to ``all``)."""
    k = (key or ALL_MODULES_KEY).strip().lower()
    if k in ("", ALL_MODULES_KEY):
        return ALL_MODULES_KEY
    if k == CORE_MODULE_KEY:
        return CORE_MODULE_KEY
    return k


def module_scope_label(scope: str) -> str:
    scope = normalize_module_scope(scope)
    if scope == ALL_MODULES_KEY:
        return "All"
    if scope == CORE_MODULE_KEY:
        return "Core"
    return scope


def list_registered_module_ids(repo_root: Path | None = None) -> list[str]:
    """Sorted module ids (``modules/core/*`` and feature modules)."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    out: list[str] = []
    for module_dir in _module_discovery.iter_module_dirs(root):
        meta = _load_module_yaml(module_dir)
        if meta.get("wiki") is False:
            continue
        module_id = str(meta.get("id") or module_dir.name).strip() or module_dir.name
        out.append(module_id)
    return out


def module_scope_options(repo_root: Path | None = None) -> list[tuple[str, str]]:
    """``[(storage_key, label), ...]`` for selectboxes — All, Core, then modules."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    opts: list[tuple[str, str]] = [
        (ALL_MODULES_KEY, "All"),
        (CORE_MODULE_KEY, "Core"),
    ]
    for ctx in list_wiki_modules(root):
        if ctx.module_id is not None:
            opts.append((ctx.storage_key, ctx.title))
    return opts



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


def _module_default_ref(meta: dict[str, Any]) -> str | None:
    raw = str(meta.get("default_ref") or "").replace("\\", "/").strip().lstrip("/")
    if not raw or raw.startswith("..") or "/.." in raw:
        return None
    return raw


def _default_module_references_dir(repo_root: Path, module_dir: Path) -> Path:
    if _module_discovery.is_core_nested_module(module_dir, repo_root):
        return repo_root / "references"
    return module_dir / "references"


def _default_module_area_path(repo_root: Path, module_dir: Path) -> Path:
    if _module_discovery.is_core_nested_module(module_dir, repo_root):
        return repo_root / "area.json"
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

    references_decl = str(meta.get("references") or "").strip()
    references_dir = (
        _resolve_under_module(module_dir, references_decl, "references")
        if references_decl
        else _default_module_references_dir(repo_root, module_dir)
    )
    area_decl = str(meta.get("area") or "").strip()
    area_path = _default_module_area_path(repo_root, module_dir)
    if area_decl:
        resolved_area = _resolve_under_module(module_dir, area_decl, "area.yaml")
        is_external = resolved_area.resolve() != (repo_root / "area.json").resolve()
        if is_external or _module_discovery.is_core_nested_module(module_dir, repo_root):
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
        default_ref=_module_default_ref(meta),
        storage_key_override=_module_discovery.module_storage_key(module_dir, repo_root),
    )


def core_module_context(repo_root: Path | None = None) -> WikiModuleContext:
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    refs = root / "references"
    return WikiModuleContext(
        module_id=None,
        title="Core",
        repo_root=root,
        module_dir=None,
        references_dir=refs,
        references_prefix="references",
        area_path=root / "area.json",
        is_all=False,
    )


def all_modules_context(repo_root: Path | None = None) -> WikiModuleContext:
    """Merged view across core + every ``modules/<id>/`` area/references tree."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    refs = root / "references"
    return WikiModuleContext(
        module_id=None,
        title="All",
        repo_root=root,
        module_dir=None,
        references_dir=refs,
        references_prefix="references",
        area_path=root / "area.json",
        is_all=True,
    )


def list_wiki_modules(repo_root: Path | None = None) -> list[WikiModuleContext]:
    """Core first, then registered modules in discovery order."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    out: list[WikiModuleContext] = [core_module_context(root)]
    for module_dir in _module_discovery.iter_module_dirs(root):
        meta = _load_module_yaml(module_dir)
        if meta.get("wiki") is False:
            continue
        out.append(_module_context(root, module_dir))
    return out


def get_wiki_module(repo_root: Path | None, module_key: str | None) -> WikiModuleContext:
    key = normalize_module_scope(module_key)
    if key == ALL_MODULES_KEY:
        return all_modules_context(repo_root)
    if key == CORE_MODULE_KEY:
        return core_module_context(repo_root)
    for ctx in list_wiki_modules(repo_root):
        if ctx.storage_key == key or ctx.module_id == key:
            return ctx
    return all_modules_context(repo_root)


def path_matches_module_scope(path: Path, repo_root: Path, module_scope: str | None) -> bool:
    """Whether ``path`` (under ``repo_root``) belongs to the active module scope."""
    scope = normalize_module_scope(module_scope)
    root = repo_root.resolve()
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return False
    if scope == ALL_MODULES_KEY:
        return True
    if scope == CORE_MODULE_KEY:
        return rel.startswith(f"modules/{_module_discovery.CORE_MODULES_DIR}/")

    path_resolved = path.resolve()
    for module_dir in _module_discovery.iter_module_dirs(root):
        if scope not in _module_discovery.module_scope_aliases(module_dir, root):
            continue
        module_resolved = module_dir.resolve()
        if path_resolved == module_resolved or module_resolved in path_resolved.parents:
            return True
    return False


def merge_all_area_docs(repo_root: Path | None = None) -> dict[str, Any]:
    """Union of screens from core ``area.json`` and each module area manifest."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    from layout.area_manifest import load_area_doc

    return load_area_doc(root)


def ocr_path_belongs_to_context(ocr: str, ctx: WikiModuleContext) -> bool:
    raw = str(ocr or "").replace("\\", "/").strip()
    if not raw:
        return False
    if ctx.is_all:
        if raw.startswith("references/"):
            return True
        return raw.startswith("modules/") and "/references/" in raw
    prefix = ctx.references_prefix.rstrip("/") + "/"
    if ctx.module_id is None:
        if raw.startswith("modules/"):
            return False
        return raw.startswith("references/")
    return raw.startswith((prefix, f"modules/{ctx.module_id}/"))


def filter_area_doc_for_context(doc: dict[str, Any], ctx: WikiModuleContext) -> dict[str, Any]:
    """Return a shallow copy with only screens belonging to ``ctx``."""
    if ctx.is_all:
        return merge_all_area_docs(ctx.repo_root)
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
