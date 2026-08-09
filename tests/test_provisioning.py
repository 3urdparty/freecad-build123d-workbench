"""Managed-kernel host discovery and installer error tests."""

import hashlib
import io
import os
import sys
import tarfile
import zipfile

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


def test_find_compatible_python_does_not_probe_macos_developer_stub(monkeypatch):
    probed = []
    monkeypatch.setattr(provisioning.sys, "platform", "darwin")
    monkeypatch.setattr(provisioning, "_is_executable", lambda _path: True)
    monkeypatch.setattr(
        provisioning,
        "python_version",
        lambda path, env=None: probed.append(path) or (3, 12),
    )

    assert provisioning.find_compatible_python(
        candidates=["/usr/bin/python3", "/opt/homebrew/bin/python3.12"]
    ) == "/opt/homebrew/bin/python3.12"
    assert probed == ["/opt/homebrew/bin/python3.12"]


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


def _uv_tarball(asset: str, contents: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo(f"{asset.removesuffix('.tar.gz')}/uv")
        info.size = len(contents)
        archive.addfile(info, io.BytesIO(contents))
    return output.getvalue()


def test_bootstrap_uv_macos_verifies_and_installs_binary(tmp_path, monkeypatch):
    asset = "uv-test-apple-darwin.tar.gz"
    contents = b"uv executable"
    payload = _uv_tarball(asset, contents)
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(provisioning.platform, "machine", lambda: "arm64")
    monkeypatch.setitem(provisioning.UV_ASSETS, ("darwin", "arm64"), (asset, checksum))
    monkeypatch.setattr(
        provisioning.urllib.request,
        "urlopen",
        lambda _url, timeout: io.BytesIO(payload),
    )

    installed = provisioning.bootstrap_uv_macos(str(tmp_path / "tools"))

    with open(installed, "rb") as uv_file:
        assert uv_file.read() == contents
    assert os.access(installed, os.X_OK)


def test_bootstrap_uv_macos_rejects_bad_checksum(tmp_path, monkeypatch):
    asset = "uv-test-apple-darwin.tar.gz"
    payload = _uv_tarball(asset, b"uv executable")
    monkeypatch.setattr(provisioning.platform, "machine", lambda: "arm64")
    monkeypatch.setitem(
        provisioning.UV_ASSETS, ("darwin", "arm64"), (asset, "0" * 64)
    )
    monkeypatch.setattr(
        provisioning.urllib.request,
        "urlopen",
        lambda _url, timeout: io.BytesIO(payload),
    )

    with pytest.raises(RuntimeError, match="integrity check"):
        provisioning.bootstrap_uv_macos(str(tmp_path / "tools"))

    assert not (tmp_path / "tools" / "uv").exists()


def _uv_zip(asset: str, contents: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        archive.writestr("uv.exe", contents)
    return output.getvalue()


def test_bootstrap_uv_windows_verifies_and_installs_executable(tmp_path, monkeypatch):
    asset = "uv-test-pc-windows-msvc.zip"
    contents = b"windows uv executable"
    payload = _uv_zip(asset, contents)
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setitem(
        provisioning.UV_ASSETS, ("win32", "x86_64"), (asset, checksum)
    )
    monkeypatch.setattr(
        provisioning.urllib.request,
        "urlopen",
        lambda _request, timeout: io.BytesIO(payload),
    )

    installed = provisioning.bootstrap_uv(
        str(tmp_path / "tools"), platform_name="win32", machine="AMD64"
    )

    assert installed.endswith("uv.exe")
    with open(installed, "rb") as uv_file:
        assert uv_file.read() == contents
