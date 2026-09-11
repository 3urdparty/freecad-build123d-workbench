from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Event
from typing import Any, Callable

from PySide import QtCore

from .components import (
    delete_component,
    document_info,
    highlight_subelements,
    recompute_document,
    update_component,
)


Handler = Callable[[dict[str, Any]], Any]


METHODS: dict[str, Handler] = {
    "document.info": document_info,
    "document.recompute": recompute_document,
    "component.update": update_component,
    "component.delete": delete_component,
    "debug.highlight": highlight_subelements,
}


@dataclass
class Command:
    method: str
    params: dict[str, Any]

    done: Event = field(default_factory=Event)

    result: Any = None
    error: Exception | None = None


_QUEUE: Queue[Command] = Queue()


def submit(
    method: str,
    params: dict[str, Any],
    timeout: float = 30.0,
):
    """
    Called from an RPC worker thread.

    Queue the command for execution on FreeCAD's Qt thread
    and wait for its result.
    """

    command = Command(
        method=method,
        params=params,
    )

    _QUEUE.put(command)

    if not command.done.wait(timeout):
        raise TimeoutError(
            f"FreeCAD did not process {method!r} within {timeout}s"
        )

    if command.error is not None:
        raise command.error

    return command.result


class Dispatcher(QtCore.QObject):
    """
    Polls the RPC command queue from FreeCAD's Qt event loop.
    """

    def __init__(self):
        super().__init__()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._process)
        self._timer.start(10)

    def _process(self):
        # Bound each tick so RPC traffic can't monopolize the UI.
        for _ in range(32):
            try:
                command = _QUEUE.get_nowait()
            except Empty:
                break

            try:
                handler = METHODS.get(command.method)

                if handler is None:
                    raise KeyError(
                        f"Unknown RPC method: {command.method}"
                    )

                command.result = handler(command.params)

            except Exception as exc:
                command.error = exc

            finally:
                command.done.set()


_dispatcher: Dispatcher | None = None


def start_dispatcher() -> Dispatcher:
    global _dispatcher

    if _dispatcher is None:
        _dispatcher = Dispatcher()

    return _dispatcher


def stop_dispatcher() -> None:
    global _dispatcher

    if _dispatcher is not None:
        _dispatcher._timer.stop()
        _dispatcher.deleteLater()
        _dispatcher = None
