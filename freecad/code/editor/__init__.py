"""Embedded script editor for the Code workbench.

Pure-PySide (works on every FreeCAD build — the official bundles ship no
QtWebEngine, and QScintilla/Spyder are PyQt-only). Code intelligence comes
from the kernel over the existing JSON-RPC: jedi runs in the kernel venv,
where build123d/cadquery actually live, and completes against the live
namespace of the last execution. See DESIGN.md §6.
"""


def create_editor(parent=None):
    """Create the configured editor frontend.

    The dock imports this factory rather than the native widget class. A future
    capability-gated CodeMirror/Monaco frontend therefore has one selection
    point and only needs to preserve the small signal/method contract currently
    implemented by :class:`CodeEditor`.
    """
    from .code_editor import CodeEditor

    return CodeEditor(parent)
