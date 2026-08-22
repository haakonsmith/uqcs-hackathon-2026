"""Run the game server.

    python server.py                          # localhost:8888
    python server.py --host 0.0.0.0           # reachable from the network
    python server.py --port 9000 -v           # another port, every frame logged
    python server.py --judge placeholder      # score without executing anything

Logging is configured here rather than in `server/server.py`, because a
library that configures the root logger on import decides the format for
whatever imports it. The module just asks for a logger; this picks where it
goes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from server.judge import Judge, PlaceholderJudge, SubprocessJudge
from server.server import DEFAULT_HOST, DEFAULT_PORT, Server

logger = logging.getLogger("Server")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="Host a game of Termination.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        metavar="ADDRESS",
        help="interface to bind; 0.0.0.0 for every interface",
    )
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument(
        "--judge",
        choices=("subprocess", "placeholder"),
        default="subprocess",
        help="subprocess runs submitted code in a sandbox; placeholder executes nothing and scores off markers",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every frame, and websockets' own output")
    return parser.parse_args(argv)


def port_number(value: str) -> int:
    """A port argparse will reject before the socket does, with a clearer error."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{port} is outside 1-65535")
    return port


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-10s %(message)s",
        datefmt="%H:%M:%S",
    )
    # websockets logs every ping and frame at DEBUG, which buries ours.
    logging.getLogger("websockets").setLevel(logging.DEBUG if verbose else logging.WARNING)


def build_judge(name: str) -> Judge:
    return SubprocessJudge() if name == "subprocess" else PlaceholderJudge()


async def main(args: argparse.Namespace) -> None:
    await Server(judge=build_judge(args.judge)).run(host=args.host, port=args.port)


if __name__ == "__main__":
    arguments = parse_args()
    configure_logging(arguments.verbose)
    try:
        asyncio.run(main(arguments))
    except KeyboardInterrupt:
        logger.info("shutting down")
    except OSError as error:
        # Almost always "address already in use", which does not need a
        # traceback to act on.
        logger.error(f"could not listen on {arguments.host}:{arguments.port}: {error}")
        raise SystemExit(1) from None
