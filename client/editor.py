"""Dropping out to the player's own editor to write a solution.

Writing the code is the game, so it happens in the editor the player already
knows rather than in a half-built one bolted to this client. Press the key, the
board gets out of the way, `$EDITOR` opens on the draft, and quitting the
editor submits whatever was saved.

The draft is one file per session, so reopening picks up where the last attempt
left off - a round is several goes at the same problem, and starting from blank
every time would be its own punishment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

import blessed

from client.input import KeyPump, suspended

logger = logging.getLogger("client")

# Tried in order when neither $VISUAL nor $EDITOR says otherwise. `vi` last
# because POSIX requires it, so it is the one that is always there.
FALLBACK_EDITORS = ("nvim", "vim", "vi")


class NoEditor(Exception):
    """Nothing on this machine can be used to write a solution."""


def resolve_editor() -> list[str]:
    """The editor command, as argv. Raises `NoEditor` if there is none.

    `$VISUAL` before `$EDITOR` is the old convention for exactly this case: a
    full-screen program on a terminal that can handle one.
    """
    for variable in ("VISUAL", "EDITOR"):
        value = os.environ.get(variable, "").strip()
        if value:
            # Split, so EDITOR="code --wait" works rather than being looked up
            # as a single impossible filename.
            return value.split()

    for candidate in FALLBACK_EDITORS:
        found = shutil.which(candidate)
        if found:
            return [found]

    raise NoEditor("no $EDITOR set, and none of " + ", ".join(FALLBACK_EDITORS) + " is installed")


class Draft:
    """The scratch file a player's solution lives in for this session."""

    def __init__(self, initial: str = "", suffix: str = ".py") -> None:
        handle, name = tempfile.mkstemp(prefix="termination-", suffix=suffix)
        os.close(handle)
        self.path: Path = Path(name)
        if initial:
            _ = self.path.write_text(initial)

    def read(self) -> str:
        try:
            return self.path.read_text()
        except OSError as error:
            logger.warning("could not read the draft back: %s", error)
            return ""

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


async def edit(term: blessed.Terminal, pump: KeyPump, draft: Draft) -> str:
    """Open the draft in the player's editor and return what they saved.

    The pump is stopped for the duration. Leaving it running would have two
    threads reading one terminal, and the editor would lose every other
    keystroke to this process.

    Run as a child process rather than blocking the loop, so the websocket
    keeps being read while somebody is typing - a round has a clock, and a
    connection that times out mid-solution would lose the round with it.
    """
    argv = [*resolve_editor(), str(draft.path)]
    logger.info("opening %s", " ".join(argv))

    with pump.paused(), suspended(term):
        process = await asyncio.create_subprocess_exec(*argv)
        code = await process.wait()

    if code != 0:
        logger.warning("%s exited with %s", argv[0], code)
    return draft.read()
