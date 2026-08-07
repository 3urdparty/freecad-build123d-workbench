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

from . import preferences
from .rpc import RpcClient, RpcError

HANDSHAKE_PREFIX = "FC_CODE_KERNEL PORT="
TOKEN_ENV = "FC_CODE_KERNEL_TOKEN"

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

    def ensure_env(self) -> None:
        """Create the venv and install kernel + CAD packages if missing.

        Provisioning is only considered complete once the sentinel file
        exists — a run that failed halfway (network error, build failure)
        leaves no sentinel and is wiped and redone on the next attempt.
        """
        if os.path.exists(self._sentinel()):
            return
        env_dir = self.env_dir()
        if os.path.isdir(env_dir):
            _log("removing incomplete kernel environment…")
            shutil.rmtree(env_dir)
        os.makedirs(os.path.dirname(env_dir), exist_ok=True)
        clean = _clean_env()
        python = self._env_python()
        uv = shutil.which("uv")
        _log(f"provisioning kernel environment at {env_dir} (first run)…")
        if uv:
            subprocess.run([uv, "venv", env_dir], check=True, env=clean)
            pip_prefix = [uv, "pip", "install", "--python", python]
        else:
            # Use *a* system python; FreeCAD's embedded interpreter may not
            # ship the venv module on all platforms.
            host_py = shutil.which("python3") or sys.executable
            subprocess.run([host_py, "-m", "venv", env_dir], check=True, env=clean)
            pip_prefix = [python, "-m", "pip", "install"]
        requirements = [r.strip() for r in preferences.package_pins().splitlines() if r.strip()]
        kernel_src = os.path.join(_addon_root(), "kernel")
        subprocess.run([*pip_prefix, "-e", kernel_src, *requirements], check=True, env=clean)
        with open(self._sentinel(), "w", encoding="utf-8") as f:
            f.write("ok\n")
        _log("kernel environment ready.")

    # -- process lifecycle ---------------------------------------------------

    def ensure_started(self, background: bool = False) -> None:
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
        """
        self._ensure_started_sync()
        assert self._client is not None
        try:
            return self._client.call("kernel.run", path=path, params=params)
        except RpcError:
            raise
        except Exception:
            # Connection died (kernel crash) — one restart attempt.
            _warn("kernel connection lost; restarting…")
            self.restart()
            assert self._client is not None
            return self._client.call("kernel.run", path=path, params=params)

    def introspect_params(self, path: str) -> list:
        self._ensure_started_sync()
        assert self._client is not None
        return self._client.call("kernel.introspect_params", path=path)
