import React from "react"
import { ComponentProps, withStreamlitConnection } from "streamlit-component-lib"

import FlowDiagram from "./FlowDiagram"
import FlowEditor from "./FlowEditor"

const Root = (props: ComponentProps) => {
  const mode = (props.args as { mode?: string }).mode ?? "view"
  if (mode === "editor") {
    return <FlowEditor {...props} />
  }
  return <FlowDiagram {...props} />
}

export default withStreamlitConnection(Root)
