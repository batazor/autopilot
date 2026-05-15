import { useEffect } from "react"
import { useReactFlow, type Edge, type Node } from "@xyflow/react"

interface FitViewOnChangeProps {
  nodes: Node[]
  edges: Edge[]
  enabled: boolean
}

/** Re-fit viewport when graph data changes (Streamlit reruns). */
const FitViewOnChange = ({ nodes, edges, enabled }: FitViewOnChangeProps) => {
  const { fitView } = useReactFlow()

  useEffect(() => {
    if (!enabled) {
      return
    }
    const id = window.requestAnimationFrame(() => {
      fitView({ padding: 0.12, duration: 200 })
    })
    return () => window.cancelAnimationFrame(id)
  }, [nodes, edges, enabled, fitView])

  return null
}

export default FitViewOnChange
