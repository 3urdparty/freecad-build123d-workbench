"""Provision, spawn, and talk to the kernel process.

The kernel lives in a managed virtual environment that is completely
independent of FreeCAD's embedded Python. This module is the only place in
the workbench that knows the kernel exists as a *process*; everything else
goes through :meth:`KernelManager.run_script`.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import threading

import FreeCAD as App  # type: ignore[import-not-found]

from . import preferences, rpc
from .provisioning import MIN_PYTHON, find_compatible_python, find_uv, run_checked
from .rpc import RpcClient, RpcError

HANDSHAKE_PREFIX = "FC_CODE_KERNEL PORT="
TOKEN_ENV = "FC_CODE_KERNEL_TOKEN"
PROVISIONING_CONSENT_ENV = "FC_CODE_PROVISIONING_CONSENT"

# Python version for the kernel's managed environment. The kernel only ever
# talks JSON-RPC to FreeCAD, so this does NOT need to match FreeCAD's own
# interpreter — and it deliberately must not BE FreeCAD's interpreter: a venv
# seeded from a conda/bundled python inherits C extensions (pyexpat, ...)
# built against that distribution's private shared libraries, which stop
# resolving inside the kernel process (e.g. conda-forge pyexpat needing
# XML_SetAllocTrackerActivationThreshold from a newer libexpat than the one
# the loader finds). uv provisions a standalone CPython instead.
KERNEL_PYTHON = "3.12"

# FreeCAD exports these into its own process environment (PYTHONHOME points
# at the .app bundle's Resources). Any subprocess that runs a *different*
# Python — the kernel venv, uv's build backend — inherits them and dies with
# "ModuleNotFoundError: No module named 'encodings'". Scrub them from every
# subprocess we spawn.
SCRUB_ENV_VARS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "PYTHONNOUSERSITE",
    "VIRTUAL_ENV",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
)


def _clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB_ENV_VARS}
    if extra:
        env.update(extra)
    return env


def _log(msg: str) -> None:
    App.Console.PrintMessage(f"[Code] {msg}\n")


def _warn(msg: str) -> None:
    App.Console.PrintWarning(f"[Code] {msg}\n")


def _addon_root() -> str:
    # .../Mod/freecad-code-workbench/freecad/code -> addon root
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class KernelManager:
    """Singleton owner of the kernel subprocess and its RPC connection."""

    _instance: KernelManager | None = None

    @classmethod
    def instance(cls) -> KernelManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._client: RpcClient | None = None
        self._token: str = ""
        self._lock = threading.RLock()
        self._provisioning_approved = False

    # -- environment ---------------------------------------------------------

    def env_dir(self) -> str:
        override = preferences.env_dir_override()
        if override:
            return override
        return os.path.join(App.getUserAppDataDir(), "CodeWorkbench", "env")

    def _env_python(self) -> str:
        sub = "Scripts" if sys.platform == "win32" else "bin"
        exe = "python.exe" if sys.platform == "win32" else "python"
        return os.path.join(self.env_dir(), sub, exe)

    def _sentinel(self) -> str:
        return os.path.join(self.env_dir(), ".provisioned")

    def is_env_provisioned(self) -> bool:
        """Whether the managed environment completed provisioning."""
        return os.path.exists(self._sentinel())

    def request_provisioning_consent(self, *, force: bool = False) -> bool:
        """Ask before a first-run download or an explicit environment rebuild."""
        if not force and (self.is_env_provisioned() or self._provisioning_approved):
            return True
        # Headless automation has no GUI in which to answer the dialog. This
        # opt-in must be set explicitly by the caller, including CI.
        if os.environ.get(PROVISIONING_CONSENT_ENV) == "1":
            self._provisioning_approved = True
            return True
        if not App.GuiUp:
            _warn(
                "kernel setup requires explicit consent in headless mode; "
                f"set {PROVISIONING_CONSENT_ENV}=1"
            )
            return False

        from PySide import QtWidgets  # FreeCAD's PySide shim

        requirements = [r.strip() for r in preferences.package_pins().splitlines() if r.strip()]
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Information)
        if force:
            box.setWindowTitle("Rebuild Code Workbench environment")
            box.setText("Rebuild the isolated Python environment for Code Workbench?")
            box.setInformativeText(
                f"The existing Code Workbench environment at:\n{self.env_dir()}\n\n"
                "will be deleted and recreated. Installed packages and local changes "
                "inside that environment will be lost.\n\n"
                f"Python {KERNEL_PYTHON} may be downloaded. The workbench will install "
                f"its pinned build123d/CadQuery packages:\n{', '.join(requirements)}\n"
                "along with their OCP and Jedi dependencies. This requires network "
                "access and disk space."
            )
            accept_label = "Rebuild environment"
            cancel_label = "Cancel"
        else:
            box.setWindowTitle("Set up Code Workbench environment")
            box.setText("Set up the isolated Python environment for Code Workbench?")
            box.setInformativeText(
                f"The environment will be created at:\n{self.env_dir()}\n\n"
                f"Python {KERNEL_PYTHON} may be downloaded. The workbench will install "
                f"its pinned build123d/CadQuery packages:\n{', '.join(requirements)}\n"
                "along with their OCP and Jedi dependencies.\n\n"
                "This requires network access and disk space. No download starts unless "
                "you choose Set up environment."
            )
            accept_label = "Set up environment"
            cancel_label = "Not now"
        setup = box.addButton(accept_label, QtWidgets.QMessageBox.AcceptRole)
        box.addButton(cancel_label, QtWidgets.QMessageBox.RejectRole)
        box.exec_()
        accepted = box.clickedButton() is setup
        if accepted:
            self._provisioning_approved = True
        else:
            _log("kernel environment setup postponed.")
        return accepted

    def ensure_env(self) -> None:
        """Create the venv and install kernel + CAD packages if missing.

        Provisioning is only considered complete once the sentinel file
        exists — a run that failed halfway (network error, build failure)
        leaves no sentinel and is wiped and redone on the next attempt.
        """
        if self.is_env_provisioned():
            return
        if not self.request_provisioning_consent():
            raise RuntimeError("kernel environment setup was postponed")
        env_dir = self.env_dir()
        if os.path.isdir(env_dir):
            _log("removing incomplete kernel environment…")
            shutil.rmtree(env_dir)
        os.makedirs(os.path.dirname(env_dir), exist_ok=True)
        clean = _clean_env()
        python = self._env_python()
        uv = find_uv()
        _log(f"provisioning kernel environment at {env_dir} (first run)…")
        if uv:
            # only-managed: never seed the venv from FreeCAD's/conda's own
            # python (see KERNEL_PYTHON above); uv downloads and caches a
            # standalone CPython on first run.
            _log(f"creating a Python {KERNEL_PYTHON} environment with {uv}…")
            run_checked(
                [uv, "venv", "--python", KERNEL_PYTHON, env_dir],
                env={**clean, "UV_PYTHON_PREFERENCE": "only-managed"},
                label="creating the kernel environment",
            )
            pip_prefix = [uv, "pip", "install", "--python", python]
        else:
            # Use *a* system python; FreeCAD's embedded interpreter may not
            # ship the venv module on all platforms.
            host_py = find_compatible_python(env=clean, excluded=(sys.executable,))
            if host_py is None:
                required = ".".join(str(part) for part in MIN_PYTHON)
                raise RuntimeError(
                    f"Python {required}+ is required for the kernel, but FreeCAD could not "
                    "find uv or a compatible system Python. Install uv, then use "
                    "Code → Rebuild kernel environment."
                )
            _log(f"creating the kernel environment with {host_py}…")
            run_checked(
                [host_py, "-m", "venv", env_dir],
                env=clean,
                label="creating the kernel environment",
            )
            pip_prefix = [python, "-m", "pip", "install"]
        requirements = [r.strip() for r in preferences.package_pins().splitlines() if r.strip()]
        kernel_src = os.path.join(_addon_root(), "kernel")
        _log("installing kernel packages: " + ", ".join(requirements) + "…")
        run_checked(
            [*pip_prefix, "-e", kernel_src, *requirements],
            env=clean,
            label="installing kernel packages",
        )
        self._prefer_vtk_ocp(python, clean, uv)
        with open(self._sentinel(), "w", encoding="utf-8") as f:
            f.write("ok\n")
        _log("kernel environment ready.")

    def _prefer_vtk_ocp(self, python: str, clean: dict, uv: str | None) -> None:
        """Put the VTK-enabled OCP back when both variants land in the env.

        build123d requires cadquery-ocp-novtk, cadquery requires the
        VTK-enabled cadquery-ocp, and both wheels ship the same
        OCP/OCP.<abi>.so — so whichever the installer writes last decides
        what is importable. When novtk wins, `import cadquery` fails inside
        OCP.IVtkOCC (whose shim swallows the error and prints "VTK not
        installed"). build123d is happy with the VTK build, it being a
        superset, so reinstall that one last.

        novtk is deliberately left installed: its RECORD lists the very
        files this restores, so uninstalling it would delete them again.
        """
        probe = [python, "-c", "import cadquery"]
        first = subprocess.run(probe, env=clean, capture_output=True, text=True)
        if first.returncode == 0 or "IVtkOCC" not in first.stderr:
            return  # cadquery absent, or failing for an unrelated reason
        version = subprocess.run(
            [python, "-c", "import importlib.metadata as m; print(m.version('cadquery-ocp'))"],
            env=clean, capture_output=True, text=True,
        ).stdout.strip()
        if not version:
            _log("cadquery cannot import and cadquery-ocp is not installed; leaving as is.")
            return
        _log(f"reinstalling cadquery-ocp=={version} so its VTK bindings win over novtk…")
        if uv:
            cmd = [uv, "pip", "install", "--python", python,
                   "--reinstall-package", "cadquery-ocp", f"cadquery-ocp=={version}"]
        else:
            cmd = [python, "-m", "pip", "install", "--force-reinstall", "--no-deps",
                   f"cadquery-ocp=={version}"]
        run_checked(
            cmd,
            env=clean,
            label=f"reinstalling cadquery-ocp=={version}",
        )
        again = subprocess.run(probe, env=clean, capture_output=True, text=True)
        if again.returncode:
            _log("cadquery still fails to import:\n" + again.stderr.strip())

    # -- process lifecycle ---------------------------------------------------

    def ensure_started(self, background: bool = False) -> None:
        # Do this on the calling (normally GUI) thread. Qt dialogs cannot be
        # safely created by the background provisioning thread.
        if not self.is_env_provisioned() and not self.request_provisioning_consent():
            return
        if background:
            def _bg() -> None:
                try:
                    self._ensure_started_sync()
                except Exception:
                    pass  # already logged inside _ensure_started_sync
            threading.Thread(target=_bg, daemon=True).start()
        else:
            self._ensure_started_sync()

    def _ensure_started_sync(self) -> None:
        with self._lock:
            if self._client is not None and self._client.connected:
                return
            try:
                self.ensure_env()
                self._spawn()
            except Exception as exc:  # surfaced, never swallowed
                _warn(f"kernel startup failed: {exc}")
                raise

    def _spawn(self) -> None:
        self._token = secrets.token_hex(16)
        env = _clean_env({TOKEN_ENV: self._token})
        self._proc = subprocess.Popen(
            [self._env_python(), "-m", "fc_code_kernel", "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline().strip()
        if not line.startswith(HANDSHAKE_PREFIX):
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise RuntimeError(f"bad kernel handshake: {line!r}\n{err}")
        port = int(line[len(HANDSHAKE_PREFIX):])
        self._client = RpcClient("127.0.0.1", port, self._token)
        self._client.connect()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        hello = self._client.call("kernel.hello")
        _log(
            "kernel up: python {python}, build123d {build123d}, "
            "cadquery {cadquery}".format(**{k: hello.get(k, "?") for k in
                                            ("python", "build123d", "cadquery")})
        )

    def _pump_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            _warn(f"kernel: {line.rstrip()}")

    def restart(self) -> None:
        with self._lock:
            self.stop()
            self._ensure_started_sync()

    def rebuild_env(self) -> bool:
        """Stop the kernel, delete the managed environment, re-provision
        from scratch (background — progress in the Report view). This is
        how users pick up new kernel dependencies after an addon update."""
        if not self.request_provisioning_consent(force=True):
            return False
        with self._lock:
            self.stop()
            env_dir = self.env_dir()
            if os.path.isdir(env_dir):
                _log(f"removing kernel environment at {env_dir}…")
                shutil.rmtree(env_dir)
        self.ensure_started(background=True)
        return True

    def stop(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            if self._proc is not None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self._proc = None

    # -- API used by the rest of the workbench -------------------------------

    def run_script(self, path: str, params: dict) -> dict:
        """Execute a script in the kernel. Returns the kernel.run result dict.

        Result shape: {objects: [{name, brep_b64, color, alpha}], stdout,
        stderr, error: null | [{file, line, text}]}

        Runs are bounded by the RunTimeoutS preference (0 = unlimited): a
        busy exec() cannot be interrupted from outside, so a run that
        exceeds the budget gets its kernel process KILLED — the next run
        starts a fresh kernel lazily. With autosave in the editor, a
        half-typed `while True:` is an everyday event, not a corner case.
        """
        self._ensure_started_sync()
        assert self._client is not None
        timeout_s = preferences.run_timeout_s()
        rpc_timeout = float(timeout_s) if timeout_s > 0 else None
        try:
            return self._client.call("kernel.run", rpc_timeout=rpc_timeout,
                                     path=path, params=params)
        except Exception as exc:
            if isinstance(exc, RpcError) and exc.code == rpc.CALL_TIMED_OUT:
                # The kernel is still busy running the script — kill it.
                # Deliberately NO retry: re-running a hanging script would
                # hang again and double the stall.
                _warn(f"script run exceeded {timeout_s}s; stopping the kernel…")
                self.stop()
                raise RpcError(
                    rpc.CALL_TIMED_OUT,
                    f"script run exceeded {timeout_s}s and was stopped "
                    f"(RunTimeoutS preference); the kernel restarts on the "
                    f"next run",
                ) from None
            # Server-reported errors (bad params, method-level failure) come
            # back over a healthy connection — surface them. Everything else,
            # INCLUDING the connection-class RpcErrors (-32000 "not
            # connected", -32001 "kernel closed the connection" — a clean EOF
            # is exactly what an OOM-killed or crashed kernel looks like),
            # means the kernel is gone: one restart attempt.
            if isinstance(exc, RpcError) and exc.code not in (-32000, -32001):
                raise
            _warn(f"kernel connection lost ({exc}); restarting…")
            self.restart()
            assert self._client is not None
            return self._client.call("kernel.run", rpc_timeout=rpc_timeout,
                                     path=path, params=params)

    def introspect_params(self, path: str) -> list:
        self._ensure_started_sync()
        assert self._client is not None
        return self._client.call("kernel.introspect_params", path=path)

    def complete(self, source: str, line: int, column: int, path: str) -> list:
        """Completions at 1-based line / 0-based column. Returns items list."""
        self._ensure_started_sync()
        assert self._client is not None
        result = self._client.call("kernel.complete", source=source,
                                   line=line, column=column, path=path)
        return result.get("items", [])

    def signatures(self, source: str, line: int, column: int, path: str) -> list:
        self._ensure_started_sync()
        assert self._client is not None
        result = self._client.call("kernel.signatures", source=source,
                                   line=line, column=column, path=path)
        return result.get("signatures", [])
