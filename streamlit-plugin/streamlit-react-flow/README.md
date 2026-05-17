# streamlit-react-flow

Local Streamlit component for read-only node graphs, built on [@xyflow/react](https://reactflow.dev/) (React Flow v12).

## API

```python
from streamlit_react_flow import FlowEdge, FlowNode, react_flow

nodes: list[FlowNode] = [
    {"id": "main_city", "position": {"x": 0, "y": 0}, "data": {"label": "main_city"}},
]
edges: list[FlowEdge] = [
    {"id": "e0", "source": "main_city", "target": "mail"},
]

react_flow(nodes=nodes, edges=edges, height=500, width=1100, key="fsm-main")
```

## Build frontend

```bash
cd streamlit_react_flow/frontend
npm install
npm run build
```

Then from the repo root: `uv sync`
