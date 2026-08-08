"""GUI commands for the Code workbench toolbar/menu."""

import os

import FreeCAD as App  # type: ignore[import-not-found]
import FreeCADGui as Gui  # type: ignore[import-not-found]

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
    icon = "code_new_script.svg"

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
    icon = "code_open_script.svg"

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
    icon = "code_rerun.svg"

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
    icon = "code_toggle_watch.svg"

    def Activated(self):
        from .feature import is_script_object

        for obj in Gui.Selection.getSelection():
            if is_script_object(obj):
                obj.AutoWatch = not obj.AutoWatch


class EditScriptCommand(_BaseCommand):
    name = "Code_EditScript"
    text = "Edit script"
    tooltip = "Open the selected script object in the embedded editor"
    icon = "code_edit_script.svg"

    def Activated(self):
        from .editor.panel import open_editor
        from .feature import is_script_object

        for obj in Gui.Selection.getSelection():
            if is_script_object(obj):
                open_editor(obj)


class KernelRestartCommand(_BaseCommand):
    name = "Code_KernelRestart"
    text = "Restart kernel"
    tooltip = "Restart the build123d/CadQuery kernel process"
    icon = "code_kernel_restart.svg"

    def Activated(self):
        from .kernel_manager import KernelManager

        KernelManager.instance().restart()


class ResetParamsCommand(_BaseCommand):
    name = "Code_ResetParams"
    text = "Reset parameters to script"
    tooltip = ("Clear panel overrides on the selected script object(s) — "
               "parameters go back to following the script's defaults")
    icon = "code_reset_params.svg"

    def Activated(self):
        from .feature import is_script_object, reset_params_to_script

        for obj in Gui.Selection.getSelection():
            if is_script_object(obj):
                reset_params_to_script(obj)


class RebuildEnvCommand(_BaseCommand):
    name = "Code_RebuildEnv"
    text = "Rebuild kernel environment"
    tooltip = ("Delete and re-provision the kernel's Python environment "
               "(build123d/cadquery/jedi) — use after updating the addon")
    icon = "code_rebuild_env.svg"

    def Activated(self):
        from PySide import QtWidgets

        from .kernel_manager import KernelManager

        mgr = KernelManager.instance()
        answer = QtWidgets.QMessageBox.question(
            None, "Rebuild kernel environment",
            f"Delete and re-download the kernel environment at:\n"
            f"{mgr.env_dir()}\n\nThis takes a few minutes. Continue?",
        )
        if answer == QtWidgets.QMessageBox.Yes:
            mgr.rebuild_env()
            App.Console.PrintMessage(
                "[Code] rebuilding kernel environment — watch this Report "
                "view for progress\n")


ALL_COMMANDS = [
    NewScriptCommand,
    OpenScriptCommand,
    EditScriptCommand,
    RerunCommand,
    ResetParamsCommand,
    ToggleWatchCommand,
    KernelRestartCommand,
    RebuildEnvCommand,
]


def register_all() -> list:
    names = []
    for cls in ALL_COMMANDS:
        Gui.addCommand(cls.name, cls())
        names.append(cls.name)
    return names
