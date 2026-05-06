"""
Optional entrypoint for the OCR region annotator only (same canvas as dashboard **Labeling**).

Prefer: ``wos`` / ``streamlit run ui/app.py`` → sidebar **Labeling** (references + ``area.json`` editor).

``area.json`` and ``references/`` are at the repository root.
"""

from __future__ import annotations

import streamlit as st

from ui.area_annotator import render_area_annotator_ui


def main() -> None:
    st.set_page_config(page_title="OCR Region Annotator", layout="wide")
    render_area_annotator_ui()


if __name__ == "__main__":
    main()
