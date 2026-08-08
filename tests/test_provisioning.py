"""Managed-kernel host discovery and installer error tests."""

import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from freecad.code import provisioning  # noqa: E402


def _fake_executable(path, output: str = ""):
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
    path.chmod(0o755)
    return str(path)


def test_find_uv_checks_gui_safe_locations(tmp_path, monkeypatch):
    uv = _fake_executable(tmp_path / "uv")
    monkeypatch.setattr(provisioning.shutil, "which", lambda _name: None)

    assert provisioning.find_uv([str(tmp_path / "missing"), uv]) == uv


def test_find_compatible_python_rejects_old_version(tmp_path):
    old = _fake_executable(tmp_path / "python-old", "3.9")
    current = _fake_executable(tmp_path / "python-current", "3.12")

    assert provisioning.find_compatible_python(candidates=[old, current]) == current


def test_find_compatible_python_honors_exclusion(tmp_path):
    compatible = _fake_executable(tmp_path / "python", "3.12")

    assert provisioning.find_compatible_python(
        candidates=[compatible], excluded=[compatible]
    ) is None


def test_python_version_reads_interpreter():
    assert provisioning.python_version(sys.executable) == sys.version_info[:2]


def test_installer_error_includes_stderr(tmp_path):
    failing = tmp_path / "fail"
    failing.write_text("#!/bin/sh\necho 'No matching distribution' >&2\nexit 7\n")
    failing.chmod(0o755)

    with pytest.raises(RuntimeError) as caught:
        provisioning.run_checked([str(failing)], env={}, label="installing packages")

    assert "installing packages failed (exit 7)" in str(caught.value)
    assert "No matching distribution" in str(caught.value)
