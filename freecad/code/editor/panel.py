"""The editor dock: one per ScriptObject.

Wiring philosophy: the editor never talks to the kernel or the document
directly. With autosave on (the default), the loop is: type → idle debounce
→ save → recompute — and because a failed script preserves the object's
last good shape, the 3D view always shows the last *valid* model while the
error is shown here. Editor-initiated writes are marked so the file watcher
doesn't recompute a second time; completions are fetched on a worker thread
so a cold kernel never freezes the GUI.
"""

from __future__ import annotations

import threading
import time

import FreeCADGui as Gui  # type: ignore[import-not-found]
from PySide import QtCore, QtWidgets  # FreeCAD's PySide shim

import FreeCAD as App  # type: ignore[import-not-found]

from .. import preferences
from .code_editor import CodeEditor

_open_docks: dict[tuple, ScriptEditorDock] = {}


def open_editor(obj) -> ScriptEditorDock:
    """Open (or focus) the editor dock for a ScriptObject. Docks delete
    their C++ side on close (WA_DeleteOnClose), so a stale registry entry
    raises RuntimeError on any access — treat that as 'gone'."""
    key = (obj.Document.Name, obj.Name)
    dock = _open_docks.get(key)
    if dock is not None:
        try:
            dock.show()
            dock.raise_()
            return dock
        except RuntimeError:  # C++ object already deleted
            _open_docks.pop(key, None)
    dock = ScriptEditorDock(obj)
    _open_docks[key] = dock
    dock.destroyed.connect(lambda *_, k=key: _open_docks.pop(k, None))
    Gui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    dock.show()
    dock.raise_()
    return dock


class _CompletionBridge(QtCore.QObject):
    """Thread-safe hop: worker thread emits, GUI thread shows the popup."""

    arrived = QtCore.Signal(list)


class ScriptEditorDock(QtWidgets.QDockWidget):
    def __init__(self, obj):
        super().__init__(f"Code — {obj.Label}", Gui.getMainWindow())
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self._doc_name = obj.Document.Name
        self._obj_name = obj.Name
        self._obj_label = obj.Label
        self._path = obj.SourceFile
        self._loading = False
        self._last_saved_text: str | None = None

        body = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QtWidgets.QToolBar(body)
        self._run_action = toolbar.addAction("Run", self._run)
        self._run_action.setToolTip("Save and re-run now (Ctrl+Enter)")
        self._save_action = toolbar.addAction("Save", self._save_only)
        self._save_action.setToolTip("Save without running (Ctrl+S runs too "
                                     "when autosave is on)")
        self._auto_action = toolbar.addAction("Autosave")
        self._auto_action.setCheckable(True)
        self._auto_action.setChecked(preferences.autosave_enabled())
        self._auto_action.setToolTip(
            "Save and re-run automatically while typing — the 3D view "
            "always shows the last valid model")
        toolbar.addSeparator()
        self._status = QtWidgets.QLabel("")
        self._status.setContentsMargins(8, 0, 8, 0)
        toolbar.addWidget(self._status)
        layout.addWidget(toolbar)

        # Override banner: visible only while panel values shadow the script.
        self._override_bar = QtWidgets.QWidget(body)
        bar_layout = QtWidgets.QHBoxLayout(self._override_bar)
        bar_layout.setContentsMargins(8, 2, 8, 2)
        self._override_label = QtWidgets.QLabel("")
        self._override_label.setStyleSheet("color: #d7a04c;")
        bar_layout.addWidget(self._override_label, 1)
        reset_btn = QtWidgets.QPushButton("Reset to script")
        reset_btn.clicked.connect(self._reset_params)
        bar_layout.addWidget(reset_btn)
        self._override_bar.setVisible(False)
        layout.addWidget(self._override_bar)

        self.editor = CodeEditor(body)
        layout.addWidget(self.editor)
        self.setWidget(body)

        self._bridge = _CompletionBridge()
        self._bridge.arrived.connect(self.editor.show_completions)
        self._completion_seq = 0

        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._autosave)

        self.editor.run_requested.connect(self._run)
        self.editor.save_requested.connect(self._run)  # Ctrl+S: save + run
        self.editor.completions_wanted.connect(self._fetch_completions)
        self.editor.textChanged.connect(self._on_text_changed)

        self._load()

    # -- object / file plumbing -------------------------------------------------

    def _object(self):
        doc = App.listDocuments().get(self._doc_name)
        return doc.getObject(self._obj_name) if doc else None

    def _load(self) -> None:
        self._loading = True
        try:
            with open(self._path, encoding="utf-8") as f:
                text = f.read()
            self.editor.setPlainText(text)
            self._last_saved_text = text
        except OSError as exc:
            self._set_status(f"cannot open: {exc}", error=True)
        finally:
            self._loading = False
        self._mark_dirty(False)

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._mark_dirty(True)
        if self._auto_action.isChecked():
            self._autosave_timer.start(preferences.autosave_ms())
        else:
            self._set_status("unsaved changes", warn=True)

    def _mark_dirty(self, dirty: bool) -> None:
        star = " •" if dirty else ""
        self.setWindowTitle(f"Code — {self._obj_label}{star}")

    def _save(self) -> bool:
        """Write the buffer to disk. Returns False if nothing changed."""
        text = self.editor.toPlainText()
        if text == self._last_saved_text:
            self._mark_dirty(False)
            return False
        from ..watcher import mark_self_write

        mark_self_write(self._path)
        with open(self._path, "w", encoding="utf-8") as f:
            f.write(text)
        self._last_saved_text = text
        self._mark_dirty(False)
        return True

    def _save_only(self) -> None:
        self._save()
        self._set_status(f"saved · {time.strftime('%H:%M:%S')}")

    def _autosave(self) -> None:
        if not self._save():
            return
        self._recompute(focus_error=False)

    def _run(self) -> None:
        self._autosave_timer.stop()
        self._save()
        self._recompute(focus_error=True)

    def _recompute(self, focus_error: bool) -> None:
        obj = self._object()
        if obj is None:
            self._set_status("object was deleted", error=True)
            return
        obj.touch()
        obj.Document.recompute()
        self._report_result(obj, focus_error=focus_error)

    def _report_result(self, obj, focus_error: bool = False) -> None:
        stamp = time.strftime("%H:%M:%S")
        error = getattr(obj.Proxy, "last_error", None)
        if error:
            loc = error[-1]
            line = loc.get("line") if loc.get("file") == self._path else None
            self.editor.set_error_line(line, focus=focus_error)
            self._set_status(
                f"error · {stamp} — showing last valid model — "
                f"{loc.get('text', '')[:100]}", error=True)
        else:
            self.editor.set_error_line(None)
            self._set_status(f"ok · {stamp}")
        self._update_override_banner(obj)

    def _update_override_banner(self, obj) -> None:
        from ..feature import overridden_params

        overrides = overridden_params(obj)
        if overrides:
            parts = [f"{name}={value!r} (script: {default!r})"
                     for name, (value, default) in sorted(overrides.items())]
            self._override_label.setText(
                "Panel overrides active — script defaults ignored for: "
                + ", ".join(parts))
        self._override_bar.setVisible(bool(overrides))

    def _reset_params(self) -> None:
        from ..feature import reset_params_to_script

        obj = self._object()
        if obj is None:
            return
        reset_params_to_script(obj)
        self._report_result(obj)

    def _set_status(self, text: str, error: bool = False, warn: bool = False) -> None:
        self._status.setText(text)
        color = "#d16969" if error else ("#d7a04c" if warn else "")
        self._status.setStyleSheet(f"color: {color};" if color else "")

    # -- completions --------------------------------------------------------------

    def _fetch_completions(self, source: str, line: int, column: int) -> None:
        from ..kernel_manager import KernelManager

        self._completion_seq += 1
        seq = self._completion_seq

        def work():
            try:
                items = KernelManager.instance().complete(source, line, column,
                                                          self._path)
            except Exception:
                items = []
            if seq == self._completion_seq:
                try:
                    self._bridge.arrived.emit(items)
                except RuntimeError:
                    pass  # dock was closed while we were thinking
        threading.Thread(target=work, daemon=True).start()
