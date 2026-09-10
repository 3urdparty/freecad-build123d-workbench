"""FreeCAD build123d bridge.

JSON-RPC 2.0 over newline-delimited JSON on localhost TCP.

The bridge receives already-built geometry from an external build123d
development process and synchronizes it into native FreeCAD document objects.

This module MUST NOT import build123d, CadQuery, or OCP.
"""

from __future__ import annotations

import hmac
import json
import secrets
import socketserver
import threading
from typing import Any

from .dispatcher import submit


PROTOCOL_VERSION = 1


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _hello(_params: dict[str, Any]) -> dict[str, Any]:
    import FreeCAD as App

    return {
        "bridge": "build123d-freecad",
        "protocol": PROTOCOL_VERSION,
        "freecad": App.Version(),
    }


SERVER_METHODS = {
    "bridge.hello": _hello,
}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server: BridgeServer = self.server  # type: ignore[assignment]

        for line in self.rfile:
            req_id = None

            try:
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise RpcError(
                        -32700,
                        "parse error",
                    )

                req_id = msg.get("id")

                if msg.get("jsonrpc") != "2.0":
                    raise RpcError(
                        -32600,
                        "invalid JSON-RPC request",
                    )

                method = msg.get("method")

                if not isinstance(method, str):
                    raise RpcError(
                        -32600,
                        "method must be a string",
                    )

                params = msg.get("params") or {}

                if not isinstance(params, dict):
                    raise RpcError(
                        -32602,
                        "params must be an object",
                    )

                params = dict(params)

                version = params.pop("_v", None)

                if version != PROTOCOL_VERSION:
                    raise RpcError(
                        -32005,
                        f"unsupported protocol version: {version}",
                    )

                supplied_token = str(
                    params.pop("_token", "")
                )

                if not hmac.compare_digest(
                    supplied_token,
                    server.token,
                ):
                    raise RpcError(
                        -32004,
                        "bad token",
                    )

                local_handler = SERVER_METHODS.get(method)

                if local_handler is not None:
                    result = local_handler(params)
                else:
                    result = submit(
                        method,
                        params,
                    )

                self._reply(
                    req_id,
                    result=result,
                )

            except RpcError as exc:
                self._reply(
                    req_id,
                    error={
                        "code": exc.code,
                        "message": exc.message,
                    },
                )

            except KeyError as exc:
                self._reply(
                    req_id,
                    error={
                        "code": -32601,
                        "message": str(exc),
                    },
                )

            except Exception as exc:
                self._reply(
                    req_id,
                    error={
                        "code": -32603,
                        "message": str(exc),
                    },
                )

    def _reply(
        self,
        req_id: Any,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        response: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
        }

        if error is not None:
            response["error"] = error
        else:
            response["result"] = result

        self.wfile.write(
            (json.dumps(response) + "\n").encode("utf-8")
        )
        self.wfile.flush()


class BridgeServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        handler,
        token: str,
    ):
        super().__init__(address, handler)
        self.token = token


class ServerHandle:
    def __init__(
        self,
        server: BridgeServer,
        thread: threading.Thread,
    ):
        self.server = server
        self.thread = thread

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def token(self) -> str:
        return self.server.token

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def start_server(
    port: int = 0,
    token: str | None = None,
) -> ServerHandle:
    """Start the bridge in a background network thread.

    port=0 asks the OS to choose an available ephemeral port.
    """

    token = token or secrets.token_urlsafe(32)

    server = BridgeServer(
        ("127.0.0.1", port),
        _Handler,
        token=token,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        name="build123d-freecad-rpc",
        daemon=True,
    )

    thread.start()

    return ServerHandle(
        server=server,
        thread=thread,
    )
