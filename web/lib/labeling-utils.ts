import type { EditorRegion } from "@/lib/bbox";
import type {
  LabelingDocument,
  LabelingReferenceMeta,
} from "@/lib/types";

export function isPendingCapture(refRel: string): boolean {
  if (refRel.includes("_current_state.png")) return false;
  return /\/temporal\//.test(refRel.replace(/\\/g, "/"));
}

/** Minimal ref row when the PNG exists on disk but is not in the references API list yet. */
export function syntheticReferenceMeta(refRel: string): LabelingReferenceMeta {
  const rel = refRel.replace(/\\/g, "/").trim();
  const name = rel.split("/").pop() ?? rel;
  const prefixMatch = rel.match(/^(?:modules\/[^/]+\/references|references)\/(.*)$/);
  const relUnder = prefixMatch?.[1] ?? rel;
  return {
    rel,
    name,
    rel_under: relUnder,
    title: name,
    screen_id: "",
    region_count: 0,
    active_version: null,
    unassigned: true,
  };
}

/** Infer module scope from a repo-relative reference path.
 *
 * Returns the first path segment after ``modules/`` (the module slug);
 * returns ``null`` for unrecognised paths. Root ``references/`` was drained
 * during the modules migration, so there's no longer a "core" scope to
 * infer there.
 */
export function inferScopeFromRef(refRel: string): string | null {
  const rel = refRel.replace(/\\/g, "/").trim();
  const m = rel.match(/^modules\/([^/]+)\/references\//);
  if (m) return m[1];
  return null;
}

export function apiToEditorRegions(raw: Record<string, unknown>[]): EditorRegion[] {
  return raw.map((r, i) => {
    const name = String(r.name || `region_${i + 1}`);
    const bbox = r.bbox as EditorRegion["bbox"];
    const out: EditorRegion = {
      id: name,
      name,
      action: String(r.action || "exist"),
      threshold: Number(r.threshold ?? 0.9),
      bbox,
    };
    if (r.overlay_auxiliary) out.overlay_auxiliary = true;
    if (r.has_red_dot) out.has_red_dot = true;
    if (r.isSearch) out.isSearch = true;
    if (r.type) out.type = String(r.type);
    const hold = Number(r.tap_hold_ms ?? 0);
    if (Number.isFinite(hold) && hold > 0) out.tap_hold_ms = Math.round(hold);
    return out;
  });
}

export function editorToApiRegions(regions: EditorRegion[]): Record<string, unknown>[] {
  return regions.map((r) => {
    const out: Record<string, unknown> = {
      name: r.name,
      action: r.action,
      threshold: r.threshold,
      bbox: r.bbox,
    };
    if (r.overlay_auxiliary) out.overlay_auxiliary = true;
    if (r.has_red_dot) out.has_red_dot = true;
    if (r.isSearch) out.isSearch = true;
    if (r.type && r.action !== "exist") out.type = r.type;
    const hold = Number(r.tap_hold_ms ?? 0);
    if (Number.isFinite(hold) && hold > 0) out.tap_hold_ms = Math.round(hold);
    return out;
  });
}

export type LabelingWorkflowStep = {
  key: string;
  label: string;
  done: boolean;
  detail: string;
};

export function labelingWorkflowSteps(args: {
  refRel: string;
  doc: LabelingDocument | null;
  regionCount: number;
  dirty: boolean;
}): LabelingWorkflowStep[] {
  const { refRel, doc, regionCount, dirty } = args;
  const temporal = isPendingCapture(refRel);
  const hasPng = Boolean(refRel);
  const published = hasPng && !temporal;
  const hasRegions = regionCount > 0;
  const screenId = (doc?.screen_id || "").trim();
  return [
    {
      key: "capture",
      label: "Screenshot",
      done: hasPng,
      detail: temporal ? "temporal (unsaved)" : published ? "on disk" : "",
    },
    {
      key: "publish",
      label: "Basename / publish",
      done: published,
      detail: temporal ? "assign basename to move out of temporal/" : "",
    },
    {
      key: "screen",
      label: "Screen ID",
      done: Boolean(screenId),
      detail: screenId,
    },
    {
      key: "regions",
      label: "Regions",
      done: hasRegions,
      detail: hasRegions ? `${regionCount} region(s)` : "draw or add regions",
    },
    {
      key: "save",
      label: "area.json saved",
      done: published && !dirty,
      detail: published ? "use **Save area.json** below" : "",
    },
  ];
}

export type RefTreeLeaf = { kind: "leaf"; ref: LabelingReferenceMeta };

export type RefTreeNode =
  | { kind: "group"; id: string; label: string; children: RefTreeNode[] }
  | RefTreeLeaf;

export type RefDirTreeNode = {
  files: LabelingReferenceMeta[];
  dirs: Record<string, RefDirTreeNode>;
};

/** Compact label for Reference PNG listbox (matches Streamlit leaf title). */
export function referenceSelectLabel(r: LabelingReferenceMeta): string {
  const parts = [r.name];
  if (r.region_count > 0) parts.push(`${r.region_count} reg`);
  if (r.active_version) parts.push(`v:${r.active_version}`);
  if (r.unassigned) parts[0] = `⚠ ${r.name}`;
  if (isPendingCapture(r.rel)) parts.push("⏳ pending");
  const sid = r.screen_id.trim();
  if (sid && !parts.some((p) => p.includes(sid))) parts.push(sid);
  return parts.join(" · ");
}

export function referenceLeafTitle(r: LabelingReferenceMeta): string {
  const flags = [
    isPendingCapture(r.rel) ? "⏳ pending" : null,
    r.unassigned ? "⚠ unassigned" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const sid = r.screen_id.trim();
  const core = r.title || r.name;
  if (sid && sid !== core) {
    return flags ? `${core} (${sid}) — ${flags}` : `${core} (${sid})`;
  }
  return flags ? `${core} — ${flags}` : core;
}

export function filterReferences(
  refs: LabelingReferenceMeta[],
  query: string,
): LabelingReferenceMeta[] {
  const q = query.trim().toLowerCase();
  if (!q) return refs;
  return refs.filter(
    (r) =>
      r.rel.toLowerCase().includes(q) ||
      r.name.toLowerCase().includes(q) ||
      r.title.toLowerCase().includes(q) ||
      r.screen_id.toLowerCase().includes(q) ||
      r.rel_under.toLowerCase().includes(q),
  );
}

function sortDirTreeNode(node: RefDirTreeNode): void {
  node.files.sort((a, b) => a.name.localeCompare(b.name));
  for (const key of Object.keys(node.dirs).sort((a, b) => a.localeCompare(b))) {
    sortDirTreeNode(node.dirs[key]);
  }
}

export function buildReferenceDirTree(refs: LabelingReferenceMeta[]): RefDirTreeNode {
  const root: RefDirTreeNode = { files: [], dirs: {} };
  for (const r of refs) {
    const parts = r.rel_under.split("/").filter(Boolean);
    if (parts.length <= 1) {
      root.files.push(r);
      continue;
    }
    let node = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const part = parts[i];
      if (!node.dirs[part]) {
        node.dirs[part] = { files: [], dirs: {} };
      }
      node = node.dirs[part];
    }
    node.files.push(r);
  }
  sortDirTreeNode(root);
  return root;
}

function dirTreeToNodes(node: RefDirTreeNode, pathPrefix = ""): RefTreeNode[] {
  const items: RefTreeNode[] = node.files.map((ref) => ({ kind: "leaf" as const, ref }));
  for (const dirname of Object.keys(node.dirs).sort((a, b) => a.localeCompare(b))) {
    const child = node.dirs[dirname];
    const children = dirTreeToNodes(child, pathPrefix ? `${pathPrefix}/${dirname}` : dirname);
    if (!children.length) continue;
    items.push({
      kind: "group",
      id: `dir:${pathPrefix ? `${pathPrefix}/` : ""}${dirname}`,
      label: `${dirname}/`,
      children,
    });
  }
  return items;
}

function buildScreenIdTree(refs: LabelingReferenceMeta[]): RefTreeNode[] {
  const bySid = new Map<string, LabelingReferenceMeta[]>();
  for (const r of refs) {
    const sid =
      r.unassigned || !r.screen_id.trim() ? "(unassigned)" : r.screen_id.trim();
    const list = bySid.get(sid) ?? [];
    list.push(r);
    bySid.set(sid, list);
  }
  return [...bySid.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([sid, children]) => ({
      kind: "group" as const,
      id: `sid:${sid}`,
      label:
        sid === "(unassigned)"
          ? `⚠ ${sid} · ${children.length} ref(s)`
          : `${sid} · ${children.length} ref(s)`,
      children: children
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((ref) => ({ kind: "leaf" as const, ref })),
    }));
}

/** Hierarchical reference picker: screen_id groups or nested folders under references/. */
export function buildReferenceTree(
  refs: LabelingReferenceMeta[],
  groupByScreenId: boolean,
): RefTreeNode[] {
  const temporal: LabelingReferenceMeta[] = [];
  const published: LabelingReferenceMeta[] = [];
  for (const r of refs) {
    if (isPendingCapture(r.rel)) temporal.push(r);
    else published.push(r);
  }
  const body = groupByScreenId
    ? buildScreenIdTree(published)
    : dirTreeToNodes(buildReferenceDirTree(published));
  if (!temporal.length) return body;
  return [
    {
      kind: "group",
      id: "temporal",
      label: `⏳ temporal · ${temporal.length} capture(s)`,
      children: temporal
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((ref) => ({ kind: "leaf" as const, ref })),
    },
    ...body,
  ];
}

/** Group ids on the path to ``refRel`` (for auto-expand). */
export function refTreeGroupIdsForSelection(
  nodes: RefTreeNode[],
  refRel: string,
): string[] {
  const path: string[] = [];
  const walk = (items: RefTreeNode[]): boolean => {
    for (const item of items) {
      if (item.kind === "leaf") {
        if (item.ref.rel === refRel) return true;
        continue;
      }
      path.push(item.id);
      if (walk(item.children)) return true;
      path.pop();
    }
    return false;
  };
  return walk(nodes) ? [...path] : [];
}

/** Drop empty groups after search filter. */
export function pruneRefTree(nodes: RefTreeNode[]): RefTreeNode[] {
  const out: RefTreeNode[] = [];
  for (const node of nodes) {
    if (node.kind === "leaf") {
      out.push(node);
      continue;
    }
    const children = pruneRefTree(node.children);
    if (children.length) {
      out.push({ ...node, children });
    }
  }
  return out;
}

export function defaultRegion(ow = 720, oh = 1280, name = "region"): EditorRegion {
  const w = 15;
  const h = 8;
  return {
    id: name,
    name,
    action: "exist",
    threshold: 0.9,
    bbox: {
      x: 50 - w / 2,
      y: 50 - h / 2,
      width: w,
      height: h,
      rotation: 0,
      original_width: ow,
      original_height: oh,
    },
  };
}

export function suggestBasename(
  doc: LabelingDocument | null,
  instanceId: string,
): string | null {
  const sid = (doc?.screen_id || "").trim();
  if (!sid) return null;
  const slug = sid.replace(/\./g, "_");
  const inst = instanceId.trim();
  let raw = inst ? `${inst}_${slug}` : slug;
  const ver = doc?.active_version?.trim();
  if (ver && ver !== "default") raw = `${raw}_${ver}`;
  return raw.replace(/[^a-zA-Z0-9._-]+/g, "_").replace(/^_|_$/g, "") || null;
}
