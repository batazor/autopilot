export type Align = "left" | "center" | "right"
export type CellType = "text" | "link" | "bool" | "pill"

/** Hint for ``pill`` cells (soft Tailwind badge colors). */
export type PillPreset =
  | "scheduled"
  | "coop"
  | "reachable"
  | "history_status"
  | "rank_indicator"
  | "fleet_status"

export interface ColumnSpec {
  id: string
  header: string
  accessor_key: string
  width?: number | string | null
  align?: Align
  cell_type?: CellType
  /** For ``link`` cells — row key for anchor text (default: same as ``accessor_key``). */
  link_text_key?: string
  /** For ``pill`` cells — how to pick badge colors from the text value. */
  pill_preset?: PillPreset
}

export interface ComponentArgs {
  rows: Record<string, unknown>[]
  columns: ColumnSpec[]
  subRowsKey?: string
  height?: number
  width?: number | null
  defaultExpanded?: boolean
  striped?: boolean
  compact?: boolean
  selectable?: boolean
  multiSelect?: boolean
  selectedIds?: string[]
  getRowId?: string
  /** Flat table: hide the expand column (no nested rows). */
  hideExpand?: boolean
}

export interface RowSelection {
  rowId: string
  depth: number
  row: Record<string, unknown>
}

export interface MultiSelection {
  selectedIds: string[]
  lastRow?: RowSelection
}
