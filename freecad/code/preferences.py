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
    """pip requirements installed into the kernel env (one per line).

    Pinned by default: an unpinned `build123d` means a PyPI release can
    break every user's kernel overnight. Users can loosen or bump these in
    the preferences page, then run 'Rebuild kernel environment'. Keep the
    defaults in sync with the versions CI tests against.
    """
    return _grp().GetString("PackagePins", "build123d==0.11.1\ncadquery==2.8.0")
