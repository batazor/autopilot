import React, { useEffect, useMemo, useState } from "react"
import {
  ColumnDef,
  ExpandedState,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  Row,
  useReactTable,
} from "@tanstack/react-table"

import type { Align, CellType, ColumnSpec, PillPreset, RowSelection } from "./types"

type RowData = Record<string, unknown>

function cellValue(row: RowData, key: string): string {
  const raw = row[key]
  if (raw === null || raw === undefined) return ""
  if (typeof raw === "object") return JSON.stringify(raw)
  return String(raw)
}

function alignClass(align: Align | undefined): string {
  if (align === "right") return "text-right"
  if (align === "center") return "text-center"
  return "text-left"
}

function widthStyle(width: ColumnSpec["width"]): React.CSSProperties | undefined {
  if (width === null || width === undefined) return undefined
  if (typeof width === "number") return { width: `${width}px`, minWidth: `${width}px` }
  return { width: String(width), minWidth: String(width) }
}

function rowIdFor(data: RowData, getRowId: string, fallback: string): string {
  const raw = data[getRowId]
  if (raw !== null && raw !== undefined && String(raw).trim()) return String(raw)
  return fallback
}

function rowIsSelectable(row: RowData): boolean {
  if (row.selectable === false) return false
  if (row.selectable === true) return true
  const id = String(row.id ?? "")
  return Boolean(id) && !id.startsWith("folder:")
}

function pillClasses(text: string, preset: PillPreset | undefined): string {
  const raw = (text || "").trim().toLowerCase()
  const base =
    "inline-flex max-w-full truncate rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset "
  switch (preset) {
    case "scheduled":
      if (raw.includes("overdue"))
        return `${base} bg-amber-50 text-amber-900 ring-amber-200/80 dark:bg-amber-950/50 dark:text-amber-100 dark:ring-amber-800`
      return `${base} bg-sky-50 text-sky-900 ring-sky-200/70 dark:bg-sky-950/40 dark:text-sky-100 dark:ring-sky-800`
    case "coop":
      return raw === "yes"
        ? `${base} bg-emerald-50 text-emerald-900 ring-emerald-200/80 dark:bg-emerald-950/45 dark:text-emerald-50 dark:ring-emerald-800`
        : `${base} bg-slate-100 text-slate-600 ring-slate-200/80 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-600`
    case "reachable":
      return raw === "yes"
        ? `${base} bg-emerald-50 text-emerald-900 ring-emerald-200/80 dark:bg-emerald-950/45 dark:text-emerald-50 dark:ring-emerald-800`
        : `${base} bg-rose-50 text-rose-800 ring-rose-200/80 dark:bg-rose-950/40 dark:text-rose-100 dark:ring-rose-900`
    case "history_status":
      if (raw === "done")
        return `${base} bg-teal-50 text-teal-900 ring-teal-200/80 dark:bg-teal-950/45 dark:text-teal-50 dark:ring-teal-800`
      if (raw === "failed")
        return `${base} bg-rose-50 text-rose-800 ring-rose-200/80 dark:bg-rose-950/40 dark:text-rose-100 dark:ring-rose-900`
      return `${base} bg-slate-100 text-slate-600 ring-slate-200/70 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-600`
    case "fleet_status": {
      switch (raw) {
        case "live":
          return `${base} bg-emerald-950/55 text-emerald-300 ring-emerald-700/70`
        case "paused":
        case "starting":
        case "restarting":
          return `${base} bg-amber-950/45 text-amber-200 ring-amber-800/70`
        case "crashed":
        case "stale":
          return `${base} bg-rose-950/50 text-rose-200 ring-rose-800/70`
        case "offline":
        default:
          return `${base} bg-slate-800/70 text-slate-400 ring-slate-600/70`
      }
    }
    case "rank_indicator": {
      const n = Number.parseInt(raw.replace(/\D/g, ""), 10)
      return n === 1
        ? `${base} bg-violet-50 text-violet-900 ring-violet-200/90 dark:bg-violet-950/50 dark:text-violet-50 dark:ring-violet-800`
        : `${base} bg-slate-50 text-slate-600 ring-transparent dark:bg-transparent dark:text-slate-400`
    }
    default:
      return `${base} bg-slate-100 text-slate-700 ring-slate-200/70 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-600`
  }
}

function renderCell(spec: ColumnSpec, row: Row<RowData>): React.ReactNode {
  const type: CellType = spec.cell_type ?? "text"
  const align = alignClass(spec.align)
  const title = cellValue(row.original, spec.accessor_key)

  if (type === "link") {
    const href = cellValue(row.original, spec.accessor_key)
    if (!href) return <span className="text-ink-faint dark:text-slate-500">—</span>
    const labelKey = spec.link_text_key ?? "link_text"
    const label = cellValue(row.original, labelKey) || "Open"
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className={`text-sm font-medium text-accent underline-offset-2 hover:underline dark:text-sky-400 ${align}`}
        onClick={(e) => e.stopPropagation()}
      >
        {label}
      </a>
    )
  }

  if (type === "pill") {
    const text = cellValue(row.original, spec.accessor_key)
    if (!text) return <span className="text-ink-faint dark:text-slate-500">—</span>
    const preset = spec.pill_preset
    const showStar = preset === "rank_indicator" && String(text).trim() === "1"
    const label = showStar ? "★ " + text : text
    return (
      <span className={`${pillClasses(text, preset)} ${align}`} title={text}>
        {label}
      </span>
    )
  }

  if (type === "bool") {
    const raw = row.original[spec.accessor_key]
    const on = raw === true || raw === "true" || raw === 1 || raw === "1"
    if (raw === "" || raw === null || raw === undefined) {
      return <span className="text-ink-faint dark:text-slate-500">—</span>
    }
    return (
      <span
        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
          on
            ? "bg-emerald-50 text-emerald-900 ring-1 ring-inset ring-emerald-200/80 dark:bg-emerald-950/45 dark:text-emerald-50 dark:ring-emerald-800"
            : "bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200/80 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-600"
        } ${align}`}
      >
        {on ? "On" : "Off"}
      </span>
    )
  }

  return (
    <span
      className={`block truncate text-sm text-ink dark:text-slate-100 ${align} ${
        row.depth > 0 ? "text-ink-muted dark:text-slate-400" : "font-medium"
      }`}
      title={title || "—"}
    >
      {title || "—"}
    </span>
  )
}

function buildColumns(
  specs: ColumnSpec[],
  subRowsKey: string,
  multiSelect: boolean,
  hideExpand: boolean,
): ColumnDef<RowData, unknown>[] {
  const cols: ColumnDef<RowData, unknown>[] = []

  const expandCol: ColumnDef<RowData, unknown> = {
    id: "__expand",
    header: () => null,
    size: 40,
    cell: ({ row }) => {
      if (!row.getCanExpand()) {
        return <span className="inline-block w-5" aria-hidden />
      }
      const open = row.getIsExpanded()
      return (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            row.getToggleExpandedHandler()()
          }}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-muted transition hover:bg-slate-100 hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          aria-expanded={open}
          aria-label={open ? "Collapse row" : "Expand row"}
        >
          <svg
            viewBox="0 0 20 20"
            fill="currentColor"
            className={`h-4 w-4 transition-transform ${open ? "rotate-90" : ""}`}
          >
            <path
              fillRule="evenodd"
              d="M7.21 14.77a.75.75 0 0 1 .02-1.06L10.94 10 7.23 6.29a.75.75 0 1 1 1.06-1.06l4.24 4.25a.75.75 0 0 1 0 1.06l-4.24 4.25a.75.75 0 0 1-1.06-.02Z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )
    },
  }
  if (!hideExpand) {
    cols.push(expandCol)
  }

  if (multiSelect) {
    cols.push({
      id: "__select",
      header: () => (
        <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted dark:text-slate-400">Sel</span>
      ),
      size: 44,
      cell: ({ row, table }) => {
        const meta = table.options.meta as { selectedIds?: Set<string> } | undefined
        const selected = meta?.selectedIds?.has(row.id) ?? false
        if (!rowIsSelectable(row.original)) {
          return <span className="inline-block w-4" aria-hidden />
        }
        return (
          <input
            type="checkbox"
            checked={selected}
            className="h-4 w-4 rounded border-slate-300 text-accent focus:ring-accent"
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => {
              e.stopPropagation()
              const toggle = (table.options.meta as { toggleSelect?: (id: string) => void }).toggleSelect
              toggle?.(row.id)
            }}
            aria-label={selected ? "Deselect row" : "Select row"}
          />
        )
      },
    })
  }

  const dataCols: ColumnDef<RowData, unknown>[] = specs.map((spec) => ({
    id: spec.id,
    header: () => (
      <span
        className={`block text-xs font-semibold uppercase tracking-wide text-ink-muted dark:text-slate-400 ${alignClass(spec.align)}`}
      >
        {spec.header}
      </span>
    ),
    accessorFn: (row) => row[spec.accessor_key],
    cell: ({ row }) => renderCell(spec, row),
    meta: { spec },
  }))

  void subRowsKey
  cols.push(...dataCols)
  return cols
}

export interface NestedTableProps {
  rows: RowData[]
  columns: ColumnSpec[]
  subRowsKey: string
  height: number
  defaultExpanded: boolean
  striped: boolean
  compact: boolean
  selectable: boolean
  multiSelect: boolean
  selectedIds: string[]
  getRowId: string
  hideExpand: boolean
  onSelect?: (selection: RowSelection) => void
  onSelectedIdsChange?: (ids: string[]) => void
}

export default function NestedTable({
  rows,
  columns,
  subRowsKey,
  height,
  defaultExpanded,
  striped,
  compact,
  selectable,
  multiSelect,
  selectedIds,
  getRowId,
  hideExpand,
  onSelect,
  onSelectedIdsChange,
}: NestedTableProps): React.ReactElement {
  const [expanded, setExpanded] = useState<ExpandedState>(defaultExpanded ? true : {})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedSet, setSelectedSet] = useState<Set<string>>(() => new Set(selectedIds))

  useEffect(() => {
    setSelectedSet(new Set(selectedIds))
  }, [selectedIds])

  const toggleSelect = (id: string) => {
    setSelectedSet((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      onSelectedIdsChange?.(Array.from(next))
      return next
    })
  }

  const columnDefs = useMemo(
    () => buildColumns(columns, subRowsKey, multiSelect, hideExpand),
    [columns, subRowsKey, multiSelect, hideExpand],
  )

  const table = useReactTable({
    data: rows,
    columns: columnDefs,
    state: { expanded },
    onExpandedChange: setExpanded,
    getSubRows: (row) => {
      const children = row[subRowsKey]
      return Array.isArray(children) ? (children as RowData[]) : undefined
    },
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowId: (row, index, parent) => {
      if (parent) {
        return rowIdFor(row, getRowId, `${parent.id}.${index}`)
      }
      return rowIdFor(row, getRowId, `row-${index}`)
    },
    meta: {
      selectedIds: selectedSet,
      toggleSelect,
    },
  })

  const py = compact ? "py-1.5" : "py-2.5"
  const depthPad = (depth: number) => ({ paddingLeft: `${12 + depth * 20}px` })

  const handleRowClick = (row: Row<RowData>) => {
    if (multiSelect && rowIsSelectable(row.original)) {
      toggleSelect(row.id)
    }
    if (!selectable && !multiSelect) return
    const id = row.id
    setSelectedId(id)
    onSelect?.({
      rowId: id,
      depth: row.depth,
      row: row.original,
    })
  }

  const colSpan = columnDefs.length

  return (
    <div
      className="flex h-full w-full flex-col overflow-hidden rounded-xl border border-surface-border bg-surface shadow-table dark:border-slate-700 dark:bg-slate-950"
      style={{ maxHeight: height }}
    >
      <div className="overflow-auto" style={{ maxHeight: height }}>
        <table className="w-full min-w-full border-collapse">
          <thead className="sticky top-0 z-10 border-b border-surface-border bg-slate-50/95 backdrop-blur dark:border-slate-700 dark:bg-slate-900/95">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const spec = (header.column.columnDef.meta as { spec?: ColumnSpec } | undefined)?.spec
                  return (
                    <th
                      key={header.id}
                      className="px-3 py-2"
                      style={spec ? widthStyle(spec.width) : undefined}
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td
                  colSpan={colSpan}
                  className="px-4 py-8 text-center text-sm text-ink-muted dark:text-slate-400"
                >
                  No rows
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => {
                const isSelected = multiSelect
                  ? selectedSet.has(row.id)
                  : selectable && selectedId === row.id
                const zebra = striped && row.index % 2 === 1
                return (
                  <tr
                    key={row.id}
                    className={`border-b border-surface-border/60 transition-colors dark:border-slate-800 ${
                      isSelected
                        ? "bg-accent/10 dark:bg-blue-950/40"
                        : zebra
                          ? "bg-slate-50/60 dark:bg-slate-900/50"
                          : "bg-surface dark:bg-slate-950"
                    } ${selectable || multiSelect ? "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-900" : ""}`}
                    onClick={() => handleRowClick(row)}
                  >
                    {row.getVisibleCells().map((cell, cellIndex) => {
                      const spec = (cell.column.columnDef.meta as { spec?: ColumnSpec } | undefined)?.spec
                      const pad =
                        !hideExpand && (cellIndex === 0 || cell.column.id === "__expand")
                          ? depthPad(row.depth)
                          : cellIndex === 0 && hideExpand
                            ? depthPad(row.depth)
                            : undefined
                      return (
                        <td
                          key={cell.id}
                          className={`px-3 ${py}`}
                          style={{ ...pad, ...(spec ? widthStyle(spec.width) : undefined) }}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      )
                    })}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}