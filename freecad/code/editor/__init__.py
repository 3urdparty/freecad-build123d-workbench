"""Embedded script editor for the Code workbench.

Pure-PySide (works on every FreeCAD build — the official bundles ship no
QtWebEngine, and QScintilla/Spyder are PyQt-only). Code intelligence comes
from the kernel over the existing JSON-RPC: jedi runs in the kernel venv,
where build123d/cadquery actually live, and completes against the live
namespace of the last execution. See DESIGN.md §6.
"""
