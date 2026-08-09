"""Parametric bearing flange used throughout the Code Workbench documentation."""

# ruff: noqa: F403, F405

# social-preview-code:start
from build123d import *

housing_d = 58.0
bolt_spacing = 78.0
thickness = 9.0
bore_d = 24.0
bolt_hole_d = 9.0
PARAMS = ["housing_d", "bolt_spacing", "thickness", "bore_d", "bolt_hole_d"]

with BuildPart() as flange:
    with BuildSketch() as outline:
        housing = Circle(housing_d / 2)
        with GridLocations(bolt_spacing, 0, 2, 1) as bolts:
            Circle(13)
        make_hull()

    extrude(amount=thickness)
    extrude(housing, amount=27)
    if housing_d < 70:
        fillet(edges(), radius=1.5)

    with Locations(faces().sort_by(Axis.Z)[-1]):
        CounterBoreHole(bore_d / 2, 23, 13)
    with Locations(*bolts):
        CounterSinkHole(bolt_hole_d / 2, bolt_hole_d / 2 + 4)

show_object(
    flange,
    name="BearingFlange",
)
# social-preview-code:end
