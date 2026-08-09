"""The model and source code shown in the repository social preview."""

# This is intentionally idiomatic, copyable build123d example code.
# ruff: noqa: F403, F405


def show_object(*_objects: object, **_options: object) -> None:
    """Stand in for the workbench display hook when generating this asset."""


# social-preview-code:start
from build123d import *

housing_d = 58.0
bolt_spacing = 78.0
thickness = 9.0
bore_d = 24.0
PARAMS = ["housing_d", "bolt_spacing", "thickness", "bore_d"]

with BuildPart() as flange:
    with BuildSketch() as outline:
        housing = Circle(housing_d / 2)
        with GridLocations(bolt_spacing, 0, 2, 1) as bolts:
            Circle(13)
        make_hull()

    extrude(amount=thickness)
    extrude(housing, amount=27)
    draft(faces().filter_by(Axis.Z, reverse=True), Plane.XY, 4)
    fillet(edges(), radius=1.5)

    with Locations(faces().sort_by(Axis.Z)[-1]):
        CounterBoreHole(bore_d / 2, 23, 13)
    with Locations(*bolts):
        CounterSinkHole(4.5, 8.5)

show_object(flange, name="BearingFlange")
# social-preview-code:end
