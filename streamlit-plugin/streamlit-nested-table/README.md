# streamlit-nested-table

Streamlit custom component: expandable nested rows on [@tanstack/react-table](https://tanstack.com/table) with [Tailwind CSS](https://tailwindcss.com/) styling.

## Build frontend

```bash
cd streamlit_nested_table/frontend
npm install
npm run build
```

Dev server (hot reload against Streamlit):

```bash
npm start
```

Set `_RELEASE = False` in `streamlit_nested_table/__init__.py` and use port `3002`.

## Python usage

```python
from streamlit_nested_table import nested_table, table_column

rows = [
    {
        "id": "mail",
        "name": "Mail",
        "count": 3,
        "subRows": [
            {"id": "m1", "name": "Alliance gift", "count": 1},
            {"id": "m2", "name": "System mail", "count": 2},
        ],
    },
]

nested_table(
    rows,
    columns=[
        table_column("name", "Scenario"),
        table_column("count", "Items", align="right"),
    ],
    sub_rows_key="subRows",
    height=360,
)
```

Child rows use the same column `accessor_key` fields as parents. Override the child key with `sub_rows_key` (default `subRows`).
