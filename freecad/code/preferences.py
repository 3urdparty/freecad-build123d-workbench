"""Thin wrapper around FreeCAD's parameter store for workbench settings."""

import FreeCAD as App  # type: ignore[import-not-found]

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/CodeWorkbench"


def _grp():
    return App.ParamGet(PARAM_PATH)


def debounce_ms() -> int:
    return _grp().GetInt("DebounceMs", 200)


def run_timeout_s() -> int:
    """Hard budget for a single script run, seconds. 0 disables the bound.
    On timeout the kernel process is killed and restarts on the next run —
    exec() cannot be interrupted from outside."""
    return _grp().GetInt("RunTimeoutS", 60)


def auto_start_bridge() -> bool:
    return _grp().GetBool("AutoStartBridge", True)

