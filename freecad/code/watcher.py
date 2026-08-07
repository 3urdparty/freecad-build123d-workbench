"""Hot reload: re-run script objects when their source file is saved.

Editor-agnostic by design — save in VS Code, Neovim, anything, and the
FreeCAD document recomputes. Debounced because editors often write files
multiple times per save (atomic-rename dances, formatters, etc.).

Subscribers are tracked as (document name, object name) pairs and resolved
at fire time: FreeCAD's FeaturePython objects do not support weak
references, and holding strong references would keep deleted objects alive.
"""

from __future__ import annotations

import os
import time

import FreeCAD as App  # type: ignore[import-not-found]

try:  # FreeCAD's PySide shim — absent or unusable under FreeCADCmd (headless)
    from PySide import QtCore
except ImportError:  # pragma: no cover - headless builds
    QtCore = None

from . import preferences


def _gui_available() -> bool:
    """Hot reload needs a Qt event loop; it's a no-op headless."""
    return QtCore is not None and App.GuiUp


_watcher: QtCore.QFileSystemWatcher | None = None
_timers: dict[str, QtCore.QTimer] = {}
# path -> {(document name, object name)}
_subscribers: dict[str, set] = {}
# path -> monotonic time of the last write made by the embedded editor
_self_writes: dict[str, float] = {}


def mark_self_write(path: str) -> None:
    """The embedded editor saves and recomputes explicitly; the watcher
    event caused by that same write must be swallowed, or every editor
    save executes the script twice."""
    _self_writes[path] = time.monotonic()


def _get_watcher() -> QtCore.QFileSystemWatcher:
    global _watcher
    if _watcher is None:
        _watcher = QtCore.QFileSystemWatcher()
        _watcher.fileChanged.connect(_on_file_changed)
    return _watcher


def _key(obj) -> tuple:
    return (obj.Document.Name, obj.Name)


def ensure_watched(obj) -> None:
    if not _gui_available():
        return
    path = getattr(obj, "SourceFile", None)
    if not path:
        return
    w = _get_watcher()
    if path not in w.files() and os.path.exists(path):
        w.addPath(path)
    _subscribers.setdefault(path, set()).add(_key(obj))


def unwatch(obj) -> None:
    if not _gui_available():
        return
    path = getattr(obj, "SourceFile", None)
    if not path:
        return
    subs = _subscribers.get(path)
    if subs is not None:
        subs.discard(_key(obj))
        if not subs and _watcher is not None and path in _watcher.files():
            _watcher.removePath(path)


def _on_file_changed(path: str) -> None:
    if time.monotonic() - _self_writes.pop(path, -1e9) < 1.0:
        # Editor-initiated write: recompute is handled by the panel.
        # Still re-add the watch in case the save dropped it.
        w = _get_watcher()
        if os.path.exists(path) and path not in w.files():
            w.addPath(path)
        return
    # Debounce: editors fire several change events per save.
    timer = _timers.get(path)
    if timer is None:
        timer = QtCore.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda p=path: _fire(p))
        _timers[path] = timer
    timer.start(preferences.debounce_ms())
    # Atomic-rename saves drop the watch; re-add it.
    w = _get_watcher()
    if os.path.exists(path) and path not in w.files():
        w.addPath(path)


def _fire(path: str) -> None:
    docs = set()
    stale = []
    for doc_name, obj_name in list(_subscribers.get(path, ())):
        doc = App.listDocuments().get(doc_name)
        obj = doc.getObject(obj_name) if doc is not None else None
        if obj is None:
            stale.append((doc_name, obj_name))
            continue
        obj.touch()
        docs.add(doc)
    subs = _subscribers.get(path)
    if subs is not None:
        for key in stale:
            subs.discard(key)
    for doc in docs:
        doc.recompute()
    if docs:
        App.Console.PrintMessage(f"[Code] reloaded {path}\n")
