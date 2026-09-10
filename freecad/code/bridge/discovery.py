"""Publish FreeCAD bridge connection information for caddev."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def discovery_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path(
            os.environ.get(
                "XDG_CACHE_HOME",
                Path.home() / ".cache",
            )
        )

    return base / "codecad" / "bridge.json"


def write_discovery(
    *,
    host: str,
    port: int,
    token: str,
    protocol: int,
) -> None:
    path = discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "host": host,
        "port": port,
        "token": token,
        "protocol": protocol,
    }

    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )

    os.replace(temp, path)


def remove_discovery() -> None:
    path = discovery_path()

    try:
        path.unlink()
    except FileNotFoundError:
        pass
