"""ScriptObject: the App::FeaturePython proxy that makes scripts parametric.

Each build123d/CadQuery script becomes a document object whose Shape is
recomputed by the kernel. Declared script parameters surface as dynamic
FreeCAD properties (group "Parameters"), so they are editable in the
property panel and drivable from expressions and spreadsheets.
"""

from __future__ import annotations

import base64

import Part  # type: ignore[import-not-found]

import FreeCAD as App  # type: ignore[import-not-found]

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


def overridden_params(obj) -> dict:
    """{name: (current value, script default)} for every parameter the user
    has overridden in the property panel."""
    defaults = getattr(getattr(obj, "Proxy", None), "_script_defaults", {}) or {}
    return {
        name: (getattr(obj, name), default)
        for name, default in defaults.items()
        if name in obj.PropertiesList and getattr(obj, name) != default
    }


def reset_params_to_script(obj, recompute: bool = True) -> None:
    """Clear all panel overrides: parameters go back to following the script."""
    for name, (_value, default) in overridden_params(obj).items():
        setattr(obj, name, default)
    if recompute:
        obj.touch()
        obj.Document.recompute()


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
        self._script_defaults: dict = {}
        obj.addProperty("App::PropertyFile", "SourceFile", "Script",
                        "Path to the build123d/CadQuery script")
        obj.addProperty("App::PropertyBool", "AutoWatch", "Script",
                        "Re-run automatically when the file is saved")
        obj.SourceFile = source_path
        obj.AutoWatch = True
        self._sync_parameters(obj)

    # -- parameters -----------------------------------------------------------
    #
    # Semantics: the SCRIPT is the source of truth unless the user has
    # overridden a value in the property panel. Re-synced on every execute:
    #   - params newly listed in PARAMS  -> property appears
    #   - params removed from PARAMS     -> property disappears
    #   - script default changed         -> property follows the script,
    #     UNLESS its current value differs from the script's previous
    #     default (i.e. the user set it by hand) — then the user wins.
    # The previous defaults are persisted with the document (dumps/loads).

    def _sync_parameters(self, obj):
        """Add/update parameter properties from the script. Returns the set
        of declared names (for the post-run removal pass) or None if the
        script couldn't be parsed. Deliberately performs NO removals: a
        transiently broken or mid-edit script must never destroy the user's
        parameter overrides — removals commit only after a successful run
        (see execute)."""
        from .kernel_manager import KernelManager

        try:
            declared = KernelManager.instance().introspect_params(obj.SourceFile)
        except Exception as exc:
            App.Console.PrintWarning(f"[Code] parameter introspection failed: {exc}\n")
            return None
        old_defaults = getattr(self, "_script_defaults", {}) or {}
        existing = {
            name for name in obj.PropertiesList
            if obj.getGroupOfProperty(name) == PARAM_GROUP
        }
        declared_names = set()
        for p in declared:
            prop_type = _PROP_TYPES.get(p["type"])
            if prop_type is None:
                continue
            name, default = p["name"], p["default"]
            declared_names.add(name)
            if name not in existing:
                obj.addProperty(prop_type, name, PARAM_GROUP, p.get("doc", ""))
                setattr(obj, name, default)
            else:
                previous_default = old_defaults.get(name, default)
                user_overrode = getattr(obj, name) != previous_default
                if not user_overrode and getattr(obj, name) != default:
                    setattr(obj, name, default)  # follow the script
        self._script_defaults = {
            p["name"]: p["default"] for p in declared
            if _PROP_TYPES.get(p["type"]) is not None
        }
        self._update_override_markers(obj)
        return declared_names

    def _update_override_markers(self, obj) -> None:
        """Property tooltips state the override status, so hovering any
        parameter in the panel explains why (or whether) script edits to
        its default are being ignored."""
        for name, default in (getattr(self, "_script_defaults", {}) or {}).items():
            if name not in obj.PropertiesList:
                continue
            if getattr(obj, name) != default:
                doc = (f"OVERRIDDEN in the panel — the script's default "
                       f"({default!r}) is ignored. Set it back to {default!r} "
                       f"(or use 'Reset parameters to script') to follow the "
                       f"script again.")
            else:
                doc = f"Follows the script (default {default!r})."
            try:
                obj.setDocumentationOfProperty(name, doc)
            except Exception:
                pass  # older FreeCAD without the API — markers are advisory

    def _remove_undeclared(self, obj, declared_names) -> None:
        for name in list(obj.PropertiesList):
            if (obj.getGroupOfProperty(name) == PARAM_GROUP
                    and name not in declared_names):
                obj.removeProperty(name)

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

        # Script edits may have added/removed/changed parameters — sync
        # (additions/updates only) before running so this recompute already
        # reflects them; removals commit after the run succeeds.
        declared = self._sync_parameters(obj)

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

        # The run succeeded — now it's safe to drop params the script no
        # longer declares.
        if declared is not None:
            self._remove_undeclared(obj, declared)

        if getattr(obj, "AutoWatch", False):
            ensure_watched(obj)

    def onChanged(self, obj, prop: str) -> None:
        if prop == "SourceFile" and getattr(obj, "SourceFile", None):
            self._sync_parameters(obj)
        elif prop == "AutoWatch":
            from .watcher import ensure_watched, unwatch

            (ensure_watched if obj.AutoWatch else unwatch)(obj)
        elif obj.getGroupOfProperty(prop) == PARAM_GROUP:
            # Panel edit: refresh the override tooltips immediately.
            self._update_override_markers(obj)

    # FeaturePython proxies must be picklable into the .FCStd file. The
    # known script defaults ride along so "user overrode this parameter"
    # survives save/reload.
    def dumps(self):
        return {"script_defaults": getattr(self, "_script_defaults", {})}

    def loads(self, state):
        self._script_defaults = (state or {}).get("script_defaults", {})


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
