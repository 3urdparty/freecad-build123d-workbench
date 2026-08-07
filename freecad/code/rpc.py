"""JSON-RPC 2.0 client over newline-delimited JSON on localhost TCP.

Kept dependency-free and FreeCAD-free so it is unit-testable anywhere.
The server half lives in ``fc_code_kernel.server`` (deliberately not shared
code: the two packages install into different Python environments).

Protocol v0 notes:
- one JSON object per line, UTF-8
- every request carries the session token handed to the kernel at spawn
- BREP payloads are base64 strings inside results (see DESIGN.md §5.2 for the
  planned move to binary frames if profiling demands it)
"""

from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 0


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


def encode_request(req_id: int, method: str, params: dict, token: str) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": {**params, "_token": token, "_v": PROTOCOL_VERSION},
            }
        )
        + "\n"
    ).encode("utf-8")


def decode_message(line: bytes) -> dict:
    return json.loads(line.decode("utf-8"))


@dataclass
class RpcClient:
    host: str
    port: int
    token: str
    timeout: float = 300.0
    _sock: socket.socket | None = field(default=None, repr=False)
    _rfile: Any = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _next_id: int = 0

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._rfile = self._sock.makefile("rb")

    def close(self) -> None:
        try:
            if self._rfile is not None:
                self._rfile.close()
            if self._sock is not None:
                self._sock.close()
        finally:
            self._sock = None
            self._rfile = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def call(self, method: str, **params: Any) -> Any:
        """Synchronous request/response. One in flight at a time (v0)."""
        if self._sock is None:
            raise RpcError(-32000, "not connected")
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            self._sock.sendall(encode_request(req_id, method, params, self.token))
            while True:
                line = self._rfile.readline()
                if not line:
                    self.close()
                    raise RpcError(-32001, "kernel closed the connection")
                msg = decode_message(line)
                if msg.get("id") != req_id:
                    # v0 has no server-initiated messages; ignore strays.
                    continue
                if "error" in msg:
                    err = msg["error"]
                    raise RpcError(err.get("code", -32603), err.get("message", ""), err.get("data"))
                return msg.get("result")
