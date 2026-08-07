"""Example: parametric mounting bracket in build123d.

Open this via Code → Open Script. The three parameters below appear in
FreeCAD's property panel; edit them there or edit this file in your own
editor and save — the model updates either way.
"""

from build123d import *  # noqa: F403

length = 60.0
width = 40.0
thickness = 6.0
hole_d = 5.0
PARAMS = ["length", "width", "thickness", "hole_d"]

with BuildPart() as bracket:  # noqa: F405
    Box(length, width, thickness)  # noqa: F405
    with Locations(  # noqa: F405
        (length / 2 - 8, width / 2 - 8),
        (-length / 2 + 8, width / 2 - 8),
        (length / 2 - 8, -width / 2 + 8),
        (-length / 2 + 8, -width / 2 + 8),
    ):
        Hole(radius=hole_d / 2)  # noqa: F405
    fillet(bracket.edges().filter_by(Axis.Z), radius=4)  # noqa: F405

show(bracket)  # noqa: F405
