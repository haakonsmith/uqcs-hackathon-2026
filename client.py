"""Game client: start menu, then the board.

    python client.py

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

from client import terrain
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
from client.menu import JoinGame, OpenSettings, Quit, notice, run_menu
from client.state import App
from protocol import Connection, Join, JoinRejected

SEED = 42

# Bigger than any terminal, so there is something to scroll around.
BOARD_WIDTH = 160
BOARD_HEIGHT = 90


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


async def join(term: blessed.Terminal, events: asyncio.Queue[AppEvent], address: str) -> None:
    """Connect, join a room, then hand the board the live connection.

    Anything that goes wrong here is reported on the menu rather than raised:
    a wrong address or a server that isn't up is a normal thing to do, not a
    crash.
    """
    notice(term, f"connecting to {address} ...")
    try:
        socket = await asyncio.wait_for(websockets.connect(address), timeout=5.0)
    except (TimeoutError, OSError, websockets.InvalidURI) as error:
        notice(term, f"could not connect: {error}")
        await asyncio.sleep(2)
        return

    async with socket:
        conn = Connection(socket)
        reader = asyncio.create_task(conn.run())
        forwarder = asyncio.create_task(pump_server(conn.events, events))
        try:
            joined = await conn.send(Join(room="lobby"))
            if isinstance(joined, JoinRejected):
                notice(term, f"join refused: {joined.reason}")
                await asyncio.sleep(2)
                return

            # The server owns the board; until it sends one, generate locally
            # so there is something to look at.
            world = terrain.generate(
                width=BOARD_WIDTH,
                height=BOARD_HEIGHT,
                scale=10,
                octaves=6,
                seed=SEED,
                water_fraction=0.68,
            )
            await play(App(term=term, world=world), events)
        except ConnectionError as error:
            notice(term, f"connection lost: {error}")
            await asyncio.sleep(2)
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
                    case JoinGame(address=address):
                        await join(term, events, address)
                    case OpenSettings():
                        notice(term, "no settings yet")
                        await asyncio.sleep(1)
                    case Quit():
                        return
        finally:
            stop.set()


if __name__ == "__main__":
    asyncio.run(main())
