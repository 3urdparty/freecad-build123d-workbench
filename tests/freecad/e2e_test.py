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
exit codes across versions). Set FC_CODE_E2E_LOG to also write those lines
to a file, which is how run_e2e.sh reads the result: freecadcmd's stdout is
discarded outright by some builds when it is not a tty.
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

_failures = []

# freecadcmd's stdout is not dependable when it is not a tty: the
# conda-forge Linux build drops it wholesale — banner, prints and all — so a
# run that passed looks identical to one that never started. Mirror every
# line into FC_CODE_E2E_LOG when it is set (run_e2e.sh does) and let that
# file, not stdout, be what decides the result. Line-buffered so a crash
# still leaves everything printed so far.
_log_file = None
if os.environ.get("FC_CODE_E2E_LOG"):
    _log_file = open(os.environ["FC_CODE_E2E_LOG"], "w", encoding="utf-8", buffering=1)


def emit(line: str) -> None:
    try:
        print(line)
    except UnicodeEncodeError:  # ascii-pipe stdout must not kill the run
        print(line.encode("ascii", "backslashreplace").decode("ascii"))
    if _log_file is not None:
        _log_file.write(line + "\n")


# CI never reliably shows FreeCAD's console (stdout/stderr handling differs
# per build), so recompute errors and "[Code] ..." warnings vanish exactly
# when they matter most. Tee every console channel into the verdict stream.
def _tee_console() -> None:
    for chan in ("PrintMessage", "PrintWarning", "PrintError"):
        orig = getattr(App.Console, chan, None)
        if orig is None:
            continue

        def _wrap(msg, _orig=orig, _chan=chan):
            emit(f"[console:{_chan[5:].lower()}] {str(msg).rstrip()}")
            try:
                _orig(msg)
            except Exception:
                pass

        try:
            setattr(App.Console, chan, _wrap)
        except Exception:
            pass  # console not patchable on this build — lose nothing


_tee_console()


emit(f"[e2e] addon package resolved from: {freecad.__path__[0]}")


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    emit(f"[e2e] {status}: {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _failures.append(name)


def vol(o) -> float:
    """Shape volume, 0.0 for a missing/null shape — a shapeless object must
    fail its checks, not abort the whole run with 'shape is invalid'."""
    try:
        s = o.Shape
        return 0.0 if s is None or s.isNull() else s.Volume
    except Exception:
        return 0.0


def warmup() -> None:
    """First CAD import in the kernel, timed and reported on its own.

    The first kernel.run pays the cold `from build123d import *` (hundreds of
    MB of shared objects) — on a resource-starved CI runner that is exactly
    when the kernel dies, and the failure used to surface two checks later as
    "script produces a shape FAIL" with the real error dropped along with the
    console. Running it explicitly makes the verdict name the true culprit.
    """
    import time

    from freecad.code.kernel_manager import KernelManager

    wu_dir = tempfile.mkdtemp(prefix="fc_code_e2e_wu_")
    wu = os.path.join(wu_dir, "warmup.py")
    with open(wu, "w", encoding="utf-8") as f:
        f.write("from build123d import *\n"
                "import cadquery  # noqa: F401\n"
                "show(Box(1, 1, 1))\n")
    t0 = time.monotonic()
    try:
        result = KernelManager.instance().run_script(wu, {})
    except Exception as exc:
        check("kernel warmup (first CAD import)", False,
              f"{type(exc).__name__}: {exc} after {time.monotonic() - t0:.1f}s")
        shutil.rmtree(wu_dir, ignore_errors=True)
        return
    detail = f"{time.monotonic() - t0:.1f}s"
    if result.get("error"):
        detail += f", error: {result['error']}"
    if result.get("stderr"):
        emit(f"[e2e] warmup stderr: {result['stderr'].strip()}")
    check("kernel warmup (first CAD import)",
          not result.get("error") and bool(result.get("objects")), detail)
    shutil.rmtree(wu_dir, ignore_errors=True)


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
    v0 = vol(obj)
    check("shape has positive volume", v0 > 0, f"volume={v0:.1f}")
    if not has_shape or v0 <= 0:
        proxy = getattr(obj, "Proxy", None)
        emit(f"[e2e] first-run diagnostics: state={list(obj.State)!r}, "
             f"last_error={getattr(proxy, 'last_error', '<unset>')!r}")

    # 2. Declared parameters surfaced as FreeCAD properties.
    check("parameters surfaced as properties",
          all(hasattr(obj, p) for p in ("length", "width", "thickness", "hole_d")))
    check("parameter default read from script",
          getattr(obj, "length", None) == 60.0, f"length={getattr(obj, 'length', None)}")

    # 3. Property panel edit -> recompute -> new geometry.
    obj.length = 100.0
    obj.touch()
    doc.recompute()
    v1 = vol(obj)
    check("parameter change recomputes geometry", v1 > v0 * 1.3,
          f"{v0:.1f} -> {v1:.1f}")

    # 4. Editor save (file edit) -> recompute -> new geometry.
    #    Edit a non-parameter value: fillet radius 4 -> 1 (removes less
    #    material, so volume increases). Parameter defaults in the file are
    #    intentionally overridden by the object's properties, so we don't
    #    edit those.
    with open(script, encoding="utf-8") as f:
        source = f.read()
    assert "radius=4" in source, "example changed; update this test"
    with open(script, "w", encoding="utf-8") as f:
        f.write(source.replace("radius=4", "radius=1"))
    obj.touch()
    doc.recompute()
    v2 = vol(obj)
    check("file edit recomputes geometry", v2 > v1, f"{v1:.1f} -> {v2:.1f}")

    # 5. A broken script must not crash FreeCAD or destroy the last good
    #    shape — the error is reported and the object keeps its geometry.
    emit("[e2e] NOTE: the recompute error below is intentional (check 5)")
    with open(script, "w", encoding="utf-8") as f:
        f.write("raise RuntimeError('intentional e2e failure')\n")
    obj.touch()
    doc.recompute()
    check("script error preserves last good shape",
          abs(vol(obj) - v2) < 1e-9)

    # 6. Kernel survives and recovers: restore a good script and re-run.
    with open(script, "w", encoding="utf-8") as f:
        f.write(source)  # original bracket
    obj.touch()
    doc.recompute()
    check("recovers after script error", abs(vol(obj) - v1) < 1e-6,
          f"volume={vol(obj):.1f}, expected {v1:.1f}")

    # 5b. Runaway script: an infinite loop must be killed at RunTimeoutS,
    #     the last good shape preserved, and the next run must succeed on a
    #     freshly restarted kernel. With editor autosave, a half-typed
    #     `while True:` is an everyday event.
    import time as _time

    prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CodeWorkbench")
    orig_timeout = prefs.GetInt("RunTimeoutS", 60)
    prefs.SetInt("RunTimeoutS", 3)
    emit("[e2e] NOTE: the timeout error below is intentional (check 5b)")
    with open(script, "w", encoding="utf-8") as f:
        f.write("import time\nwhile True:\n    time.sleep(0.05)\n")
    t0 = _time.monotonic()
    obj.touch()
    doc.recompute()
    elapsed = _time.monotonic() - t0
    # Unlimited for the rest of the run (a loaded CI runner must not trip the
    # budget on a legitimate script); the original value is put back at the
    # end of main() — this test shares the developer's real config.
    prefs.SetInt("RunTimeoutS", 0)
    check("runaway script is stopped near the budget",
          2.0 <= elapsed < 30.0, f"elapsed={elapsed:.1f}s (budget 3s)")
    check("runaway script preserves last good shape",
          abs(vol(obj) - v1) < 1e-6)
    last_error = getattr(obj.Proxy, "last_error", None)
    check("timeout reported to the editor surface",
          bool(last_error) and "exceeded" in last_error[-1].get("text", ""),
          f"last_error={last_error!r}")
    with open(script, "w", encoding="utf-8") as f:
        f.write(source)  # original bracket again
    obj.touch()
    doc.recompute()
    check("fresh kernel serves the next run after a kill",
          abs(vol(obj) - v1) < 1e-6, f"volume={vol(obj):.1f}")

    # 6b. Parameter sync semantics: the script is the source of truth
    #     unless the user overrode a value in the property panel.
    #     State here: obj.length == 100.0 (user override; script default 60),
    #     obj.width == 40.0 (never touched).
    with open(script, encoding="utf-8") as f:
        current = f.read()
    edited = current.replace("width = 40.0", "width = 55.0")       # follow
    edited = edited.replace("length = 60.0", "length = 75.0")      # overridden
    edited = edited.replace(
        'PARAMS = ["length", "width", "thickness", "hole_d"]',
        'notch = 3.0\nPARAMS = ["length", "width", "notch"]',       # +notch, -thickness, -hole_d
    )
    with open(script, "w", encoding="utf-8") as f:
        f.write(edited)
    obj.touch()
    doc.recompute()
    check("un-overridden param follows script edit",
          getattr(obj, "width", None) == 55.0, f"width={getattr(obj, 'width', None)}")
    check("user-overridden param keeps user value",
          getattr(obj, "length", None) == 100.0, f"length={getattr(obj, 'length', None)}")
    check("newly declared param appears as property",
          getattr(obj, "notch", None) == 3.0)
    check("undeclared params removed from properties",
          not hasattr(obj, "thickness") and not hasattr(obj, "hole_d"))
    check("geometry reflects script-edited param",
          vol(obj) > v1, f"{v1:.1f} -> {vol(obj):.1f}")

    # 6c. Override visibility: property tooltips state the status, and
    #     overridden_params reports exactly the shadowed ones.
    from freecad.code.feature import overridden_params

    check("overridden param marked in its tooltip",
          "OVERRIDDEN" in obj.getDocumentationOfProperty("length"))
    check("following param marked in its tooltip",
          "Follows the script" in obj.getDocumentationOfProperty("width"))
    check("overridden_params reports exactly the shadowed params",
          set(overridden_params(obj)) == {"length"},
          f"reported: {sorted(overridden_params(obj))}")

    # 7. Persistence: save to .FCStd, close, reopen. The shape, the
    #    parameter values, and the proxy must all survive — and the object
    #    must still recompute through the kernel after restore. This is the
    #    "results are real document objects" claim, tested.
    fcstd = os.path.join(tmp, "e2e.FCStd")
    obj_name = obj.Name
    v_saved = vol(obj)
    doc.saveAs(fcstd)
    App.closeDocument(doc.Name)
    doc2 = App.openDocument(fcstd)
    obj2 = doc2.getObject(obj_name)
    check("object survives save/reload", obj2 is not None)
    check("shape survives save/reload",
          obj2 is not None and abs(vol(obj2) - v_saved) < 1e-6)
    check("parameter values survive save/reload",
          getattr(obj2, "length", None) == 100.0,
          f"length={getattr(obj2, 'length', None)}")
    if obj2 is not None:
        obj2.length = 80.0
        obj2.touch()
        doc2.recompute()
        check("recomputes through kernel after reload",
              0 < vol(obj2) < v_saved,
              f"volume={vol(obj2):.1f}")
        # Reset clears overrides (override state survived the reload).
        from freecad.code.feature import reset_params_to_script

        reset_params_to_script(obj2)
        check("reset returns params to script defaults",
              getattr(obj2, "length", None) == 75.0,
              f"length={getattr(obj2, 'length', None)}")
        check("reset param follows script again",
              "Follows the script" in obj2.getDocumentationOfProperty("length"))
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
    try:
        cq_volume = cq_obj.Shape.Volume
    except Exception as exc:
        # A missing shape is this check failing, not the harness breaking —
        # keep going so the remaining checks still report.
        check("cadquery script produces correct geometry", False, f"no shape: {exc}")
    else:
        check("cadquery script produces correct geometry",
              abs(cq_volume - 1000.0) < 1e-6, f"volume={cq_volume:.1f}")

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

    prefs.SetInt("RunTimeoutS", orig_timeout)
    shutil.rmtree(tmp, ignore_errors=True)


try:
    warmup()
    main()
except Exception as exc:  # infrastructure failure, not a check failure
    import traceback

    traceback.print_exc()
    _failures.append(f"unhandled exception: {exc}")

emit(f"E2E RESULT: {'FAIL' if _failures else 'PASS'}"
     + (f"  failed: {_failures}" if _failures else ""))
