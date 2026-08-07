"""Thin wrapper around FreeCAD's parameter store for workbench settings."""

import FreeCAD as App  # type: ignore[import-not-found]

PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/CodeWorkbench"


def _grp():
    return App.ParamGet(PARAM_PATH)


def debounce_ms() -> int:
    return _grp().GetInt("DebounceMs", 200)


def autosave_enabled() -> bool:
    """Embedded editor: save + re-run automatically while typing."""
    return _grp().GetBool("AutosaveEnabled", True)


def autosave_ms() -> int:
    """Idle time before the embedded editor autosaves and re-runs."""
    return _grp().GetInt("AutosaveMs", 600)


def auto_start_kernel() -> bool:
    return _grp().GetBool("AutoStartKernel", True)


def env_dir_override() -> str:
    """Empty string means: use the default location under user app data."""
    return _grp().GetString("EnvDir", "")


def package_pins() -> str:
    """Extra pip requirements installed into the kernel env (one per line)."""
    return _grp().GetString("PackagePins", "build123d\ncadquery")
