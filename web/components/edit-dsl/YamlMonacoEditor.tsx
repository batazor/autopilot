"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type {
  editor,
  IMarkdownString,
  languages,
  IRange,
} from "monaco-editor";
import type { Monaco, OnMount } from "@monaco-editor/react";
import { editDslRegionPreviewUrl } from "@/lib/api";
import {
  estimateTimeline,
  formatDuration,
  formatTimestamp,
} from "./dsl-timeline";

const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((mod) => mod.default),
  { ssr: false, loading: () => <div className="muted">Loading editor…</div> },
);

export type YamlMarker = {
  message: string;
  line: number;
  column?: number;
  severity?: "error" | "warning";
};

export type RegionMeta = {
  regions: string[];
  region_refs?: Record<string, string>;
};

type Props = {
  value: string;
  onChange: (value: string) => void;
  markers?: YamlMarker[];
  height?: number | string;
  readOnly?: boolean;
  /** Enable scenario-timeline CodeLens + InlayHints (DSL editor only). */
  scenarioTimeline?: boolean;
  /** Region catalog for link / hover / unknown-region markers. */
  regionMeta?: RegionMeta;
};

const MARKER_OWNER = "edit-dsl-yaml";
const UNKNOWN_REGION_OWNER = "edit-dsl-unknown-region";

const REGION_KEYS = ["click", "long_click", "match", "while_match", "ocr"] as const;
const REGION_LINE_RE =
  /^(\s*-?\s*)(click|long_click|match|while_match|ocr)(\s*:\s*)(['"]?)([A-Za-z0-9_.\-/]+)\4/;

type RegionHit = {
  line: number;
  startCol: number;
  endCol: number;
  name: string;
};

function scanRegions(text: string): RegionHit[] {
  const out: RegionHit[] = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const m = REGION_LINE_RE.exec(ln);
    if (!m) continue;
    const prefixLen = m[1].length + m[2].length + m[3].length + m[4].length;
    const startCol = prefixLen + 1;
    const endCol = startCol + m[5].length;
    out.push({ line: i + 1, startCol, endCol, name: m[5] });
  }
  return out;
}

function labelingHref(meta: RegionMeta, name: string): string {
  const params = new URLSearchParams();
  const ref = meta.region_refs?.[name];
  if (ref) params.set("ref", ref);
  params.set("region", name);
  return `/labeling?${params.toString()}`;
}

/**
 * Module-level holder for the active DSL model's region meta.
 * Monaco providers are registered globally per language, so we look up
 * the current meta when they fire. Only one DSL scenario editor is open
 * at a time in the UI.
 */
const activeRegionMeta: { current: RegionMeta | null } = { current: null };

let dslProvidersRegistered = false;

function registerDslProviders(m: Monaco): void {
  if (dslProvidersRegistered) return;
  dslProvidersRegistered = true;

  const codeLensProvider: languages.CodeLensProvider = {
    provideCodeLenses(model) {
      if (!isDslScenarioModel(model)) return { lenses: [], dispose: () => {} };
      const { totalMs } = estimateTimeline(model.getValue());
      return {
        lenses: [
          {
            range: {
              startLineNumber: 1,
              startColumn: 1,
              endLineNumber: 1,
              endColumn: 1,
            },
            id: "dsl-scenario-estimate",
            command: {
              id: "",
              title: `⏱ Estimated runtime: ~${formatDuration(totalMs)}`,
            },
          },
        ],
        dispose: () => {},
      };
    },
    resolveCodeLens(_model, lens) {
      return lens;
    },
  };
  m.languages.registerCodeLensProvider("yaml", codeLensProvider);

  const inlayProvider: languages.InlayHintsProvider = {
    provideInlayHints(model) {
      if (!isDslScenarioModel(model)) return { hints: [], dispose: () => {} };
      const { perLine } = estimateTimeline(model.getValue());
      const hints: languages.InlayHint[] = [];
      for (const [line, ms] of perLine) {
        const column = model.getLineMaxColumn(line);
        hints.push({
          position: { lineNumber: line, column },
          label: `  // ${formatTimestamp(ms)} from start`,
          paddingLeft: true,
          kind: m.languages.InlayHintKind.Type,
        });
      }
      return { hints, dispose: () => {} };
    },
  };
  m.languages.registerInlayHintsProvider("yaml", inlayProvider);

  const linkProvider: languages.LinkProvider = {
    provideLinks(model) {
      if (!isDslScenarioModel(model)) return { links: [] };
      const meta = activeRegionMeta.current;
      if (!meta) return { links: [] };
      const hits = scanRegions(model.getValue());
      const links: languages.ILink[] = hits.map((h) => {
        const range: IRange = {
          startLineNumber: h.line,
          startColumn: h.startCol,
          endLineNumber: h.line,
          endColumn: h.endCol,
        };
        return {
          range,
          url: labelingHref(meta, h.name),
          tooltip: `Open ${h.name} in labeling`,
        };
      });
      return { links };
    },
  };
  m.languages.registerLinkProvider("yaml", linkProvider);

  const hoverProvider: languages.HoverProvider = {
    provideHover(model, position) {
      if (!isDslScenarioModel(model)) return null;
      const meta = activeRegionMeta.current;
      if (!meta) return null;
      const line = model.getLineContent(position.lineNumber);
      const lineMatch = REGION_LINE_RE.exec(line);
      if (!lineMatch) return null;
      const prefixLen =
        lineMatch[1].length +
        lineMatch[2].length +
        lineMatch[3].length +
        lineMatch[4].length;
      const startCol = prefixLen + 1;
      const endCol = startCol + lineMatch[5].length;
      if (position.column < startCol || position.column > endCol) return null;
      const name = lineMatch[5];
      const known = meta.regions.includes(name);
      const previewUrl = editDslRegionPreviewUrl(name);
      const href = labelingHref(meta, name);
      const header = known
        ? `**${name}**`
        : `**${name}** &nbsp;⚠ _unknown region_`;
      const contents: IMarkdownString[] = [
        { value: header, isTrusted: true, supportHtml: true },
        {
          value: `![${name}](${previewUrl})`,
          isTrusted: true,
          supportHtml: true,
        },
        {
          value: `[Open in labeling ↗](${href})`,
          isTrusted: true,
        },
      ];
      return {
        range: {
          startLineNumber: position.lineNumber,
          startColumn: startCol,
          endLineNumber: position.lineNumber,
          endColumn: endCol,
        },
        contents,
      };
    },
  };
  m.languages.registerHoverProvider("yaml", hoverProvider);
}

function isDslScenarioModel(model: editor.ITextModel): boolean {
  const path = model.uri?.path ?? "";
  return path.includes("/dsl-scenario/");
}

export function YamlMonacoEditor({
  value,
  onChange,
  markers = [],
  height = 480,
  readOnly = false,
  scenarioTimeline = false,
  regionMeta,
}: Props) {
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);

  const knownRegions = useMemo(
    () => new Set(regionMeta?.regions ?? []),
    [regionMeta],
  );

  const applyMarkers = useCallback(() => {
    const ed = editorRef.current;
    const m = monacoRef.current;
    if (!ed || !m) return;
    const model = ed.getModel();
    if (!model) return;
    const totalLines = model.getLineCount();
    const data: editor.IMarkerData[] = markers.map((mk) => {
      const line = Math.min(Math.max(mk.line, 1), totalLines);
      const col = Math.max(mk.column ?? 1, 1);
      const lineMax = model.getLineMaxColumn(line);
      return {
        message: mk.message,
        severity:
          mk.severity === "warning"
            ? m.MarkerSeverity.Warning
            : m.MarkerSeverity.Error,
        startLineNumber: line,
        startColumn: col,
        endLineNumber: line,
        endColumn: lineMax,
      };
    });
    m.editor.setModelMarkers(model, MARKER_OWNER, data);
  }, [markers]);

  const applyUnknownRegionMarkers = useCallback(() => {
    const ed = editorRef.current;
    const m = monacoRef.current;
    if (!ed || !m) return;
    const model = ed.getModel();
    if (!model) return;
    if (!regionMeta) {
      m.editor.setModelMarkers(model, UNKNOWN_REGION_OWNER, []);
      return;
    }
    const hits = scanRegions(model.getValue());
    const data: editor.IMarkerData[] = [];
    for (const h of hits) {
      if (knownRegions.has(h.name)) continue;
      data.push({
        message: `Unknown region "${h.name}" — not found in area.yaml / area.json`,
        severity: m.MarkerSeverity.Error,
        startLineNumber: h.line,
        startColumn: h.startCol,
        endLineNumber: h.line,
        endColumn: h.endCol,
      });
    }
    m.editor.setModelMarkers(model, UNKNOWN_REGION_OWNER, data);
  }, [knownRegions, regionMeta]);

  useEffect(() => {
    applyMarkers();
  }, [applyMarkers]);

  useEffect(() => {
    applyUnknownRegionMarkers();
  }, [applyUnknownRegionMarkers, value]);

  // Keep the module-level meta in sync with this component's meta so the
  // globally-registered link/hover providers can resolve the right catalog.
  useEffect(() => {
    if (!regionMeta) return;
    activeRegionMeta.current = regionMeta;
    // Nudge Monaco to refresh links after meta changes.
    const m = monacoRef.current;
    const ed = editorRef.current;
    if (m && ed) {
      // Touching markers triggers a re-render that re-queries link provider.
      applyUnknownRegionMarkers();
    }
    return () => {
      if (activeRegionMeta.current === regionMeta) {
        activeRegionMeta.current = null;
      }
    };
  }, [regionMeta, applyUnknownRegionMarkers]);

  const handleMount: OnMount = (ed, m) => {
    editorRef.current = ed;
    monacoRef.current = m;
    if (scenarioTimeline) registerDslProviders(m);
    if (regionMeta) activeRegionMeta.current = regionMeta;
    applyMarkers();
    applyUnknownRegionMarkers();
  };

  return (
    <MonacoEditor
      height={height}
      defaultLanguage="yaml"
      language="yaml"
      path={scenarioTimeline ? "/dsl-scenario/scenario.yaml" : undefined}
      theme="vs-dark"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={handleMount}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        tabSize: 2,
        insertSpaces: true,
        scrollBeyondLastLine: false,
        wordWrap: "on",
        readOnly,
        automaticLayout: true,
        renderWhitespace: "boundary",
      }}
    />
  );
}

export const DSL_REGION_KEYS = REGION_KEYS;

/**
 * Best-effort line/column extraction from PyYAML and Pydantic errors.
 * Falls back to `{ line: 1, column: 1 }` when nothing is found.
 */
export function parseYamlErrorLocation(error: string): {
  line: number;
  column: number;
} {
  if (!error) return { line: 1, column: 1 };
  // PyYAML: '... line 3, column 5'
  const pyyaml = error.match(/line\s+(\d+),\s*column\s+(\d+)/i);
  if (pyyaml) {
    return { line: parseInt(pyyaml[1], 10), column: parseInt(pyyaml[2], 10) };
  }
  const lineOnly = error.match(/line\s+(\d+)/i);
  if (lineOnly) return { line: parseInt(lineOnly[1], 10), column: 1 };
  return { line: 1, column: 1 };
}
