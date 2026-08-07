"""BREP serialization round-trip tests. Skipped unless OCP is installed
(CI installs the real CAD stack for these; see .github/workflows/ci.yml)."""

import base64
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "kernel"))

ocp = pytest.importorskip("OCP", reason="OCP not installed (kernel-env only)")

from fc_code_kernel.serialize import (  # noqa: E402
    extract_topods_shapes,
    serialize_object,
    shape_to_brep,
)
from fc_code_kernel.show import ShownObject  # noqa: E402


def _box():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()


def test_shape_to_brep_roundtrip(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    brep = shape_to_brep(_box())
    assert brep.startswith(b"DBRep_DrawableShape") or b"CASCADE" in brep[:200]

    # Round-trip through a file the way FreeCAD's importBrepFromString would.
    restored = TopoDS_Shape()
    path = str(tmp_path / "x.brep")
    with open(path, "wb") as f:
        f.write(brep)
    assert BRepTools.Read_s(restored, path, BRep_Builder())
    assert not restored.IsNull()


def test_extract_raw_topods():
    shapes = extract_topods_shapes(_box())
    assert len(shapes) == 1


def test_serialize_object_metadata():
    entry = serialize_object(ShownObject(_box(), "lid", {"color": "#ff0000"}), index=3)
    assert entry["name"] == "lid"
    assert entry["color"] == "#ff0000"
    assert base64.b64decode(entry["brep_b64"])


def test_build123d_unwrap():
    b3d = pytest.importorskip("build123d")
    box = b3d.Box(1, 2, 3)
    shapes = extract_topods_shapes(box)
    assert len(shapes) == 1
    assert type(shapes[0]).__name__.startswith("TopoDS")
