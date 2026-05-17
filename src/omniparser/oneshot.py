"""One-shot OmniParser CLI used by local mode.

The parent UI process starts this module in a subprocess for a single image.
After parsing, the process exits and releases model memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from PIL import Image

from omniparser.local import health_status, parse_icon_detect_image, parse_image


def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--output")
    parser.add_argument("--box-threshold", type=float, default=0.05)
    parser.add_argument("--iou-threshold", type=float, default=0.1)
    parser.add_argument("--imgsz", type=int)
    parser.add_argument("--no-paddleocr", action="store_true")
    parser.add_argument("--backend", choices=["icon_detect", "full"], default="icon_detect")
    args = parser.parse_args()

    if args.health:
        print(json.dumps(health_status(), ensure_ascii=False))
        return

    if not args.image:
        _die("--image is required")
    if not args.output:
        _die("--output is required")

    image_path = Path(args.image)
    output_path = Path(args.output)
    image = Image.open(image_path).convert("RGB")
    if args.backend == "icon_detect":
        elements = parse_icon_detect_image(
            image,
            box_threshold=args.box_threshold,
            imgsz=args.imgsz,
        )
    else:
        elements = parse_image(
            image,
            box_threshold=args.box_threshold,
            iou_threshold=args.iou_threshold,
            use_paddleocr=not args.no_paddleocr,
            imgsz=args.imgsz,
        )
    output_path.write_text(
        json.dumps(
            {
                "width": image.size[0],
                "height": image.size[1],
                "elements": elements,
                "count": len(elements),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
