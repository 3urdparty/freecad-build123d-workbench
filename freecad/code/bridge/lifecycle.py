from .discovery import remove_discovery, write_discovery
from .dispatcher import start_dispatcher, stop_dispatcher
from .server import PROTOCOL_VERSION, ServerHandle, start_server


_server: ServerHandle | None = None


def start() -> ServerHandle:
    global _server

    if _server is not None:
        return _server

    start_dispatcher()
    _server = start_server()

    write_discovery(
        host="127.0.0.1",
        port=_server.port,
        token=_server.token,
        protocol=PROTOCOL_VERSION,
    )

    return _server


def stop() -> None:
    global _server

    remove_discovery()

    if _server is not None:
        _server.stop()
        _server = None

    stop_dispatcher()


def get_server() -> ServerHandle | None:
    return _server


def is_running() -> bool:
    return _server is not None
