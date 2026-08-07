import argparse

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(prog="fc_code_kernel")
    parser.add_argument("--port", type=int, default=0,
                        help="TCP port to bind on 127.0.0.1 (0 = ephemeral)")
    args = parser.parse_args()
    serve(port=args.port)


if __name__ == "__main__":
    main()
