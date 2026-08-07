"""Turn shown objects into (BREP bytes, metadata) for the wire.

This module is the only place the kernel touches OCP types directly, and the
imports are lazy so the rest of the kernel stays importable in minimal test
environments.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from .show import ShownObject


def serialize_object(item: ShownObject, index: int = 0) -> dict:
    shapes = extract_topods_shapes(item.obj)
    if not shapes:
        raise TypeError(f"not a recognizable CAD object: {type(item.obj).__name__}")
    shape = shapes[0] if len(shapes) == 1 else _compound(shapes)
    brep = shape_to_brep(shape)
    return {
        "name": item.name or f"object_{index}",
        "brep_b64": base64.b64encode(brep).decode("ascii"),
        "color": item.options.get("color"),
        "alpha": item.options.get("alpha"),
    }


def extract_topods_shapes(obj: Any) -> list:
    """Unwrap build123d objects, CadQuery Workplanes, or raw TopoDS_Shapes."""
    # Raw OCP shape
    if type(obj).__name__.startswith("TopoDS"):
        return [obj]
    # build123d BuildPart/BuildSketch/BuildLine contexts expose .part/.sketch/.line
    for attr in ("part", "sketch", "line"):
        inner = getattr(obj, attr, None)
        if inner is not None and hasattr(inner, "wrapped"):
            obj = inner
            break
    # build123d Shape / CadQuery Shape: .wrapped is a TopoDS_Shape
    wrapped = getattr(obj, "wrapped", None)
    if wrapped is not None and type(wrapped).__name__.startswith("TopoDS"):
        return [wrapped]
    # CadQuery Workplane: .vals() -> [Shape]
    vals = getattr(obj, "vals", None)
    if callable(vals):
        return [v.wrapped for v in vals() if getattr(v, "wrapped", None) is not None]
    return []


def shape_to_brep(shape) -> bytes:
    from OCP.BRepTools import BRepTools

    buf = io.BytesIO()
    BRepTools.Write_s(shape, buf)
    return buf.getvalue()


def _compound(shapes: list):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for s in shapes:
        builder.Add(compound, s)
    return compound
