import React, { memo } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"

import NodeStatusIndicator, { type NodeStatus } from "./NodeStatusIndicator"

export interface WorkflowNodeData {
  label?: string
  subtitle?: string
  background?: string
  highlighted?: boolean
  dimmed?: boolean
  status?: NodeStatus
}

const WorkflowNode = ({ data, selected }: NodeProps) => {
  const d = data as WorkflowNodeData
  const label = d.label ?? ""
  const subtitle = d.subtitle ?? ""
  const highlighted = Boolean(d.highlighted)
  const dimmed = Boolean(d.dimmed)
  const status = (d.status ?? "initial") as NodeStatus

  const card = (
    <div
      className={[
        "workflow-node",
        highlighted ? "workflow-node--highlighted" : "",
        selected ? "workflow-node--selected" : "",
        dimmed ? "workflow-node--dimmed" : "",
        status !== "initial" ? `workflow-node--status-${status}` : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={d.background ? { background: d.background } : undefined}
    >
      <Handle type="target" position={Position.Left} className="workflow-node__handle" />
      <div className="workflow-node__header">
        <span className="workflow-node__dot" style={{ background: d.background ?? "#a1a1aa" }} />
        <span className="workflow-node__title">{label}</span>
      </div>
      {subtitle ? <div className="workflow-node__subtitle">{subtitle}</div> : null}
      <Handle type="source" position={Position.Right} className="workflow-node__handle" />
    </div>
  )

  return (
    <NodeStatusIndicator status={status} variant="border">
      {card}
    </NodeStatusIndicator>
  )
}

export default memo(WorkflowNode)
