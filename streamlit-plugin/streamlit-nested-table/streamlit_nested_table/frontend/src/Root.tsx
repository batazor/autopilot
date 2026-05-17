import React, { useEffect, useState } from "react"
import { ComponentProps, Streamlit, withStreamlitConnection } from "streamlit-component-lib"

import NestedTable from "./NestedTable"
import type { ComponentArgs, MultiSelection, RowSelection } from "./types"

const Root = (props: ComponentProps) => {
  const args = props.args as ComponentArgs
  const [selection, setSelection] = useState<RowSelection | null>(null)
  const [selectedIds, setSelectedIds] = useState<string[]>(
    Array.isArray(args.selectedIds) ? args.selectedIds.map(String) : []
  )

  const rows = Array.isArray(args.rows) ? args.rows : []
  const columns = Array.isArray(args.columns) ? args.columns : []
  const subRowsKey = args.subRowsKey?.trim() || "subRows"
  const height = typeof args.height === "number" ? args.height : 420
  const defaultExpanded = Boolean(args.defaultExpanded)
  const striped = args.striped !== false
  const compact = Boolean(args.compact)
  const selectable = Boolean(args.selectable)
  const multiSelect = Boolean(args.multiSelect)
  const getRowId = args.getRowId?.trim() || "id"
  const hideExpand = Boolean(args.hideExpand)

  useEffect(() => {
    Streamlit.setFrameHeight(height + 8)
  }, [height, rows.length, columns.length])

  useEffect(() => {
    if (multiSelect) return
    if (selectable && selection) {
      Streamlit.setComponentValue(selection)
    }
  }, [selection, selectable, multiSelect])

  useEffect(() => {
    if (!multiSelect) return
    const payload: MultiSelection = { selectedIds }
    if (selection) payload.lastRow = selection
    Streamlit.setComponentValue(payload)
  }, [selectedIds, selection, multiSelect])

  const theme = props.theme as { base?: string } | undefined
  const isDark = theme?.base === "dark"

  return (
    <div
      className={isDark ? "dark h-full w-full bg-slate-900 text-slate-100" : "h-full w-full"}
      style={{
        pointerEvents: props.disabled ? "none" : "auto",
        opacity: props.disabled ? 0.6 : 1,
      }}
    >
      <NestedTable
        rows={rows}
        columns={columns}
        subRowsKey={subRowsKey}
        height={height}
        defaultExpanded={defaultExpanded}
        striped={striped}
        compact={compact}
        selectable={selectable}
        multiSelect={multiSelect}
        selectedIds={selectedIds}
        getRowId={getRowId}
        hideExpand={hideExpand}
        onSelect={
          selectable || multiSelect
            ? (sel) => {
                setSelection(sel)
              }
            : undefined
        }
        onSelectedIdsChange={multiSelect ? setSelectedIds : undefined}
      />
    </div>
  )
}

export default withStreamlitConnection(Root)
