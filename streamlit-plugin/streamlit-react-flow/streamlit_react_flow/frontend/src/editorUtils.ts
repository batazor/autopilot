import type { Edge, Node } from "@xyflow/react"

export interface EditorPayload {
  nodes: Array<{
    id: string
    type?: string
    position: { x: number; y: number }
    data?: Record<string, unknown>
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    type?: string
    animated?: boolean
    style?: Record<string, unknown>
  }>
  selectedNodeId: string | null
}

export function serializeEditorState(
  nodes: Node[],
  edges: Edge[],
  selectedNodeId: string | null
): EditorPayload {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: { x: n.position.x, y: n.position.y },
      data: n.data as Record<string, unknown>,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: e.type,
      animated: e.animated,
      style: e.style as Record<string, unknown> | undefined,
    })),
    selectedNodeId,
  }
}

export function nextEdgeId(edges: Edge[]): string {
  let max = -1
  for (const e of edges) {
    const m = /^e(\d+)$/.exec(e.id)
    if (m) {
      max = Math.max(max, Number(m[1]))
    }
  }
  return `e${max + 1}`
}
