import React from "react"

export type NodeStatus = "initial" | "loading" | "success" | "error"

interface NodeStatusIndicatorProps {
  status: NodeStatus
  variant?: "border" | "overlay"
  children: React.ReactNode
}

/**
 * Inspired by React Flow UI Node Status Indicator:
 * https://reactflow.dev/ui/components/node-status-indicator
 */
const NodeStatusIndicator = ({
  status,
  variant = "border",
  children,
}: NodeStatusIndicatorProps) => {
  return (
    <div
      className={[
        "node-status",
        `node-status--${status}`,
        `node-status--${variant}`,
      ].join(" ")}
    >
      {variant === "overlay" && status === "loading" ? (
        <div className="node-status__overlay" aria-hidden>
          <span className="node-status__spinner" />
        </div>
      ) : null}
      {children}
    </div>
  )
}

export default NodeStatusIndicator
