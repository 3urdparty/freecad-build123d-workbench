"""Host-runtime discovery helpers for the managed kernel environment.

This module deliberately has no FreeCAD imports so discovery can be tested
outside the application. GUI apps launched from Finder receive a minimal PATH
on macOS, so relying only on :func:`shutil.which` misses normal Homebrew and
user-local installations.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterable

MIN_PYTHON = (3, 10)
VERSION_PROBE = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"


def run_checked(cmd: list[str], *, env: dict, label: str) -> subprocess.CompletedProcess:
    """Run a setup command and preserve the useful installer diagnostics."""
    try:
        return subprocess.run(
            cmd,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if len(detail) > 8000:
            detail = "…\n" + detail[-8000:]
        suffix = f"\n{detail}" if detail else ""
        raise RuntimeError(f"{label} failed (exit {exc.returncode}){suffix}") from exc


def find_uv(search_paths: Iterable[str] | None = None) -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    paths = search_paths if search_paths is not None else (
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        os.path.expanduser("~/.local/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv"),
    )
    return _first_executable(paths)


def find_compatible_python(
    *,
    env: dict | None = None,
    excluded: Iterable[str] = (),
    candidates: Iterable[str] | None = None,
) -> str | None:
    """Find a non-excluded Python new enough to host the kernel."""
    if candidates is None:
        discovered = [
            shutil.which(name)
            for name in ("python3.12", "python3.11", "python3.10", "python3")
        ]
        candidates = [
            *discovered,
            "/opt/homebrew/bin/python3.12",
            "/opt/homebrew/bin/python3.11",
            "/opt/homebrew/bin/python3.10",
            "/usr/local/bin/python3.12",
            "/usr/local/bin/python3.11",
            "/usr/local/bin/python3.10",
            "/usr/bin/python3",
        ]

    excluded_real = {os.path.realpath(path) for path in excluded if path}
    seen = set()
    for candidate in candidates:
        if not candidate or not _is_executable(candidate):
            continue
        real = os.path.realpath(candidate)
        if real in seen or real in excluded_real:
            continue
        seen.add(real)
        if python_version(candidate, env=env) >= MIN_PYTHON:
            return candidate
    return None


def python_version(python: str, env: dict | None = None) -> tuple[int, int]:
    try:
        result = subprocess.run(
            [python, "-c", VERSION_PROBE],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        major, minor = result.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0, 0


def _first_executable(paths: Iterable[str]) -> str | None:
    return next((path for path in paths if _is_executable(path)), None)


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)
