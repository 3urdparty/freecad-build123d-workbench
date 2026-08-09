"""Workbench registration (new-style FreeCAD addon).

FreeCAD >= 0.19 auto-discovers ``freecad/<pkg>/init_gui.py`` for addons using
the ``freecad`` namespace-package layout, so no top-level InitGui.py is needed.
"""

import os

import FreeCADGui as Gui  # type: ignore[import-not-found]

ICON_PATH = os.path.join(os.path.dirname(__file__), "resources", "icons")


class CodeWorkbench(Gui.Workbench):
    """Work with build123d and CadQuery natively inside FreeCAD."""

    MenuText = "Code"
    ToolTip = "build123d / CadQuery scripting with an isolated kernel"
    Icon = os.path.join(ICON_PATH, "code_workbench.svg")

    def Initialize(self):
        # Imports deferred: FreeCAD calls Initialize() on first activation.
        from . import commands

        self._command_names = commands.register_all()
        self.appendToolbar("Code", self._command_names)
        self.appendMenu("&Code", self._command_names)
        try:
            from .prefs_page import CodePreferencesPage

            Gui.addPreferencePage(CodePreferencesPage, "Code Workbench")
        except Exception as exc:  # preferences are a convenience, not load-bearing
            import FreeCAD as App

            App.Console.PrintWarning(f"[Code] preferences page unavailable: {exc}\n")

    def Activated(self):
        from . import preferences
        from .kernel_manager import KernelManager

        # Respect the user's preference. ensure_started() asks before any
        # first-run download, then provisions in the background if accepted.
        if preferences.auto_start_kernel():
            KernelManager.instance().ensure_started(background=True)

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(CodeWorkbench())
