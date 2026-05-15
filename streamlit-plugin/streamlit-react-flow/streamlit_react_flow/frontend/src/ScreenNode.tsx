import React, { memo } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"

export interface ScreenNodeData {
  label?: string
  subtitle?: string
  background?: string
  highlighted?: boolean
}

const ScreenNode = ({ data, selected }: NodeProps) => {
  const d = data as ScreenNodeData
  const label = d.label ?? ""
  const subtitle = d.subtitle ?? ""
  const highlighted = Boolean(d.highlighted)
  const background = d.background ?? "#f4f4f5"

  return (
    <div
      className={[
        "screen-node",
        highlighted ? "screen-node--highlighted" : "",
        selected ? "screen-node--selected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ background }}
      title={label}
    >
      <Handle type="target" position={Position.Top} className="screen-node__handle" />
      <div className="screen-node__label">{label}</div>
      {subtitle ? <div className="screen-node__subtitle">{subtitle}</div> : null}
      <Handle type="source" position={Position.Bottom} className="screen-node__handle" />
    </div>
  )
}

export default memo(ScreenNode)
