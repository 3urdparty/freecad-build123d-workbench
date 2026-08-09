#!/usr/bin/env python3
"""Generate the parameterised 7b Code Workbench logo as an SVG.

The mark is an orthographic projection of a cube, not a perspective drawing.
Changing ``--elevation`` changes the camera elevation and derives both the top
face depth and visible cube height, so the result remains a cube.

Examples:
    python assets/logo/generate_logo.py
    python assets/logo/generate_logo.py --elevation 35 --left-grey '#aab3bb'

By default this writes the runtime icon used by FreeCAD and the project README.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2]
    / "freecad"
    / "code"
    / "resources"
    / "icons"
    / "code_workbench.svg"
)


def point(point_: tuple[float, float]) -> str:
    """Format an SVG coordinate pair without visual noise."""
    return " ".join(f"{coordinate:.2f}".rstrip("0").rstrip(".") for coordinate in point_)


def lerp(
    start: tuple[float, float], end: tuple[float, float], amount: float
) -> tuple[float, float]:
    """Return the point ``amount`` of the way from start to end."""
    return (
        start[0] + (end[0] - start[0]) * amount,
        start[1] + (end[1] - start[1]) * amount,
    )


def generate_svg(
    *,
    size: float = 64,
    padding: float = 3.5,
    half_width: float | None = None,
    elevation_degrees: float = 48.59,
    left_grey: str = "#8796a0",
    right_grey: str = "#687985",
    code_green: str = "#35a85a",
    stroke_width: float = 5.25,
    bracket_length: float = 0.636,
) -> str:
    """Return the 7b logo as an SVG document.

    The visible faces and rounded bracket strokes are fitted into ``size`` with
    ``padding`` on every side. Pass ``half_width`` to override automatic fitting.
    At a 45-degree yaw, an orthographic cube projection has:

        top face half-height = half_width * sin(elevation)
        visible body height  = half_width * sqrt(2) * cos(elevation)

    These linked values are the essential cube constraint.
    """
    if size <= 0 or stroke_width <= 0:
        raise ValueError("size and stroke_width must be positive")
    if padding < 0 or 2 * padding >= size:
        raise ValueError("padding must be non-negative and less than half the size")
    if half_width is not None and half_width <= 0:
        raise ValueError("half_width must be positive when supplied")
    if not 0 < elevation_degrees < 90:
        raise ValueError("elevation must be strictly between 0 and 90 degrees")
    if not 0 < bracket_length < 1:
        raise ValueError("bracket_length must be strictly between 0 and 1")

    elevation = math.radians(elevation_degrees)
    sine_elevation = math.sin(elevation)
    cosine_elevation = math.cos(elevation)
    stroke_radius = stroke_width / 2

    if half_width is None:
        available = size - 2 * padding
        # Horizontal bounds include the rounded joins at the two outer corners.
        horizontal_limit = (available - 2 * stroke_radius) / 2
        # The top bound is the upper bracket endpoint plus its round cap. The
        # bottom bound is the un-stroked lower corner of the two filled faces.
        vertical_factor = (1 + bracket_length) * sine_elevation + math.sqrt(2) * cosine_elevation
        vertical_limit = (available - stroke_radius) / vertical_factor
        half_width = min(horizontal_limit, vertical_limit)
        if half_width <= 0:
            raise ValueError("stroke_width and padding leave no room for the logo")

    top_half_height = half_width * sine_elevation
    body_height = half_width * math.sqrt(2) * cosine_elevation

    center_x = size / 2
    # Centre what 7b actually draws. Its virtual top apex is deliberately absent.
    visible_top_offset = -bracket_length * top_half_height - stroke_radius
    visible_bottom_offset = top_half_height + body_height
    center_y = size / 2 - (visible_top_offset + visible_bottom_offset) / 2

    top = (center_x, center_y - top_half_height)
    left = (center_x - half_width, center_y)
    front = (center_x, center_y + top_half_height)
    right = (center_x + half_width, center_y)
    lower_left = (left[0], left[1] + body_height)
    lower_front = (front[0], front[1] + body_height)
    lower_right = (right[0], right[1] + body_height)

    left_upper = lerp(left, top, bracket_length)
    left_lower = lerp(left, front, bracket_length)
    right_upper = lerp(right, top, bracket_length)
    right_lower = lerp(right, front, bracket_length)

    view_box = f"0 0 {size:g} {size:g}"
    left_side = f"M {point(left)} L {point(front)} L {point(lower_front)} L {point(lower_left)} Z"
    right_side = (
        f"M {point(right)} L {point(front)} L {point(lower_front)} L {point(lower_right)} Z"
    )
    left_bracket = f"M {point(left_upper)} L {point(left)} L {point(left_lower)}"
    right_bracket = f"M {point(right_upper)} L {point(right)} L {point(right_lower)}"

    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="{size:g}" height="{size:g}" viewBox="{view_box}">
  <!-- Generated by assets/logo/generate_logo.py; do not edit this file by hand. -->
  <path d="{left_side}" fill="{left_grey}"/>
  <path d="{right_side}" fill="{right_grey}"/>
  <path d="{left_bracket}" fill="none" stroke="{code_green}"
        stroke-width="{stroke_width:g}" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="{right_bracket}" fill="none" stroke="{code_green}"
        stroke-width="{stroke_width:g}" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--size", type=float, default=64)
    parser.add_argument("--padding", type=float, default=3.5)
    parser.add_argument(
        "--half-width",
        type=float,
        help="override automatic fitting with a top-face horizontal half-diagonal",
    )
    parser.add_argument("--elevation", type=float, default=48.59, metavar="DEGREES")
    parser.add_argument("--left-grey", default="#8796a0")
    parser.add_argument("--right-grey", default="#687985")
    parser.add_argument("--code-green", default="#35a85a")
    parser.add_argument("--stroke-width", type=float, default=5.25)
    parser.add_argument("--bracket-length", type=float, default=0.636)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    svg = generate_svg(
        size=args.size,
        padding=args.padding,
        half_width=args.half_width,
        elevation_degrees=args.elevation,
        left_grey=args.left_grey,
        right_grey=args.right_grey,
        code_green=args.code_green,
        stroke_width=args.stroke_width,
        bracket_length=args.bracket_length,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
