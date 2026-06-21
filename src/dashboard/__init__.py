"""Non-UI runtime helpers shared by the FastAPI server, worker, and tests.

Formerly ``src/ui/`` (Streamlit-era). Streamlit has been removed; this package
keeps the Redis/state/labeling helpers that back the Next.js dashboard via the
FastAPI server in ``src/api/``.
"""
