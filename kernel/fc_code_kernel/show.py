"""show() / show_object() shim, API-compatible with ocp_vscode.

Scripts written for ocp-vscode / cq-editor call show(...) or
show_object(...); we collect whatever they pass for serialization. If a
script shows nothing, the executor falls back to auto-discovering top-level
CAD objects (same heuristic family as cq-editor).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ShownObject:
    obj: Any
    name: str | None = None
    options: dict = field(default_factory=dict)


class ShowCollector:
    """Per-execution collector, installed into the script's namespace."""

    def __init__(self) -> None:
        self.shown: list[ShownObject] = []

    # ocp_vscode-compatible signatures (extra kwargs tolerated + kept as options)
    def show(self, *objs: Any, names: list | None = None, **options: Any) -> None:
        names = names or []
        for i, obj in enumerate(objs):
            name = names[i] if i < len(names) else None
            self.shown.append(ShownObject(obj, name, dict(options)))

    def show_object(self, obj: Any, name: str | None = None,
                    options: dict | None = None, **kw: Any) -> None:
        merged = dict(options or {})
        merged.update(kw)
        self.shown.append(ShownObject(obj, name, merged))


# The active collector for the currently executing script (kernel.run is
# serialized per connection in protocol v0, but keep this thread-local so a
# future concurrent server doesn't cross-wire shows).
_local = threading.local()


def set_collector(collector: ShowCollector | None) -> None:
    _local.collector = collector


def get_collector() -> ShowCollector | None:
    return getattr(_local, "collector", None)
