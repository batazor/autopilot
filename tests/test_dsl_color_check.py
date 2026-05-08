import numpy as np

from tasks.dsl_scenario import _dominant_color_label


def test_dominant_color_label_primary_colors() -> None:
    red = np.full((40, 60, 3), (0, 0, 255), dtype=np.uint8)  # BGR
    blue = np.full((40, 60, 3), (255, 0, 0), dtype=np.uint8)
    green = np.full((40, 60, 3), (0, 255, 0), dtype=np.uint8)
    gray = np.full((40, 60, 3), (128, 128, 128), dtype=np.uint8)

    assert _dominant_color_label(red)[0] == "red"
    assert _dominant_color_label(blue)[0] == "blue"
    assert _dominant_color_label(green)[0] == "green"
    assert _dominant_color_label(gray)[0] == "gray"

