from dataclasses import dataclass
from typing import Any

import numpy as np
from _typeshed import Incomplete
from PIL.Image import Image

parent_dir: Incomplete
build_dir: Incomplete

@dataclass
class CanvasResult:
    image_data: np.ndarray = ...  # type: ignore[type-arg]
    json_data: dict[str, Any] = ...
    active_region_name: str = ...

def st_canvas(
    fill_color: str = "#eee",
    stroke_width: int = 20,
    stroke_color: str = "black",
    background_color: str = "",
    background_image: Image | None = None,
    update_streamlit: bool = True,
    height: int = 400,
    width: int = 600,
    drawing_mode: str = "freedraw",
    initial_drawing: dict[str, Any] | None = None,
    display_toolbar: bool = True,
    point_display_radius: int = 3,
    key: Any = None,
) -> CanvasResult: ...
