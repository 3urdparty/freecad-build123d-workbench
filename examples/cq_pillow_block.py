"""Example: parametric pillow block plate in CadQuery.

The CadQuery twin of bracket.py — open it via Code → Open Script. The
parameters below appear in FreeCAD's property panel; edit them there or
edit this file (embedded editor or your own) and save.

show_object() works exactly as it does in CQ-editor and ocp-vscode; the
name becomes the FreeCAD object's label and the color is applied to it.
"""

import cadquery as cq

# --- parameters (surface in FreeCAD's property panel) ---
length = 80.0
height = 60.0
thickness = 10.0
center_hole_d = 22.0
cbore_hole_d = 2.4
cbore_d = 4.4
cbore_depth = 2.1
PARAMS = [
    "length", "height", "thickness",
    "center_hole_d", "cbore_hole_d", "cbore_d", "cbore_depth",
]

# --- model ---
result = (
    cq.Workplane("XY")
    .box(length, height, thickness)
    .faces(">Z")
    .workplane()
    .hole(center_hole_d)
    .faces(">Z")
    .workplane()
    .rect(length - 8.0, height - 8.0, forConstruction=True)
    .vertices()
    .cboreHole(cbore_hole_d, cbore_d, cbore_depth)
    .edges("|Z")
    .fillet(2.0)
)

show_object(result, name="pillow_block", color="steelblue")  # noqa: F821
