"""Color parsing for show()/show_object() options.

FreeCAD-free and Qt-free on purpose: unit-testable anywhere, and usable
headless. Accepts the shapes ocp_vscode users actually pass: hex strings
("#f80", "#ff8800"), named web colors, and numeric triples (0–1 floats or
0–255 ints). Returns an (r, g, b) tuple of 0–1 floats, or None for
anything unparseable — appearance is advisory, never an error.
"""

from __future__ import annotations

# The subset of CSS names that show up in real CAD scripts. QColor would
# know them all, but Qt is not available in the kernel or headless.
NAMED = {
    "black": (0.0, 0.0, 0.0), "white": (1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0), "green": (0.0, 0.5, 0.0), "lime": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0), "yellow": (1.0, 1.0, 0.0),
    "cyan": (0.0, 1.0, 1.0), "magenta": (1.0, 0.0, 1.0),
    "orange": (1.0, 0.647, 0.0), "purple": (0.5, 0.0, 0.5),
    "pink": (1.0, 0.753, 0.796), "brown": (0.647, 0.165, 0.165),
    "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5),
    "silver": (0.753, 0.753, 0.753), "gold": (1.0, 0.843, 0.0),
    "navy": (0.0, 0.0, 0.5), "teal": (0.0, 0.5, 0.5),
    "olive": (0.5, 0.5, 0.0), "maroon": (0.5, 0.0, 0.0),
    "steelblue": (0.275, 0.51, 0.706), "tomato": (1.0, 0.388, 0.278),
}


def parse_color(value) -> tuple[float, float, float] | None:
    """Best-effort parse to an (r, g, b) tuple of floats in [0, 1]."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("#"):
            return _parse_hex(text[1:])
        return NAMED.get(text)
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return _parse_triple(value[:3])
    return None


def _parse_hex(digits: str) -> tuple[float, float, float] | None:
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) not in (6, 8):  # tolerate #rrggbbaa; alpha handled separately
        return None
    try:
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return (r / 255.0, g / 255.0, b / 255.0)


def _parse_triple(triple) -> tuple[float, float, float] | None:
    try:
        nums = [float(x) for x in triple]
    except (TypeError, ValueError):
        return None
    if any(n < 0 for n in nums):
        return None
    if all(n <= 1.0 for n in nums):
        return tuple(nums)  # already 0–1 floats
    if all(n <= 255 for n in nums):
        return tuple(n / 255.0 for n in nums)
    return None
