"""Board viewer.

    python client.py

The board is larger than the terminal, so only a slice is on screen. Arrow
keys and the mouse wheel scroll it (shift+wheel scrolls sideways), `+` and
`-` zoom in and out by subsampling, the mouse inspects the cell under the
cursor, `o` toggles the ownership overlay, `q` quits.

The loop is async so the keyboard and the server can drive it together.
Every source posts a tagged `AppEvent` onto one queue, so the loop is a single
`await get()` and a `match`.

For live tweaking of the generation parameters, run `viewer.py`.
"""

from __future__ import annotations

import asyncio
import threading

import blessed

from client import terrain
from client.input import AppEvent, KeyPress, Received, Resized, mouse_tracking, pump_keys, watch_resize
from client.render import describe
from client.state import App

SEED = 42

# Bigger than any terminal, so there is something to scroll around.
BOARD_WIDTH = 160
BOARD_HEIGHT = 90


async def run(app: App) -> None:
    events: asyncio.Queue[AppEvent] = asyncio.Queue()
    stop = threading.Event()
    pump_keys(app.term, events, stop)
    watch_resize(events)

    try:
        app.fit()
        app.draw()
        while app.running:
            # The network joins by starting pump_server against this same
            # queue; nothing here changes, because the tag rides on the item.
            match await events.get():
                case KeyPress(key=key):
                    app.handle_key(key)
                case Resized():
                    app.fit()
                case Received(event=event):
                    app.apply_event(event)

            if app.dirty:
                app.draw()
    finally:
        stop.set()


async def main() -> None:
    world = terrain.generate(
        width=BOARD_WIDTH,
        height=BOARD_HEIGHT,
        scale=10,
        octaves=6,
        seed=SEED,
        water_fraction=0.68,
    )
    term = blessed.Terminal()
    app = App(term=term, world=world)

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), mouse_tracking(term):
        await run(app)

    describe(world)


if __name__ == "__main__":
    asyncio.run(main())
