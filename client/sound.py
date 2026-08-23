"""The terminal bell, for the moments a player is not looking at the board.

Client-side by definition: the point of a noise is that somebody hears it, and
the server usually runs on another machine with nobody sitting at it.

Never blocks. `winsound.Beep` is synchronous and holds the calling thread for
the length of the tone, which in the draw loop is a visible stall, so the
Windows path runs on a daemon thread and the caller carries on.
"""

from __future__ import annotations

import sys
import threading

# Loud enough to hear from a text editor, short enough not to be a nuisance.
TONE_HZ = 1000
TONE_MS = 150
REPEATS = 3


def _windows_beep() -> None:
    import winsound

    for _ in range(REPEATS):
        winsound.Beep(TONE_HZ, TONE_MS)


def beep() -> None:
    """Ask for the player's attention. Returns immediately."""
    if sys.platform == "win32":
        threading.Thread(target=_windows_beep, daemon=True).start()
        return
    # `\a` is handled by the terminal itself, so this costs nothing to write.
    print("\a", end="", flush=True)
