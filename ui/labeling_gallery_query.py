"""Query params for Gallery → Open in Labeling (no Streamlit)."""

from __future__ import annotations

from layout.area_versions import normalize_version_id


def open_in_labeling_query_params(
    labeling_ref: str,
    card_ver: str,
    *,
    module_key: str = "core",
) -> dict[str, str]:
    """``st.page_link(..., query_params=…)`` for the labeling deep-link.

    ``labeling_ref`` is the path under the active references tree (default ``ocr`` for the screen).
    ``card_ver`` is the gallery Layout key: ``auto``, ``default``, or a version id (e.g. ``v2``).
    """
    qp: dict[str, str] = {"ref": labeling_ref, "module": module_key}
    if card_ver in ("auto", "default"):
        qp["version"] = "default"
    else:
        vid = normalize_version_id(card_ver)
        qp["version"] = vid if vid else "default"
    return qp
