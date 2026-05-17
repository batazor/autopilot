import React from "react"
import { Panel } from "@xyflow/react"
import type { Edge, Node } from "@xyflow/react"

interface EditorPanelProps {
  search: string
  onSearchChange: (value: string) => void
  selectedNode: Node | null
  edges: Edge[]
  edgesLocked: boolean
  onFitView: () => void
  onDeleteSelected: () => void
}

const EditorPanel = ({
  search,
  onSearchChange,
  selectedNode,
  edges,
  edgesLocked,
  onFitView,
  onDeleteSelected,
}: EditorPanelProps) => {
  const inCount = selectedNode
    ? edges.filter((e) => e.target === selectedNode.id).length
    : 0
  const outCount = selectedNode
    ? edges.filter((e) => e.source === selectedNode.id).length
    : 0

  return (
    <Panel position="top-left" className="editor-panel">
      <p className="editor-panel__title">Route graph editor</p>
      <input
        className="editor-panel__search nodrag"
        placeholder="Search screens…"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
      />
      <p className="editor-panel__hint">
        Drag nodes · connect handles · Delete/Backspace removes selection
        {edgesLocked ? " · edges locked" : ""}
      </p>
      <button type="button" className="editor-panel__btn nodrag" onClick={onFitView}>
        Fit view
      </button>
      <button
        type="button"
        className="editor-panel__btn editor-panel__btn--danger nodrag"
        onClick={onDeleteSelected}
        disabled={!selectedNode}
      >
        Delete selected
      </button>
      {selectedNode ? (
        <div className="editor-panel__selection">
          <p className="editor-panel__selection-label">Selected</p>
          <p className="editor-panel__selection-id">{selectedNode.id}</p>
          {(selectedNode.data as { subtitle?: string }).subtitle ? (
            <p className="editor-panel__selection-sub">
              {(selectedNode.data as { subtitle?: string }).subtitle}
            </p>
          ) : null}
          <div className="editor-panel__stats">
            <span>{inCount} in</span>
            <span>{outCount} out</span>
          </div>
        </div>
      ) : (
        <p className="editor-panel__selection editor-panel__selection--empty">
          Click a node to inspect
        </p>
      )}
    </Panel>
  )
}

export default EditorPanel
