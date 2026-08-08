"""Preferences page for FreeCAD's settings dialog (Edit → Preferences).

FreeCAD's Python preference-page protocol: a class whose instances carry a
``form`` widget and implement ``loadSettings()`` / ``saveSettings()``;
registered with Gui.addPreferencePage(cls, group) at workbench init.
"""

from __future__ import annotations

import FreeCAD as App  # type: ignore[import-not-found]
from PySide import QtWidgets  # FreeCAD's PySide shim

from .preferences import PARAM_PATH


def _grp():
    return App.ParamGet(PARAM_PATH)


class CodePreferencesPage:
    def __init__(self, parent=None):
        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle("Code Workbench")
        layout = QtWidgets.QFormLayout(self.form)

        self.autosave = QtWidgets.QCheckBox("Save and re-run automatically while typing")
        layout.addRow("Autosave", self.autosave)

        self.autosave_ms = QtWidgets.QSpinBox()
        self.autosave_ms.setRange(100, 10000)
        self.autosave_ms.setSingleStep(100)
        self.autosave_ms.setSuffix(" ms")
        layout.addRow("Autosave idle delay", self.autosave_ms)

        self.debounce_ms = QtWidgets.QSpinBox()
        self.debounce_ms.setRange(50, 5000)
        self.debounce_ms.setSingleStep(50)
        self.debounce_ms.setSuffix(" ms")
        self.debounce_ms.setToolTip("Delay after an external editor saves before re-running")
        layout.addRow("File-watch debounce", self.debounce_ms)

        self.run_timeout = QtWidgets.QSpinBox()
        self.run_timeout.setRange(0, 3600)
        self.run_timeout.setSuffix(" s")
        self.run_timeout.setSpecialValueText("unlimited")
        self.run_timeout.setToolTip(
            "Hard budget per script run. A run that exceeds it is stopped by "
            "killing the kernel (it restarts on the next run). 0 = unlimited.")
        layout.addRow("Script run timeout", self.run_timeout)

        self.auto_start = QtWidgets.QCheckBox("Start the kernel when the workbench activates")
        layout.addRow("Kernel autostart", self.auto_start)

        self.env_dir = QtWidgets.QLineEdit()
        self.env_dir.setPlaceholderText("default: <user app data>/CodeWorkbench/env")
        layout.addRow("Kernel environment path", self.env_dir)

        self.pins = QtWidgets.QPlainTextEdit()
        self.pins.setMaximumHeight(90)
        self.pins.setToolTip(
            "pip requirements installed into the kernel environment, one per "
            "line. Change these, then run 'Rebuild kernel environment'.")
        layout.addRow("Kernel packages", self.pins)

    def loadSettings(self):  # noqa: N802 (FreeCAD API)
        from . import preferences

        self.autosave.setChecked(preferences.autosave_enabled())
        self.autosave_ms.setValue(preferences.autosave_ms())
        self.debounce_ms.setValue(preferences.debounce_ms())
        self.run_timeout.setValue(preferences.run_timeout_s())
        self.auto_start.setChecked(preferences.auto_start_kernel())
        self.env_dir.setText(preferences.env_dir_override())
        self.pins.setPlainText(preferences.package_pins())

    def saveSettings(self):  # noqa: N802 (FreeCAD API)
        grp = _grp()
        grp.SetBool("AutosaveEnabled", self.autosave.isChecked())
        grp.SetInt("AutosaveMs", self.autosave_ms.value())
        grp.SetInt("DebounceMs", self.debounce_ms.value())
        grp.SetInt("RunTimeoutS", self.run_timeout.value())
        grp.SetBool("AutoStartKernel", self.auto_start.isChecked())
        grp.SetString("EnvDir", self.env_dir.text().strip())
        grp.SetString("PackagePins", self.pins.toPlainText().strip())
