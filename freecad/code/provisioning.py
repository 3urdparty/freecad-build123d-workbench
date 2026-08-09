"""Host-runtime discovery helpers for the managed kernel environment.

This module deliberately has no FreeCAD imports so discovery can be tested
outside the application. GUI apps launched from Finder receive a minimal PATH
on macOS, so relying only on :func:`shutil.which` misses normal Homebrew and
user-local installations.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterable

MIN_PYTHON = (3, 10)
VERSION_PROBE = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"

# A small, pinned uv binary gives Code Workbench a standalone Python without
# requiring users to install Python, Git, a compiler, or shell tooling first.
# macOS also ships /usr/bin/python3 as a developer-tool launcher, so probing it
# on a clean machine would open Apple's large Command Line Tools installer.
UV_VERSION = "0.12.3"
UV_RELEASE_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
UV_ASSETS = {
    ("darwin", "arm64"): (
        "uv-aarch64-apple-darwin.tar.gz",
        "546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843",
    ),
    ("darwin", "x86_64"): (
        "uv-x86_64-apple-darwin.tar.gz",
        "4c9f52262a14da336e4a42ed24992d12d0c956acde87619e4611d321dffa602b",
    ),
    ("win32", "arm64"): (
        "uv-aarch64-pc-windows-msvc.zip",
        "4343217d668727b8a8eb5cad92389a1d2eeead93c89940d1b955ba1bb15462eb",
    ),
    ("win32", "x86"): (
        "uv-i686-pc-windows-msvc.zip",
        "8b1e428b1f5acfd5ba6bf490a7205b4f9149a27a0d9cbd1487243fd8e3d8a80b",
    ),
    ("win32", "x86_64"): (
        "uv-x86_64-pc-windows-msvc.zip",
        "b23350c79e8ad0192b8124af13a0f17e8d4e4549524785e1aef389ae5a06990e",
    ),
}


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
        os.path.expanduser("~/.local/bin/uv.exe"),
        os.path.expanduser("~/.cargo/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv.exe"),
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
            for name in ("python3.12", "python3.11", "python3.10", "python3", "python")
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
        if sys.platform == "darwin" and "/usr/bin/python3" in (
            os.path.abspath(candidate),
            real,
        ):
            continue
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


def _normalise_machine(machine: str) -> str:
    aliases = {
        "aarch64": "arm64",
        "amd64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
        "x86_64": "x86_64",
    }
    return aliases.get(machine.lower(), machine.lower())


def bootstrapped_uv_path(install_dir: str, *, platform_name: str | None = None) -> str:
    platform_name = platform_name or sys.platform
    return os.path.join(install_dir, "uv.exe" if platform_name == "win32" else "uv")


def bootstrap_uv(
    install_dir: str,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> str:
    """Download and verify Code Workbench's private uv executable."""
    platform_name = platform_name or sys.platform
    machine = _normalise_machine(machine or platform.machine())
    asset_info = UV_ASSETS.get((platform_name, machine))
    platform_label = "Windows" if platform_name == "win32" else "Mac"
    if asset_info is None:
        raise RuntimeError(
            f"automatic uv setup does not support this {platform_label} "
            f"architecture ({machine})"
        )

    target = bootstrapped_uv_path(install_dir, platform_name=platform_name)
    if _is_executable(target):
        return target

    asset, expected_sha256 = asset_info
    url = f"{UV_RELEASE_BASE}/{asset}"
    os.makedirs(install_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix=".uv-download-", dir=install_dir)
    archive_path = os.path.join(temp_dir, asset)
    staged_path = os.path.join(temp_dir, os.path.basename(target))
    try:
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "freecad-code-workbench uv bootstrap"}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                with open(archive_path, "wb") as archive_file:
                    shutil.copyfileobj(response, archive_file)
        except OSError as exc:
            raise RuntimeError(f"downloading uv {UV_VERSION} failed: {exc}") from exc

        digest = hashlib.sha256()
        with open(archive_path, "rb") as archive_file:
            for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError(
                f"downloaded uv {UV_VERSION} failed its SHA-256 integrity check"
            )

        executable = "uv.exe" if platform_name == "win32" else "uv"
        try:
            if asset.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as archive:
                    with archive.open(executable) as source, open(staged_path, "wb") as output:
                        shutil.copyfileobj(source, output)
            else:
                archive_stem = asset.removesuffix(".tar.gz")
                member_name = f"{archive_stem}/{executable}"
                with tarfile.open(archive_path, "r:gz") as archive:
                    member = archive.getmember(member_name)
                    if not member.isfile():
                        raise RuntimeError("the uv release archive has an invalid layout")
                    source = archive.extractfile(member)
                    if source is None:
                        raise RuntimeError("the uv release archive has an invalid layout")
                    with source, open(staged_path, "wb") as output:
                        shutil.copyfileobj(source, output)
        except (KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
            raise RuntimeError("could not unpack the uv release archive") from exc

        os.chmod(
            staged_path,
            stat.S_IRUSR
            | stat.S_IWUSR
            | stat.S_IXUSR
            | stat.S_IRGRP
            | stat.S_IXGRP
            | stat.S_IROTH
            | stat.S_IXOTH,
        )
        os.replace(staged_path, target)
        return target
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def bootstrap_uv_macos(install_dir: str) -> str:
    """Install a pinned uv binary without invoking macOS developer tools."""
    return bootstrap_uv(install_dir, platform_name="darwin")


def _first_executable(paths: Iterable[str]) -> str | None:
    return next((path for path in paths if _is_executable(path)), None)


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)
