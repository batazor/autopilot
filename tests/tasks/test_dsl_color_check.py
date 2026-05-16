import numpy as np

from layout.color_bucket import dominant_color_label_bgr


def test_dominant_color_label_primary_colors() -> None:
    red = np.full((40, 60, 3), (0, 0, 255), dtype=np.uint8)  # BGR
    blue = np.full((40, 60, 3), (255, 0, 0), dtype=np.uint8)
    green = np.full((40, 60, 3), (0, 255, 0), dtype=np.uint8)
    gray = np.full((40, 60, 3), (128, 128, 128), dtype=np.uint8)

    assert dominant_color_label_bgr(red)[0] == "red"
    assert dominant_color_label_bgr(blue)[0] == "blue"
    assert dominant_color_label_bgr(green)[0] == "green"
    assert dominant_color_label_bgr(gray)[0] == "gray"

