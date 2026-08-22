"""Game client: start menu, then the board.

    python client.py

The menu is arrow keys or the mouse; JOIN GAME asks for a username and
connects, SETTINGS edits the server address, EXIT (or `q`) leaves.

Joining lands in a lobby. The server generates the board only once every
player there has readied up, so the board arrives as a push rather than as
part of the join.

The board is larger than the terminal, so only a slice is on screen. Arrow
keys and the mouse wheel scroll it (shift+wheel scrolls sideways), `+` and
`-` zoom in and out by subsampling, the mouse inspects the cell under the
cursor, `o` toggles the ownership overlay, `q` quits.

One event loop and one key pump serve both screens: every source posts a
tagged `AppEvent` onto a single queue, so each screen is an `await get()` and
a `match`.

For live tweaking of the generation parameters, run `viewer.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

import blessed
import websockets

from client.input import (
    AppEvent,
    KeyPress,
    Received,
    Resized,
    mouse_tracking,
    pump_keys,
    pump_server,
    watch_resize,
)
from client.lobby import run_lobby
from client.menu import JoinGame, Quit, alert, notice, run_menu
from client.state import App
from protocol import Connection, Join, JoinRejected


async def play(app: App, events: asyncio.Queue[AppEvent]) -> None:
    """Run the board until the player quits or the connection drops."""
    app.fit()
    app.draw()
    while app.running:
        match await events.get():
            case KeyPress(key=key):
                app.handle_key(key)
            case Resized():
                app.fit()
            case Received(event=event):
                app.apply_event(event)

        if app.dirty:
            app.draw()


async def join(term: blessed.Terminal, events: asyncio.Queue[AppEvent], address: str, username: str) -> None:
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
            world = await run_lobby(term, events, conn, joined.lobby, joined.player_id)
            if world is None:
                return

            await play(App(term=term, world=world.to_map()), events)
        except ConnectionError as error:
            await alert(term, events, "CONNECTION LOST", "", str(error))
        finally:
            for task in (reader, forwarder):
                _ = task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


async def main() -> None:
    term = blessed.Terminal()
    events: asyncio.Queue[AppEvent] = asyncio.Queue()
    stop = threading.Event()

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), mouse_tracking(term):
        # One pump for both screens, so keys never go missing between them.
        pump_keys(term, events, stop)
        watch_resize(events)
        try:
            while True:
                match await run_menu(term, events):
                    case JoinGame(address=address, username=username):
                        await join(term, events, address, username)
                    case Quit():
                        return
        finally:
            stop.set()


if __name__ == "__main__":
    asyncio.run(main())
