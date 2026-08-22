"""Game client: start menu, then the board.

    python client.py                              # menu, defaulting to localhost:8888
    python client.py --address 10.0.0.4           # someone else's machine
    python client.py --address ws://host:9000     # spelled out in full
    TERMINATION_SERVER=host:8888 python client.py --public
    python client.py --public                     # same, read from .env

The menu is arrow keys or the mouse; JOIN GAME asks for a username and
connects, SETTINGS edits the server address, EXIT (or `q`) leaves.

During a round, `s` drops out to $EDITOR to write a solution and submits it on
exit, `n` picks a territory, `space`/`p` place troops, `m` orders a march, `c`
clears both, `f` says you are finished with the phase and `tab` shows the
scoreboard.

Troops are placed and marched in the one phase, and neither happens as you ask
for it. Both are secret until the phase ends - your own show as a `(+n)` beside
the garrison they will join - and then they all happen together: what you
placed lands first, so it can march the same turn, and the biggest stack in a
territory takes it, losing as many troops as the next biggest had.

Joining lands in a lobby. The server generates the board only once every
player there has readied up, so the board arrives as a push rather than as
part of the join.

The board is larger than the terminal, so only a slice is on screen. Arrow
keys and the mouse wheel scroll it (shift+wheel scrolls sideways), `+` and
`-` zoom in and out by subsampling, the mouse inspects the cell under the
cursor, `o` switches the border between faint and bold, `l` toggles the legend
of who is who and what to press, `q` quits.

Every player's ground is outlined in their own colour, the local player in
allied blue. The colours are chosen by each client for itself - see
`client.palette`.

One event loop and one key pump serve both screens: every source posts a
tagged `AppEvent` onto a single queue, so each screen is an `await get()` and
a `match`.

For live tweaking of the generation parameters, run `viewer.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os

import blessed
import websockets
from dotenv import load_dotenv

from client.input import (
    AppEvent,
    KeyPress,
    KeyPump,
    Received,
    Resized,
    Tick,
    mouse_tracking,
    pump_clock,
    pump_server,
    watch_resize,
)
from client.lobby import run_lobby
from client.menu import DEFAULT_ADDRESS, JoinGame, Quit, alert, notice, run_menu, set_address, set_username
from client.palette import Factions
from client.state import App
from protocol import Connection, Join, JoinRejected


async def play(app: App, events: asyncio.Queue[AppEvent]) -> None:
    """Run the board until the player quits or the connection drops."""
    app.fit()
    app.draw()
    # Only the board needs a second hand; the menu has nothing that counts down.
    ticker = asyncio.create_task(pump_clock(events))
    try:
        while app.running:
            match await events.get():
                case KeyPress(key=key):
                    await app.handle_key(key)
                case Resized():
                    app.fit()
                case Received(event=event):
                    app.apply_event(event)
                case Tick():
                    app.tick()

            if app.dirty:
                app.draw()
    finally:
        _ = ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker


async def join(
    term: blessed.Terminal,
    events: asyncio.Queue[AppEvent],
    address: str,
    username: str,
    pump: KeyPump,
) -> None:
    """Connect, join a room, then hand the board the live connection.

    Anything that goes wrong here is reported on the menu rather than raised:
    a wrong address or a server that isn't up is a normal thing to do, not a
    crash.
    """
    notice(term, "CONNECTING", "", address, f"as {username}")
    try:
        socket = await asyncio.wait_for(websockets.connect(address), timeout=5.0)
    except (TimeoutError, OSError, websockets.InvalidURI) as error:
        await alert(term, events, "COULD NOT CONNECT", "", address, str(error))
        return

    async with socket:
        conn = Connection(socket)
        reader = asyncio.create_task(conn.run())
        forwarder = asyncio.create_task(pump_server(conn.events, events))
        try:
            notice(term, "JOINING", "", address, f"as {username}")
            joined = await conn.send(Join(room="lobby", name=username))
            if isinstance(joined, JoinRejected):
                await alert(term, events, "JOIN REFUSED", "", joined.reason)
                return

            # The board does not exist until the lobby says go, and the server
            # is the only end that generates one - two ends generating
            # separately would disagree the moment a parameter drifted.
            started = await run_lobby(term, events, conn, joined.lobby, joined.player_id)
            if started is None:
                return

            world, lobby = started
            board = world.to_map()
            # Colours are picked here rather than sent with the board: they are
            # presentation, and which side counts as "ours" is only known here.
            factions = Factions.assign(board, joined.player_id, lobby.players)
            await play(
                App(
                    term=term,
                    world=board,
                    factions=factions,
                    conn=conn,
                    pump=pump,
                    me=joined.player_id,
                ),
                events,
            )
        except ConnectionError as error:
            await alert(term, events, "CONNECTION LOST", "", str(error))
        finally:
            for task in (reader, forwarder):
                _ = task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


DEFAULT_PORT = 8888

# Where `--public` reads the shared server's address from - the environment,
# or a git-ignored `.env` beside this file. Not a constant in here, because
# this repository is public and the address is somebody's home connection
# running an unauthenticated server: committing it would publish it, and git
# history would keep it long after the address changed.
PUBLIC_ADDRESS_ENV = "TERMINATION_SERVER"


def websocket_url(value: str) -> str:
    """Accept `host`, `host:port` or a full `ws://...` and return a full URL.

    Typing the scheme and port every time is friction for the common case of
    "the machine over there", so both are filled in. An explicit scheme is
    checked rather than passed through: `http://` here fails inside
    `websockets.connect` with a message about the wrong library.
    """
    if "://" in value:
        scheme, _, rest = value.partition("://")
        if scheme not in ("ws", "wss"):
            raise argparse.ArgumentTypeError(f"{scheme!r} is not a websocket scheme; use ws:// or wss://")
        if not rest:
            raise argparse.ArgumentTypeError(f"{value!r} has no host")
        return value

    # An IPv6 literal is already bracketed, and its colons are not a port.
    host, sep, port = value.rpartition(":")
    if sep and not value.endswith("]") and port.isdigit():
        return f"ws://{host}:{port}"
    return f"ws://{value}:{DEFAULT_PORT}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="client.py",
        description="Play a game of Termination.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # Both write to `address`, so nothing downstream has to know which was
    # given, and asking for two servers at once is refused rather than one
    # quietly winning.
    where = parser.add_mutually_exclusive_group()
    where.add_argument(
        "--address",
        type=websocket_url,
        default=DEFAULT_ADDRESS,
        metavar="HOST[:PORT]",
        help="server to join; editable afterwards from the SETTINGS screen",
    )
    where.add_argument(
        "--public",
        action="store_true",
        help=f"join the shared server named by ${PUBLIC_ADDRESS_ENV}",
    )
    parser.add_argument("--name", metavar="NAME", help="pre-fill the username prompt")

    args = parser.parse_args(argv)
    if args.public:
        args.address = _public_address(parser)
    return args


def _public_address(parser: argparse.ArgumentParser) -> str:
    """Resolve `--public` from the environment, or explain how to set it.

    Read here rather than as an argparse default so that an unset variable is
    a clear error at startup instead of a connection attempt against "".
    """

    value = os.environ.get(PUBLIC_ADDRESS_ENV, "").strip()
    if not value:
        parser.error(
            f"--public needs {PUBLIC_ADDRESS_ENV} set, for example:\n"
            f"    export {PUBLIC_ADDRESS_ENV}=203.0.113.10:8888    # bash/zsh\n"
            f"    set -x {PUBLIC_ADDRESS_ENV} 203.0.113.10:8888    # fish"
        )
    try:
        return websocket_url(value)
    except argparse.ArgumentTypeError as error:
        parser.error(f"{PUBLIC_ADDRESS_ENV}={value!r}: {error}")


async def main(args: argparse.Namespace) -> None:
    term = blessed.Terminal()
    events: asyncio.Queue[AppEvent] = asyncio.Queue()

    set_address(args.address)
    if args.name:
        set_username(args.name)

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), mouse_tracking(term):
        # One pump for every screen, so keys never go missing between them,
        # and so it can be handed over wholesale to an editor.
        pump = KeyPump(term, events)
        pump.start()
        watch_resize(events)
        try:
            while True:
                match await run_menu(term, events):
                    case JoinGame(address=address, username=username):
                        await join(term, events, address, username, pump)
                    case Quit():
                        return
        finally:
            pump.stop()


if __name__ == "__main__":
    load_dotenv(override=False)

    asyncio.run(main(parse_args()))
