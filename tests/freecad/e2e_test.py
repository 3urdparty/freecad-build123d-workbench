"""End-to-end test of the whole Code Workbench loop, headless.

Run inside FreeCAD's console interpreter:

    freecadcmd tests/freecad/e2e_test.py          # from the repo root
    FC_CODE_ADDON_DIR=/path/to/repo freecadcmd tests/freecad/e2e_test.py

Exercises: script file -> kernel (provisioned on first run) -> BREP ->
parametric document object -> parameter change -> recompute -> file edit
(simulated editor save) -> recompute -> script error handling.

The QFileSystemWatcher hot-reload event itself needs a Qt event loop, so
headless we edit the file and trigger the recompute directly; the watcher
is a GUI-only convenience on top of exactly this path.

Prints one PASS/FAIL line per check and a final "E2E RESULT: PASS|FAIL"
marker (run_e2e.sh greps for it — freecadcmd does not reliably propagate
exit codes across versions).
"""

import os
import shutil
import sys
import tempfile

ADDON = os.environ.get("FC_CODE_ADDON_DIR") or os.getcwd()
sys.path.insert(0, ADDON)

import FreeCAD as App  # noqa: E402

# FreeCAD imports its own `freecad` namespace package during startup —
# before this script could put the repo on sys.path — so its __path__ was
# computed without us. PREPEND the repo's package dir so this checkout wins
# over any copy installed in Mod/ (otherwise the test can silently exercise
# stale code; a symlinked Mod install is unaffected either way).
import freecad  # noqa: E402

_pkg_dir = os.path.join(ADDON, "freecad")
if _pkg_dir in freecad.__path__:
    freecad.__path__.remove(_pkg_dir)
freecad.__path__.insert(0, _pkg_dir)
print(f"[e2e] addon package resolved from: {freecad.__path__[0]}")

_failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[e2e] {status}: {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _failures.append(name)


def main() -> None:
    from freecad.code.feature import make_script_object

    # Work on a temp copy so the "editor save" step doesn't dirty the repo.
    tmp = tempfile.mkdtemp(prefix="fc_code_e2e_")
    script = os.path.join(tmp, "bracket.py")
    shutil.copy(os.path.join(ADDON, "examples", "bracket.py"), script)

    doc = App.newDocument("E2E")

    # 1. Script -> kernel -> BREP -> document object with a real shape.
    obj = make_script_object(doc, script)
    has_shape = obj.Shape is not None and not obj.Shape.isNull()
    check("script produces a shape", has_shape)
    v0 = obj.Shape.Volume if has_shape else 0.0
    check("shape has positive volume", v0 > 0, f"volume={v0:.1f}")

    # 2. Declared parameters surfaced as FreeCAD properties.
    check("parameters surfaced as properties",
          all(hasattr(obj, p) for p in ("length", "width", "thickness", "hole_d")))
    check("parameter default read from script",
          getattr(obj, "length", None) == 60.0, f"length={getattr(obj, 'length', None)}")

    # 3. Property panel edit -> recompute -> new geometry.
    obj.length = 100.0
    obj.touch()
    doc.recompute()
    v1 = obj.Shape.Volume
    check("parameter change recomputes geometry", v1 > v0 * 1.3,
          f"{v0:.1f} -> {v1:.1f}")

    # 4. Editor save (file edit) -> recompute -> new geometry.
    #    Edit a non-parameter value: fillet radius 4 -> 1 (removes less
    #    material, so volume increases). Parameter defaults in the file are
    #    intentionally overridden by the object's properties, so we don't
    #    edit those.
    with open(script, "r", encoding="utf-8") as f:
        source = f.read()
    assert "radius=4" in source, "example changed; update this test"
    with open(script, "w", encoding="utf-8") as f:
        f.write(source.replace("radius=4", "radius=1"))
    obj.touch()
    doc.recompute()
    v2 = obj.Shape.Volume
    check("file edit recomputes geometry", v2 > v1, f"{v1:.1f} -> {v2:.1f}")

    # 5. A broken script must not crash FreeCAD or destroy the last good
    #    shape — the error is reported and the object keeps its geometry.
    print("[e2e] NOTE: the recompute error below is intentional (check 5)")
    with open(script, "w", encoding="utf-8") as f:
        f.write("raise RuntimeError('intentional e2e failure')\n")
    obj.touch()
    doc.recompute()
    check("script error preserves last good shape",
          abs(obj.Shape.Volume - v2) < 1e-9)

    # 6. Kernel survives and recovers: restore a good script and re-run.
    with open(script, "w", encoding="utf-8") as f:
        f.write(source)  # original bracket
    obj.touch()
    doc.recompute()
    check("recovers after script error", abs(obj.Shape.Volume - v1) < 1e-6,
          f"volume={obj.Shape.Volume:.1f}, expected {v1:.1f}")

    # 7. Persistence: save to .FCStd, close, reopen. The shape, the
    #    parameter values, and the proxy must all survive — and the object
    #    must still recompute through the kernel after restore. This is the
    #    "results are real document objects" claim, tested.
    fcstd = os.path.join(tmp, "e2e.FCStd")
    obj_name = obj.Name
    v_saved = obj.Shape.Volume
    doc.saveAs(fcstd)
    App.closeDocument(doc.Name)
    doc2 = App.openDocument(fcstd)
    obj2 = doc2.getObject(obj_name)
    check("object survives save/reload", obj2 is not None)
    check("shape survives save/reload",
          obj2 is not None and abs(obj2.Shape.Volume - v_saved) < 1e-6)
    check("parameter values survive save/reload",
          getattr(obj2, "length", None) == 100.0,
          f"length={getattr(obj2, 'length', None)}")
    if obj2 is not None:
        obj2.length = 80.0
        obj2.touch()
        doc2.recompute()
        check("recomputes through kernel after reload",
              0 < obj2.Shape.Volume < v_saved,
              f"volume={obj2.Shape.Volume:.1f}")
    App.closeDocument(doc2.Name)

    # 8. CadQuery scripts work through the same pipeline (Workplane
    #    unwrapping via .vals(), not build123d's .wrapped).
    cq_script = os.path.join(tmp, "cq_box.py")
    with open(cq_script, "w", encoding="utf-8") as f:
        f.write(
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(10, 10, 10)\n"
            "show_object(result, name='cq_box')\n"
        )
    doc3 = App.newDocument("E2E_CQ")
    cq_obj = make_script_object(doc3, cq_script)
    check("cadquery script produces correct geometry",
          abs(cq_obj.Shape.Volume - 1000.0) < 1e-6,
          f"volume={cq_obj.Shape.Volume:.1f}")

    # 9. Multiple shown objects arrive as a compound (v0 contract).
    multi_script = os.path.join(tmp, "multi.py")
    with open(multi_script, "w", encoding="utf-8") as f:
        f.write(
            "from build123d import *\n"
            "a = Box(10, 10, 10)\n"
            "b = Pos(30, 0, 0) * Box(5, 5, 5)\n"
            "show(a, b, names=['a', 'b'])\n"
        )
    multi_obj = make_script_object(doc3, multi_script)
    check("multiple shown objects become a compound",
          abs(multi_obj.Shape.Volume - 1125.0) < 1e-6
          and multi_obj.Shape.ShapeType == "Compound",
          f"volume={multi_obj.Shape.Volume:.1f}, type={multi_obj.Shape.ShapeType}")
    App.closeDocument(doc3.Name)

    shutil.rmtree(tmp, ignore_errors=True)


try:
    main()
except Exception as exc:  # infrastructure failure, not a check failure
    import traceback

    traceback.print_exc()
    _failures.append(f"unhandled exception: {exc}")

print(f"E2E RESULT: {'FAIL' if _failures else 'PASS'}"
      + (f"  failed: {_failures}" if _failures else ""))
