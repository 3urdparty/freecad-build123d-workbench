"""ScriptObject: the App::FeaturePython proxy that makes scripts parametric.

Each build123d/CadQuery script becomes a document object whose Shape is
recomputed by the kernel. Declared script parameters surface as dynamic
FreeCAD properties (group "Parameters"), so they are editable in the
property panel and drivable from expressions and spreadsheets.
"""

from __future__ import annotations

import base64

import FreeCAD as App  # type: ignore[import-not-found]
import Part  # type: ignore[import-not-found]

PARAM_GROUP = "Parameters"

# kernel param type -> FreeCAD property type
_PROP_TYPES = {
    "float": "App::PropertyFloat",
    "int": "App::PropertyInteger",
    "bool": "App::PropertyBool",
    "str": "App::PropertyString",
}


def is_script_object(obj) -> bool:
    return getattr(obj, "Proxy", None).__class__.__name__ == "ScriptObjectProxy"


def make_script_object(doc, source_path: str):
    obj = doc.addObject("Part::FeaturePython", "Script")
    ScriptObjectProxy(obj, source_path)
    if App.GuiUp:
        ScriptViewProvider(obj.ViewObject)
    doc.recompute()
    return obj


class ScriptObjectProxy:
    def __init__(self, obj, source_path: str):
        obj.Proxy = self
        obj.addProperty("App::PropertyFile", "SourceFile", "Script",
                        "Path to the build123d/CadQuery script")
        obj.addProperty("App::PropertyBool", "AutoWatch", "Script",
                        "Re-run automatically when the file is saved")
        obj.SourceFile = source_path
        obj.AutoWatch = True
        self._sync_parameters(obj)

    # -- parameters -----------------------------------------------------------

    def _sync_parameters(self, obj) -> None:
        """Mirror the script's declared parameters as FreeCAD properties."""
        from .kernel_manager import KernelManager

        try:
            declared = KernelManager.instance().introspect_params(obj.SourceFile)
        except Exception as exc:
            App.Console.PrintWarning(f"[Code] parameter introspection failed: {exc}\n")
            return
        existing = set(obj.PropertiesList)
        for p in declared:
            prop_type = _PROP_TYPES.get(p["type"])
            if prop_type is None:
                continue
            if p["name"] not in existing:
                obj.addProperty(prop_type, p["name"], PARAM_GROUP, p.get("doc", ""))
                setattr(obj, p["name"], p["default"])

    def _current_params(self, obj) -> dict:
        return {
            name: getattr(obj, name)
            for name in obj.PropertiesList
            if obj.getGroupOfProperty(name) == PARAM_GROUP
        }

    # -- recompute ------------------------------------------------------------

    def execute(self, obj) -> None:
        from .kernel_manager import KernelManager
        from .watcher import ensure_watched

        result = KernelManager.instance().run_script(
            obj.SourceFile, self._current_params(obj)
        )
        if result.get("stdout"):
            App.Console.PrintMessage(result["stdout"])
        if result.get("error"):
            frames = result["error"]
            loc = frames[-1] if frames else {}
            raise RuntimeError(
                "script failed at {file}:{line}: {text}".format(
                    file=loc.get("file", "?"), line=loc.get("line", "?"),
                    text=loc.get("text", ""))
            )

        shapes = []
        for entry in result.get("objects", []):
            sh = Part.Shape()
            sh.importBrepFromString(
                base64.b64decode(entry["brep_b64"]).decode("utf-8", errors="replace")
            )
            shapes.append(sh)
        if not shapes:
            App.Console.PrintWarning("[Code] script produced no shapes\n")
            return
        # v0: multiple shown objects become a compound. Preserving the full
        # assembly hierarchy as child objects is Phase 3 (DESIGN.md §5.4).
        obj.Shape = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)

        if getattr(obj, "AutoWatch", False):
            ensure_watched(obj)

    def onChanged(self, obj, prop: str) -> None:
        if prop == "SourceFile" and getattr(obj, "SourceFile", None):
            self._sync_parameters(obj)
        if prop == "AutoWatch":
            from .watcher import ensure_watched, unwatch

            (ensure_watched if obj.AutoWatch else unwatch)(obj)

    # FeaturePython proxies must be picklable into the .FCStd file.
    def dumps(self):
        return None

    def loads(self, state):
        return None


class ScriptViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def getIcon(self):
        import os

        from .init_gui import ICON_PATH

        return os.path.join(ICON_PATH, "code_workbench.svg")

    def attach(self, vobj):
        self._vobj = vobj

    def dumps(self):
        return None

    def loads(self, state):
        return None
