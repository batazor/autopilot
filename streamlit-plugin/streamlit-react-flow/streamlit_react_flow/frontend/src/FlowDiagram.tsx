import React, { useCallback, useEffect, useMemo } from "react"
import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
} from "@xyflow/react"
import { ComponentProps, Streamlit } from "streamlit-component-lib"

import FitViewOnChange from "./FitViewOnChange"
import ScreenNode from "./ScreenNode"

import "@xyflow/react/dist/style.css"

const nodeTypes = { screen: ScreenNode }

const HIGHLIGHT_EDGE_STYLE = { stroke: "#ea580c", strokeWidth: 2.5 }

type EdgePair = [string, string]

interface PythonArgs {
  nodes?: Node[]
  edges?: Edge[]
  height?: number
  width?: number
  highlightNodes?: string[]
  highlightEdges?: EdgePair[]
  selectable?: boolean
  showMinimap?: boolean
  showControls?: boolean
  fitView?: boolean
  legendItems?: LegendItem[]
}

interface LegendItem {
  label: string
  color: string
  kind: "node" | "edge" | "dashed-edge"
}

function applyHighlights(
  nodes: Node[],
  edges: Edge[],
  highlightNodes: string[],
  highlightEdges: EdgePair[]
): { nodes: Node[]; edges: Edge[] } {
  const hiNodeSet = new Set(highlightNodes)
  const hiEdgeSet = new Set(highlightEdges.map(([s, t]) => `${s}\0${t}`))

  const outNodes = nodes.map((n) => {
    const highlighted = Boolean((n.data as { highlighted?: boolean }).highlighted) || hiNodeSet.has(n.id)
    return highlighted === (n.data as { highlighted?: boolean }).highlighted
      ? n
      : { ...n, data: { ...n.data, highlighted } }
  })

  const outEdges = edges.map((e) => {
    const key = `${e.source}\0${e.target}`
    if (!hiEdgeSet.has(key)) {
      return e
    }
    return {
      ...e,
      animated: true,
      style: { ...e.style, ...HIGHLIGHT_EDGE_STYLE },
    }
  })

  return { nodes: outNodes, edges: outEdges }
}

const FlowDiagramInner = ({ args }: ComponentProps) => {
  const {
    nodes: rawNodes = [],
    edges: rawEdges = [],
    height = 500,
    width = 1100,
    highlightNodes = [],
    highlightEdges = [],
    selectable = false,
    showMinimap = false,
    showControls = true,
    fitView = true,
    legendItems = [],
  } = args as PythonArgs

  const { nodes, edges } = useMemo(
    () => applyHighlights(rawNodes, rawEdges, highlightNodes, highlightEdges),
    [rawNodes, rawEdges, highlightNodes, highlightEdges]
  )

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (selectable) {
        Streamlit.setComponentValue(node.id)
      }
    },
    [selectable]
  )

  useEffect(() => {
    Streamlit.setFrameHeight(height + 8)
    Streamlit.setComponentReady()
  }, [height])

  return (
    <div style={{ width, height }} className="flow-view">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={selectable}
        panOnDrag
        zoomOnScroll
        minZoom={0.08}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} size={1} />
        {showControls ? <Controls showInteractive={false} /> : null}
        {showMinimap ? <MiniMap zoomable pannable /> : null}
        {legendItems.length > 0 ? (
          <Panel position="top-right" className="flow-legend">
            {legendItems.map((item) => (
              <div className="flow-legend__item" key={`${item.kind}-${item.label}`}>
                <span
                  className={[
                    "flow-legend__swatch",
                    item.kind === "edge" ? "flow-legend__swatch--edge" : "",
                    item.kind === "dashed-edge" ? "flow-legend__swatch--dashed-edge" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  style={{ backgroundColor: item.kind === "node" ? item.color : undefined, borderColor: item.color }}
                />
                <span>{item.label}</span>
              </div>
            ))}
          </Panel>
        ) : null}
        <FitViewOnChange nodes={nodes} edges={edges} enabled={fitView} />
      </ReactFlow>
    </div>
  )
}

const FlowDiagram = (props: ComponentProps) => (
  <ReactFlowProvider>
    <FlowDiagramInner {...props} />
  </ReactFlowProvider>
)

export default FlowDiagram
