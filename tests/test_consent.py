"""Consent gates for managed kernel provisioning."""

import importlib
import os
import sys
from types import SimpleNamespace

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)


@pytest.fixture
def kernel_module(monkeypatch, tmp_path):
    warnings = []
    app = SimpleNamespace(
        GuiUp=False,
        Console=SimpleNamespace(
            PrintMessage=lambda _message: None,
            PrintWarning=warnings.append,
        ),
        getUserAppDataDir=lambda: str(tmp_path),
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.delitem(sys.modules, "freecad.code.preferences", raising=False)
    monkeypatch.delitem(sys.modules, "freecad.code.kernel_manager", raising=False)
    module = importlib.import_module("freecad.code.kernel_manager")
    monkeypatch.setattr(module.preferences, "env_dir_override", lambda: str(tmp_path / "env"))
    monkeypatch.setattr(module.preferences, "package_pins", lambda: "build123d==1\ncadquery==2")
    return module, app, warnings


def _declining_message_box():
    class MessageBox:
        Information = object()
        AcceptRole = object()
        RejectRole = object()
        instances = []

        def __init__(self):
            self.buttons = []
            self.informative_text = ""
            self.clicked = None
            type(self).instances.append(self)

        def setIcon(self, _icon):
            pass

        def setWindowTitle(self, _title):
            pass

        def setText(self, _text):
            pass

        def setInformativeText(self, text):
            self.informative_text = text

        def addButton(self, label, _role):
            button = object()
            self.buttons.append((label, button))
            return button

        def exec_(self):
            self.clicked = self.buttons[-1][1]

        def clickedButton(self):
            return self.clicked

    return MessageBox


def test_provisioned_environment_skips_consent_prompt(kernel_module, monkeypatch):
    module, _app, _warnings = kernel_module
    manager = module.KernelManager()
    monkeypatch.setattr(manager, "is_env_provisioned", lambda: True)

    assert manager.request_provisioning_consent() is True


def test_headless_opt_in_permits_provisioning(kernel_module, monkeypatch):
    module, _app, _warnings = kernel_module
    manager = module.KernelManager()
    monkeypatch.setenv(module.PROVISIONING_CONSENT_ENV, "1")

    assert manager.request_provisioning_consent() is True
    assert manager._provisioning_approved is True


def test_headless_without_opt_in_fails_closed(kernel_module, monkeypatch):
    module, _app, warnings = kernel_module
    manager = module.KernelManager()
    monkeypatch.delenv(module.PROVISIONING_CONSENT_ENV, raising=False)

    assert manager.request_provisioning_consent() is False
    assert module.PROVISIONING_CONSENT_ENV in warnings[0]


def test_declining_setup_does_not_delete_or_install(kernel_module, monkeypatch, tmp_path):
    module, app, _warnings = kernel_module
    app.GuiUp = True
    message_box = _declining_message_box()
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtWidgets=SimpleNamespace(
        QMessageBox=message_box)))
    manager = module.KernelManager()
    os.makedirs(manager.env_dir())
    deleted = []
    installed = []
    subprocesses = []
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: deleted.append(path))
    monkeypatch.setattr(module, "run_checked", lambda *_args, **_kwargs: installed.append(True))
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: subprocesses.append(True),
    )

    with pytest.raises(RuntimeError, match="postponed"):
        manager.ensure_env()

    assert deleted == []
    assert installed == []
    assert subprocesses == []


def test_declining_rebuild_preserves_environment_and_discloses_deletion(
    kernel_module, monkeypatch
):
    module, app, _warnings = kernel_module
    app.GuiUp = True
    monkeypatch.setattr(module.sys, "platform", "darwin")
    message_box = _declining_message_box()
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtWidgets=SimpleNamespace(
        QMessageBox=message_box)))
    manager = module.KernelManager()
    os.makedirs(manager.env_dir())
    deleted = []
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: deleted.append(path))

    assert manager.rebuild_env() is False
    assert deleted == []
    assert "will be deleted and recreated" in message_box.instances[0].informative_text
    assert "local changes" in message_box.instances[0].informative_text
    assert f"private uv {module.UV_VERSION}" in message_box.instances[0].informative_text
    assert manager._managed_uv_dir() in message_box.instances[0].informative_text
    assert [label for label, _button in message_box.instances[0].buttons] == [
        "Rebuild environment",
        "Cancel",
    ]


def test_windows_setup_discloses_private_uv_download(kernel_module, monkeypatch):
    module, app, _warnings = kernel_module
    app.GuiUp = True
    monkeypatch.setattr(module.sys, "platform", "win32")
    message_box = _declining_message_box()
    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtWidgets=SimpleNamespace(QMessageBox=message_box)),
    )
    manager = module.KernelManager()

    assert manager.request_provisioning_consent() is False
    assert f"private uv {module.UV_VERSION}" in message_box.instances[0].informative_text
    assert manager._managed_uv_dir() in message_box.instances[0].informative_text


def test_windows_setup_prefers_private_uv_over_system_python(kernel_module, monkeypatch):
    module, _app, _warnings = kernel_module
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "find_uv", lambda: None)
    monkeypatch.setattr(
        module,
        "find_compatible_python",
        lambda **_kwargs: pytest.fail("system Python should only be a fallback"),
    )
    bootstrapped = []
    monkeypatch.setattr(
        module,
        "bootstrap_uv",
        lambda path: bootstrapped.append(path) or str(os.path.join(path, "uv.exe")),
    )
    commands = []
    manager = module.KernelManager()
    manager._provisioning_approved = True

    def run(cmd, **_kwargs):
        commands.append(cmd)
        os.makedirs(manager.env_dir(), exist_ok=True)

    monkeypatch.setattr(module, "run_checked", run)
    monkeypatch.setattr(manager, "_prefer_vtk_ocp", lambda *_args: None)

    manager.ensure_env()

    assert bootstrapped == [manager._managed_uv_dir()]
    assert commands[0][:4] == [
        os.path.join(manager._managed_uv_dir(), "uv.exe"),
        "venv",
        "--python",
        module.KERNEL_PYTHON,
    ]
    assert commands[1][5:7] == ["--only-binary", ":all:"]
    assert manager.is_env_provisioned()


def test_macos_intel_uses_wheel_backed_numba_requirements(kernel_module, monkeypatch):
    module, _app, _warnings = kernel_module
    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")

    assert module._kernel_requirements() == [
        "build123d==1",
        "cadquery==2",
        "numba==0.62.1",
        "llvmlite==0.45.1",
    ]


def test_autostart_disabled_does_not_start_kernel(kernel_module, monkeypatch):
    module, _app, _warnings = kernel_module
    started = []
    manager = SimpleNamespace(ensure_started=lambda **kwargs: started.append(kwargs))
    gui = SimpleNamespace(
        Workbench=type("Workbench", (), {}),
        addWorkbench=lambda _workbench: None,
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setattr(module.preferences, "auto_start_kernel", lambda: False)
    monkeypatch.setattr(module.KernelManager, "instance", classmethod(lambda _cls: manager))
    monkeypatch.delitem(sys.modules, "freecad.code.init_gui", raising=False)
    init_gui = importlib.import_module("freecad.code.init_gui")

    init_gui.CodeWorkbench().Activated()

    assert started == []
