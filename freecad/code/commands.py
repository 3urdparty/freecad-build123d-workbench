"""GUI commands for the Code workbench toolbar/menu."""

import os

import FreeCADGui as Gui  # type: ignore[import-not-found]

import FreeCAD as App  # type: ignore[import-not-found]

from .init_gui import ICON_PATH

NEW_SCRIPT_TEMPLATE = '''\
"""New Code Workbench script.

Anything passed to show() appears in FreeCAD. If nothing is shown, top-level
build123d / CadQuery objects are auto-discovered.

Module-level literals listed in PARAMS become editable FreeCAD properties.
"""

from build123d import *  # noqa: F403

# --- parameters (surface in FreeCAD's property panel) ---
length = 40.0
width = 30.0
thickness = 5.0
PARAMS = ["length", "width", "thickness"]

# --- model ---
with BuildPart() as part:
    Box(length, width, thickness)

show(part)  # noqa: F405
'''


def _active_doc():
    return App.ActiveDocument or App.newDocument("Unnamed")


class _BaseCommand:
    name = "Code_Base"
    text = "Base"
    tooltip = ""
    icon = "code_workbench.svg"

    def GetResources(self):
        return {
            "Pixmap": os.path.join(ICON_PATH, self.icon),
            "MenuText": self.text,
            "ToolTip": self.tooltip or self.text,
        }

    def IsActive(self):
        return True


class NewScriptCommand(_BaseCommand):
    name = "Code_NewScript"
    text = "New script"
    tooltip = "Create a new build123d/CadQuery script and add it to the document"

    def Activated(self):
        from PySide import QtWidgets  # FreeCAD's PySide shim

        from .feature import make_script_object

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            None, "New script", "model.py", "Python scripts (*.py)"
        )
        if not path:
            return
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(NEW_SCRIPT_TEMPLATE)
        make_script_object(_active_doc(), path)


class OpenScriptCommand(_BaseCommand):
    name = "Code_OpenScript"
    text = "Open script"
    tooltip = "Add an existing script to the document as a parametric object"

    def Activated(self):
        from PySide import QtWidgets

        from .feature import make_script_object

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Open script", "", "Python scripts (*.py)"
        )
        if path:
            make_script_object(_active_doc(), path)


class RerunCommand(_BaseCommand):
    name = "Code_Rerun"
    text = "Re-run"
    tooltip = "Re-execute the selected script object(s)"

    def Activated(self):
        from .feature import is_script_object

        for obj in Gui.Selection.getSelection():
            if is_script_object(obj):
                obj.touch()
        if App.ActiveDocument:
            App.ActiveDocument.recompute()


class ToggleWatchCommand(_BaseCommand):
    name = "Code_ToggleWatch"
    text = "Toggle watch"
    tooltip = "Toggle hot reload (re-run on file save) for the selected script object(s)"

    def Activated(self):
        from .feature import is_script_object

        for obj in Gui.Selection.getSelection():
            if is_script_object(obj):
                obj.AutoWatch = not obj.AutoWatch


class KernelRestartCommand(_BaseCommand):
    name = "Code_KernelRestart"
    text = "Restart kernel"
    tooltip = "Restart the build123d/CadQuery kernel process"

    def Activated(self):
        from .kernel_manager import KernelManager

        KernelManager.instance().restart()


ALL_COMMANDS = [
    NewScriptCommand,
    OpenScriptCommand,
    RerunCommand,
    ToggleWatchCommand,
    KernelRestartCommand,
]


def register_all() -> list:
    names = []
    for cls in ALL_COMMANDS:
        Gui.addCommand(cls.name, cls())
        names.append(cls.name)
    return names
