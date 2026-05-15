"""Central registry of all st.session_state keys used across the Streamlit UI.

Import constants from here instead of repeating raw strings in multiple files.
Attribute-style accesses (``st.session_state.foo``) are left as-is where they
are scoped to a single file — this file covers cross-file and collision-prone keys.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Wiki module scope (Gallery + Labeling)
# ---------------------------------------------------------------------------
WIKI_MODULE_STORAGE_KEY = "wiki_module_storage_key"

# ---------------------------------------------------------------------------
# Labeling page (shared between views/labeling.py and labeling_reference_panel.py)
# ---------------------------------------------------------------------------
LABELING_TREE_SELECTION = "labeling_tree_selection"
LABELING_RENAME_FLASH = "labeling_rename_flash"
LABELING_ERROR_FLASH = "labeling_error_flash"
LABELING_BN_SYNC_SEL = "labeling_basename_sync_sel"
LABELING_PENDING_CAPTURE_REL = "labeling_pending_capture_rel"
LABELING_SELECTION_BEFORE_CAPTURE = "labeling_selection_before_capture"
LABELING_REF_TREE_NONCE = "labeling_ref_tree_nonce"
LABELING_LAST_INSTANCE = "labeling_bn_last_instance"
LABELING_BN_NONE = "labeling_bn_none"
LABELING_REFRESH_PENDING = "labeling_refresh_pending"

# ---------------------------------------------------------------------------
# Area annotator (shared between area_annotator.py, labeling_reference_panel.py,
# and views/labeling.py)
# ---------------------------------------------------------------------------
AREA_DOC = "area_doc"
CANVAS_REV = "canvas_rev"
CANVAS_LAST_SIG = "last_canvas_sig"
ENTRY_IDX = "entry_idx"

# ---------------------------------------------------------------------------
# Area annotator internals (single-file, listed here to prevent future collisions)
# ---------------------------------------------------------------------------
PIL_ORIGINAL = "pil_original"
ACTIVE_IMAGE_PATH = "active_image_path"
SELECTED_REGION_IDX = "selected_region_idx"
SELECTED_REGION_NAME = "selected_region_name"
# Last `active_region_name` observed from the canvas frontend. Used to react
# only to *changes* on the canvas side — without it, a stale value from a
# previous canvas click silently reverts radio-button selections.
CANVAS_LAST_ACTIVE_REGION = "canvas_last_active_region"
IMAGE_ERROR = "image_error"
LOAD_ERROR = "load_error"
PENDING_IMAGE_PATH = "pending_image_path"
ANNOT_LABELING_REF = "_annot_labeling_ref"
OVL_YAML_WARN = "_ovl_yaml_warn"
LABELING_TEMPORAL_REGIONS = "_labeling_temporal_regions"

# Session key suffix ``_lbl`` / ``_std``: ``f"{AREA_DELETE_REGION_PENDING_PREFIX}_{rk}"``.
AREA_DELETE_REGION_PENDING_PREFIX = "area_delete_region_pending"

# ---------------------------------------------------------------------------
# Overview page
# ---------------------------------------------------------------------------
OVERVIEW_FEEDBACK = "overview_feedback"

# ---------------------------------------------------------------------------
# Instance page
# ---------------------------------------------------------------------------
INSTANCE_PREVIEW_CACHE = "_instance_preview_cache"

# ---------------------------------------------------------------------------
# Pipeline overlay cache (Click approvals idle probe, etc.)
# ---------------------------------------------------------------------------
PIPELINE_OVERLAY_CACHE = "_pipeline_overlay_cache"
