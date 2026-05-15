import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react"
import { ComponentProps, Streamlit } from "streamlit-component-lib"

import EditorPanel from "./EditorPanel"
import FitViewOnChange from "./FitViewOnChange"
import WorkflowNode from "./WorkflowNode"
import { nextEdgeId, serializeEditorState } from "./editorUtils"

import "@xyflow/react/dist/style.css"

const nodeTypes = { workflow: WorkflowNode }

const PUSH_DEBOUNCE_MS = 280

interface PythonArgs {
  nodes?: Node[]
  edges?: Edge[]
  height?: number
  width?: number
  edgesLocked?: boolean
  showMinimap?: boolean
}

function normalizeNodes(nodes: Node[], search: string): Node[] {
  const q = search.trim().toLowerCase()
  if (!q) {
    return nodes.map((n) => ({
      ...n,
      type: n.type ?? "workflow",
      data: { ...n.data, dimmed: false },
    }))
  }
  return nodes.map((n) => {
    const label = String((n.data as { label?: string }).label ?? n.id).toLowerCase()
    const match = label.includes(q) || n.id.toLowerCase().includes(q)
    return {
      ...n,
      type: n.type ?? "workflow",
      data: { ...n.data, dimmed: !match },
    }
  })
}

const FlowEditorInner = ({ args }: ComponentProps) => {
  const {
    nodes: seedNodes = [],
    edges: seedEdges = [],
    height = 700,
    width = 1100,
    edgesLocked = false,
    showMinimap = true,
  } = args as PythonArgs

  const [search, setSearch] = useState("")
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const pushTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [nodes, setNodes, onNodesChange] = useNodesState(seedNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(seedEdges)
  const { fitView, deleteElements, getNodes, getEdges } = useReactFlow()

  useEffect(() => {
    setNodes(seedNodes)
    setEdges(seedEdges)
  }, [seedNodes, seedEdges, setNodes, setEdges])

  const displayNodes = useMemo(() => normalizeNodes(nodes, search), [nodes, search])

  const pushToStreamlit = useCallback(
    (nextNodes: Node[], nextEdges: Edge[], selectedNodeId: string | null) => {
      if (pushTimer.current) {
        clearTimeout(pushTimer.current)
      }
      pushTimer.current = setTimeout(() => {
        Streamlit.setComponentValue(
          serializeEditorState(nextNodes, nextEdges, selectedNodeId)
        )
      }, PUSH_DEBOUNCE_MS)
    },
    []
  )

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      setSelectedId(node.id)
      pushToStreamlit(nodes, edges, node.id)
    },
    [nodes, edges, pushToStreamlit]
  )

  const onNodeDragStop = useCallback(() => {
    pushToStreamlit(getNodes(), getEdges(), selectedId)
  }, [getNodes, getEdges, selectedId, pushToStreamlit])

  const onConnect = useCallback(
    (connection: Connection) => {
      if (edgesLocked) {
        return
      }
      setEdges((eds) => {
        const next = addEdge(
          {
            ...connection,
            id: nextEdgeId(eds),
            type: "smoothstep",
          },
          eds
        )
        pushToStreamlit(nodes, next, selectedId)
        return next
      })
    },
    [edgesLocked, nodes, selectedId, pushToStreamlit, setEdges]
  )

  const onEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      if (edgesLocked) {
        return
      }
      setEdges((eds) => {
        const ids = new Set(deleted.map((e) => e.id))
        const next = eds.filter((e) => !ids.has(e.id))
        pushToStreamlit(nodes, next, selectedId)
        return next
      })
    },
    [edgesLocked, nodes, selectedId, pushToStreamlit, setEdges]
  )

  const onNodesDelete = useCallback(
    (deleted: Node[]) => {
      const ids = new Set(deleted.map((n) => n.id))
      const nextId = selectedId && ids.has(selectedId) ? null : selectedId
      setSelectedId(nextId)
      window.setTimeout(() => {
        pushToStreamlit(getNodes(), getEdges(), nextId)
      }, 0)
    },
    [selectedId, pushToStreamlit, getNodes, getEdges]
  )

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedId) ?? null,
    [nodes, selectedId]
  )

  const handleFitView = useCallback(() => {
    fitView({ padding: 0.15, duration: 250 })
  }, [fitView])

  const handleDeleteSelected = useCallback(() => {
    if (!selectedNode) {
      return
    }
    void deleteElements({ nodes: [selectedNode] })
  }, [selectedNode, deleteElements])

  useEffect(() => {
    Streamlit.setFrameHeight(height + 8)
    Streamlit.setComponentReady()
  }, [height])

  useEffect(
    () => () => {
      if (pushTimer.current) {
        clearTimeout(pushTimer.current)
      }
    },
    []
  )

  return (
    <div style={{ width, height }} className="flow-editor">
      <ReactFlow
        nodes={displayNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onNodeDragStop={onNodeDragStop}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
        nodesDraggable
        nodesConnectable={!edgesLocked}
        elementsSelectable
        onBeforeDelete={
          edgesLocked
            ? async ({ edges: toDelete }) => toDelete.length === 0
            : undefined
        }
        deleteKeyCode={["Backspace", "Delete"]}
        panOnDrag
        zoomOnScroll
        minZoom={0.05}
        maxZoom={1.8}
        defaultEdgeOptions={{ type: "smoothstep" }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="#e4e4e7" />
        <Controls showInteractive />
        {showMinimap ? <MiniMap zoomable pannable /> : null}
        <FitViewOnChange nodes={displayNodes} edges={edges} enabled={true} />
        <EditorPanel
          search={search}
          onSearchChange={setSearch}
          selectedNode={selectedNode}
          edges={edges}
          edgesLocked={edgesLocked}
          onFitView={handleFitView}
          onDeleteSelected={handleDeleteSelected}
        />
      </ReactFlow>
    </div>
  )
}

const FlowEditor = (props: ComponentProps) => (
  <ReactFlowProvider>
    <FlowEditorInner {...props} />
  </ReactFlowProvider>
)

export default FlowEditor
