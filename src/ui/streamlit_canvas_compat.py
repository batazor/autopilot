"""Patch ``streamlit-drawable-canvas`` for Streamlit 1.39+ (``image_to_url`` moved off ``elements.image``)."""
from __future__ import annotations

_PATCHED = False


def ensure_drawable_canvas_compat() -> None:
    """Register ``streamlit.elements.image.image_to_url`` if missing (legacy canvas package)."""

    global _PATCHED
    if _PATCHED:
        return

    import streamlit.elements.image as st_image_module

    if hasattr(st_image_module, "image_to_url"):
        _PATCHED = True
        return

    from streamlit.elements.lib.image_utils import image_to_url as _image_to_url_impl
    from streamlit.elements.lib.layout_utils import create_layout_config

    def image_to_url(image, width, clamp, channels, output_format, image_id) -> str:  # noqa: ANN001
        layout_config = create_layout_config(width=width, allow_content_width=True)
        return _image_to_url_impl(
            image,
            layout_config,
            clamp,
            channels,
            output_format,
            image_id,
        )

    st_image_module.image_to_url = image_to_url  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _PATCHED = True
