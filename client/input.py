"""Terminal input, and the bridge from every event source to one asyncio queue.

blessed's `inkey()` blocks, so it cannot share a thread with an event loop.
Each source - keyboard, terminal resize, network - pushes a tagged `AppEvent`
into a single queue, so the loop is one `await get()` and a `match`.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
from collections.abc import Generator
from dataclasses import dataclass

import blessed
from blessed.keyboard import Keystroke

from protocol import ServerEvent

# All-motion mouse tracking, and SGR coordinates so positions past column 95
# survive the encoding.
MOUSE_MODES = (1003, 1006)

SCROLL_KEYS = {"KEY_LEFT": (-1, 0), "KEY_RIGHT": (1, 0), "KEY_UP": (0, -1), "KEY_DOWN": (0, 1)}

# Cells per wheel notch. One would feel glacial next to the arrow keys.
WHEEL_STEP = 3


@dataclass(frozen=True)
class KeyPress:
    key: Keystroke


@dataclass(frozen=True)
class Resized:
    """The terminal changed size."""


@dataclass(frozen=True)
class Received:
    """The server pushed something at us."""

    event: ServerEvent


# One queue carries all three, so the loop needs no select and no correlation
# between a source and the type it yields - the tag is on the item.
type AppEvent = KeyPress | Resized | Received


@contextlib.contextmanager
def mouse_tracking(term: blessed.Terminal) -> Generator[None]:
    """Turn on mouse tracking without asking the terminal's permission first.

    blessed's `mouse_enabled()` queries each mode with DECRQM and skips any the
    reply claims is already on. Ghostty answers that 1003 is enabled on a fresh
    terminal when it isn't, so blessed sends no enable and no motion events ever
    arrive - hover silently does nothing there while working elsewhere.

    These set the modes directly. They also update blessed's mode cache, which
    is what its input parser consults to decide whether to decode mouse
    sequences, so writing the raw escapes instead would enable the terminal but
    leave the decoder handing back `CSI`, `<`, `3`, `5` as separate keystrokes.
    """
    term._dec_mode_set_enabled(*MOUSE_MODES)  # pyright: ignore[reportPrivateUsage]
    try:
        yield
    finally:
        term._dec_mode_set_disabled(*MOUSE_MODES)  # pyright: ignore[reportPrivateUsage]


def wheel_delta(name: str) -> tuple[int, int] | None:
    """Scroll delta for a wheel event name, or None if it isn't one.

    blessed reports these as MOUSE_SCROLL_UP / MOUSE_SCROLL_DOWN, with
    modifiers folded into the name (MOUSE_SHIFT_SCROLL_UP). Shift scrolls
    sideways, matching how most terminal apps treat it.
    """
    if "SCROLL_UP" in name:
        step = -WHEEL_STEP
    elif "SCROLL_DOWN" in name:
        step = WHEEL_STEP
    else:
        return None
    return (step, 0) if "SHIFT" in name else (0, step)


def watch_resize(events: asyncio.Queue[AppEvent]) -> None:
    """Post a `Resized` on SIGWINCH.

    A signal rather than a size check each pass: the loop blocks until
    something happens, so there is no periodic wakeup left to hang a poll on.
    Silently does nothing where the signal is unavailable, costing at worst a
    stale layout until the next keypress.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGWINCH, lambda: events.put_nowait(Resized()))
    except (AttributeError, NotImplementedError, ValueError):
        pass


def pump_keys(
    term: blessed.Terminal,
    events: asyncio.Queue[AppEvent],
    stop: threading.Event,
    poll: float = 0.1,
) -> None:
    """Post a `KeyPress` per keystroke, read on a dedicated thread.

    `call_soon_threadsafe` is the supported way to hand work to the loop from a
    foreign thread. asyncio.Queue is not itself thread safe, so the put has to
    happen *on* the loop - calling `put_nowait` from the pump thread appears to
    work and corrupts the queue under load.

    A dedicated thread rather than `asyncio.to_thread`, because this runs for
    the life of the app and would otherwise hold a default-executor worker.

    `poll` is not a latency floor: `inkey` returns the moment a key arrives, so
    the timeout only bounds how long before the thread rechecks `stop`.
    """
    loop = asyncio.get_running_loop()

    def pump() -> None:
        while not stop.is_set():
            key = term.inkey(timeout=poll)
            if key:
                _ = loop.call_soon_threadsafe(events.put_nowait, KeyPress(key))

    threading.Thread(target=pump, daemon=True, name="keys").start()


async def pump_server(incoming: asyncio.Queue[ServerEvent], events: asyncio.Queue[AppEvent]) -> None:
    """Forward server pushes onto the one queue the loop reads."""
    while True:
        events.put_nowait(Received(await incoming.get()))
