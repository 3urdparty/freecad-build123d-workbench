"""JSON-RPC 2.0 server over newline-delimited JSON on localhost TCP.

Handshake: prints ``FC_CODE_KERNEL PORT=<port>`` on stdout once bound, so the
spawning KernelManager can discover the ephemeral port. Every request must
carry the session token from the FC_CODE_KERNEL_TOKEN environment variable.
"""

from __future__ import annotations

import hmac
import json
import os
import platform
import socketserver
import sys
from typing import Any

from . import __version__
from .executor import introspect_params, run_script

TOKEN_ENV = "FC_CODE_KERNEL_TOKEN"


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "not installed"


def _hello(_params: dict) -> dict:
    return {
        "kernel": __version__,
        "protocol": 0,
        "python": platform.python_version(),
        "build123d": _pkg_version("build123d"),
        "cadquery": _pkg_version("cadquery"),
        "ocp": _pkg_version("cadquery-ocp") or _pkg_version("ocp"),
    }


def _run(params: dict) -> dict:
    return run_script(path=params.get("path"), source=params.get("source"),
                      params=params.get("params") or {})


def _introspect(params: dict) -> list:
    return introspect_params(params["path"])


METHODS = {
    "kernel.hello": _hello,
    "kernel.run": _run,
    "kernel.introspect_params": _introspect,
}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        token = os.environ.get(TOKEN_ENV, "")
        for line in self.rfile:
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                self._reply(None, error={"code": -32700, "message": "parse error"})
                continue
            req_id = msg.get("id")
            params: dict[str, Any] = msg.get("params") or {}
            supplied = str(params.pop("_token", ""))
            params.pop("_v", None)
            if not (token and hmac.compare_digest(supplied, token)):
                self._reply(req_id, error={"code": -32004, "message": "bad token"})
                continue
            handler = METHODS.get(msg.get("method"))
            if handler is None:
                self._reply(req_id, error={"code": -32601, "message": "method not found"})
                continue
            try:
                self._reply(req_id, result=handler(params))
            except Exception as exc:  # method-level failure, connection survives
                self._reply(req_id, error={"code": -32603, "message": str(exc)})

    def _reply(self, req_id, result=None, error=None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        msg["error" if error is not None else "result"] = error if error is not None else result
        self.wfile.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.wfile.flush()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int = 0) -> None:
    with _Server(("127.0.0.1", port), _Handler) as srv:
        bound = srv.server_address[1]
        print(f"FC_CODE_KERNEL PORT={bound}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            print("kernel shutting down", file=sys.stderr, flush=True)
