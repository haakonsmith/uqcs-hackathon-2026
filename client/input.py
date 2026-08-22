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
class Tick:
    """A second passed. Only the phase countdown cares."""


@dataclass(frozen=True)
class Received:
    """The server pushed something at us."""

    event: ServerEvent


# One queue carries all three, so the loop needs no select and no correlation
# between a source and the type it yields - the tag is on the item.
type AppEvent = KeyPress | Resized | Received | Tick


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


class KeyPump:
    """Reads the terminal on a thread and posts one `KeyPress` per keystroke.

    `call_soon_threadsafe` is the supported way to hand work to the loop from a
    foreign thread. asyncio.Queue is not itself thread safe, so the put has to
    happen *on* the loop - calling `put_nowait` from the pump thread appears to
    work and corrupts the queue under load.

    A dedicated thread rather than `asyncio.to_thread`, because this runs for
    the life of the app and would otherwise hold a default-executor worker.

    Stoppable, because a subprocess that wants the keyboard - an editor, say -
    cannot have it while this thread is sitting in `inkey()` eating bytes.
    """

    def __init__(self, term: blessed.Terminal, events: asyncio.Queue[AppEvent], poll: float = 0.1) -> None:
        self._term: blessed.Terminal = term
        self._events: asyncio.Queue[AppEvent] = events
        # Not a latency floor: `inkey` returns the moment a key arrives, so
        # this only bounds how long before the thread rechecks the stop flag.
        self._poll: float = poll
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._stop.clear()

        def pump() -> None:
            while not self._stop.is_set():
                key = self._term.inkey(timeout=self._poll)
                if key and not self._stop.is_set():
                    _ = loop.call_soon_threadsafe(self._events.put_nowait, KeyPress(key))

        self._thread = threading.Thread(target=pump, daemon=True, name="keys")
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            # Wait for it to actually leave `inkey`, or it takes the first
            # keystroke meant for whatever runs next.
            self._thread.join(timeout)
        self._thread = None

    @contextlib.contextmanager
    def paused(self) -> Generator[None]:
        """Let go of the keyboard for the duration of the block."""
        self.stop()
        try:
            yield
        finally:
            self.start()


@contextlib.contextmanager
def suspended(term: blessed.Terminal) -> Generator[None]:
    """Give the terminal back, for a subprocess that wants all of it.

    Mouse tracking off first: motion reports would otherwise arrive in the
    subprocess as escape sequences it never asked for. Then out of the alt
    screen and cursor back on, so an editor starts on a clean one.

    cbreak is left alone. A full-screen program sets its own line discipline
    and restores what it found, which is this one.
    """
    term._dec_mode_set_disabled(*MOUSE_MODES)  # pyright: ignore[reportPrivateUsage]
    print(term.rmcup + term.normal_cursor, end="", flush=True)
    try:
        yield
    finally:
        print(term.smcup + term.hide_cursor, end="", flush=True)
        term._dec_mode_set_enabled(*MOUSE_MODES)  # pyright: ignore[reportPrivateUsage]


async def pump_clock(events: asyncio.Queue[AppEvent], period: float = 1.0) -> None:
    """Post a `Tick` every `period` seconds.

    The loop blocks on the queue, so without this a countdown would only move
    when something else happened to wake the screen up.
    """
    while True:
        await asyncio.sleep(period)
        events.put_nowait(Tick())


async def pump_server(incoming: asyncio.Queue[ServerEvent], events: asyncio.Queue[AppEvent]) -> None:
    """Forward server pushes onto the one queue the loop reads."""
    while True:
        events.put_nowait(Received(await incoming.get()))
