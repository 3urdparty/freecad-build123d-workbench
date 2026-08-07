"""End-to-end RPC test: real kernel server + real workbench client.

Needs neither FreeCAD nor OCP — kernel.hello and the token/framing layer are
dependency-free by design.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "kernel"))
sys.path.insert(0, REPO)

from freecad.code.rpc import RpcClient, RpcError, decode_message, encode_request  # noqa: E402


def test_frame_roundtrip():
    raw = encode_request(7, "kernel.hello", {"a": 1}, token="t0k")
    assert raw.endswith(b"\n")
    msg = decode_message(raw)
    assert msg["id"] == 7
    assert msg["method"] == "kernel.hello"
    assert msg["params"]["_token"] == "t0k"
    assert msg["params"]["a"] == 1


@pytest.fixture()
def kernel_proc():
    env = dict(os.environ, FC_CODE_KERNEL_TOKEN="secret", PYTHONPATH=os.path.join(REPO, "kernel"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "fc_code_kernel", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
    )
    line = proc.stdout.readline().strip()
    assert line.startswith("FC_CODE_KERNEL PORT="), proc.stderr.read()
    port = int(line.split("=", 1)[1])
    # wait for the socket to accept
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield port
    proc.terminate()
    proc.wait(timeout=5)


def test_hello_and_bad_token(kernel_proc):
    port = kernel_proc

    good = RpcClient("127.0.0.1", port, token="secret", timeout=10)
    good.connect()
    hello = good.call("kernel.hello")
    assert hello["protocol"] == 0
    assert "python" in hello
    good.close()

    bad = RpcClient("127.0.0.1", port, token="wrong", timeout=10)
    bad.connect()
    with pytest.raises(RpcError) as exc_info:
        bad.call("kernel.hello")
    assert exc_info.value.code == -32004
    bad.close()


def test_unknown_method(kernel_proc):
    client = RpcClient("127.0.0.1", kernel_proc, token="secret", timeout=10)
    client.connect()
    with pytest.raises(RpcError) as exc_info:
        client.call("kernel.nope")
    assert exc_info.value.code == -32601
    client.close()
