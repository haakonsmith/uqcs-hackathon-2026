"""Dropping out to the player's own editor to write a solution.

Writing the code is the game, so it happens in the editor the player already
knows rather than in a half-built one bolted to this client. Press the key, the
board gets out of the way, the editor opens on the draft, and what was written
goes to the server when the player is done with it.

There are two ways that goes. A terminal editor - `$VISUAL`, `$EDITOR`, or
whichever of vi's descendants is installed - is run as a child and takes over
the terminal, and quitting it submits. Where there is no such thing, which is
an ordinary Windows machine, the OS's own "Open with" chooser is raised on the
draft instead and the file itself is watched: saving submits, because a
windowed editor is not something this process can wait on the way it waits on
a child vi.

The draft is one file per session, so reopening picks up where the last attempt
left off - a round is several goes at the same problem, and starting from blank
every time would be its own punishment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import blessed

from client.input import KeyPump, suspended

logger = logging.getLogger("client")

# Tried in order when neither $VISUAL nor $EDITOR says otherwise. `vi` last
# because POSIX requires it, so it is the one that is always there.
FALLBACK_EDITORS = ("nvim", "vim", "vi")

# The chooser on a Linux desktop. `xdg-open` would be no use: it picks the
# handler itself, and the point here is to let the player pick.
LINUX_CHOOSER = "mimeopen"

# How often the draft is looked at while a windowed editor has it. Short
# enough that saving feels like it did something, long enough to be free.
WATCH_INTERVAL = 0.3

# What the watcher can conclude: the player saved, wants a different
# application, or wants the board back without submitting anything.
type Waited = Literal["saved", "reopen", "cancelled"]


class NoEditor(Exception):
    """Nothing on this machine can be used to write a solution."""


def terminal_editor() -> list[str] | None:
    """The editor to run in this terminal, as argv, or None if there is none.

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
    return None


def chooser(path: Path) -> list[str]:
    """Argv that raises the OS's "Open with" dialog on `path`.

    Each platform's own chooser, rather than a list of applications to try:
    whatever this player writes Python in is registered with the OS already,
    and guessing at notepad.exe would be wrong for everyone who has anything
    better installed.

    Raises `NoEditor` when there is no chooser to raise either, which leaves a
    machine with nothing at all to write in.
    """
    system = platform.system()
    if system == "Windows":
        # The shell's own "Open with" dialog. rundll32 exits when it is
        # dismissed, whether or not it opened anything.
        return ["rundll32.exe", "shell32.dll,OpenAs_RunDLL", str(path)]
    if system == "Darwin":
        # `choose application` is the Finder's picker, and opening "using" its
        # answer hands the file to whatever was picked.
        script = f'tell application "Finder" to open (POSIX file "{path}") using (choose application)'
        return ["osascript", "-e", script]

    found = shutil.which(LINUX_CHOOSER)
    if found is None:
        raise NoEditor(f"no $EDITOR set, none of {', '.join(FALLBACK_EDITORS)} installed, and no {LINUX_CHOOSER} to pick something with")
    return [found, "-d", str(path)]


class Draft:
    """The scratch file a player's solution lives in for this session."""

    def __init__(self, initial: str = "", suffix: str = ".py") -> None:
        handle, name = tempfile.mkstemp(prefix="termination-", suffix=suffix)
        os.close(handle)
        self.path: Path = Path(name)
        # Whether an application has already been chosen for this file. The
        # chooser is only worth raising once: the application it opened still
        # has the draft when the player comes back for a second attempt, and a
        # second window on the same file is how edits get lost.
        self.chosen: bool = False
        if initial:
            _ = self.path.write_text(initial)

    def reset(self, initial: str = "") -> None:
        """Put a fresh problem in the draft, keeping the same file.

        Rewritten rather than replaced. The windowed path hands this path to an
        application that goes on holding it for the rest of the session, so a
        new file would leave that application editing the old one, and deleting
        this one would pull it out from under them. Writing through the same
        path is also what lets an editor notice the change and offer to reload.
        """
        try:
            _ = self.path.write_text(initial)
        except OSError as error:
            logger.warning("could not reset the draft: %s", error)

    def read(self) -> str:
        try:
            return self.path.read_text()
        except OSError as error:
            logger.warning("could not read the draft back: %s", error)
            return ""

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


async def edit(term: blessed.Terminal, pump: KeyPump, draft: Draft) -> str | None:
    """Open the draft in the player's editor and return what they saved.

    None means nothing was saved and nothing should be submitted. Only the
    windowed path can say that: quitting a terminal editor submits whatever is
    in the file, whether it was saved this time or last.

    The pump is stopped either way. Leaving it running would have two threads
    reading one terminal, and the editor would lose every other keystroke to
    this process.
    """
    argv = terminal_editor()
    if argv is not None:
        return await _edit_in_terminal(term, pump, draft, [*argv, str(draft.path)])
    return await _edit_in_application(term, pump, draft)


async def _edit_in_terminal(term: blessed.Terminal, pump: KeyPump, draft: Draft, argv: list[str]) -> str:
    """Run a full-screen editor on the draft, and submit when it quits.

    Run as a child process rather than blocking the loop, so the websocket
    keeps being read while somebody is typing - a round has a clock, and a
    connection that times out mid-solution would lose the round with it.
    """
    logger.info("opening %s", " ".join(argv))

    with pump.paused(), suspended(term):
        process = await asyncio.create_subprocess_exec(*argv)
        code = await process.wait()

    if code != 0:
        logger.warning("%s exited with %s", argv[0], code)
    return draft.read()


async def _edit_in_application(term: blessed.Terminal, pump: KeyPump, draft: Draft) -> str | None:
    """Hand the draft to an application the player picks, and watch the file.

    Waiting for the process to exit, the way the terminal path does, would not
    work here. The chooser returns as soon as it has handed the file over, and
    the application it opened is often one window of an editor that was
    already running and will outlive the round. What a player does that means
    "try this" is save, so a save is what is waited for.
    """
    argv = chooser(draft.path)

    with pump.paused(), suspended(term):
        while True:
            # Read before the chooser runs, so a save made while the dialog
            # was still up counts as one.
            before = _stamp(draft.path)
            _explain(term, draft.path, choosing=not draft.chosen)
            if not draft.chosen:
                await _raise_chooser(argv)

            match await _watch(term, draft.path, before):
                case "saved":
                    # Only now is the application known to be open on the
                    # draft. Marking it as chosen when the dialog went up
                    # instead would leave a player who dismissed it without
                    # picking anything with no way back to it but [o].
                    draft.chosen = True
                    return draft.read()
                case "reopen":
                    draft.chosen = False
                case "cancelled":
                    return None


def _explain(term: blessed.Terminal, path: Path, choosing: bool) -> None:
    """Say what is happening, on the terminal the board has just let go of."""
    lead = (
        [
            "Nothing here can edit a solution in the terminal - no $VISUAL or",
            "$EDITOR, and no vi - so the choice is yours: the OS is about to ask",
            "which application should open your solution.",
        ]
        if choosing
        else ["Your solution is still open in the application you chose."]
    )
    tail = [
        "Save it there and it goes straight to the server, tests and all.",
        "",
        "    [o]    choose a different application",
        "    [esc]  back to the board without submitting",
    ]
    print(term.home + term.clear, end="")
    print("\n".join([*lead, "", f"    {path}", "", *tail]), flush=True)


async def _raise_chooser(argv: list[str]) -> None:
    """Put the dialog up and wait for it to be answered.

    Waited on rather than left running, because one of these choosers -
    `mimeopen -d` - asks its question on the terminal that was just given
    back, and the watcher below would be reading the keyboard out from under
    it.
    """
    logger.info("asking which application to use: %s", " ".join(argv))
    process = await asyncio.create_subprocess_exec(*argv)
    code = await process.wait()
    if code != 0:
        # Dismissing the dialog is a non-zero exit on some of these and is not
        # an error. Whether anything came of it is the watcher's business.
        logger.info("%s exited with %s", argv[0], code)


async def _watch(term: blessed.Terminal, path: Path, before: tuple[int, int] | None) -> Waited:
    """Wait for the draft to be saved, or for the player to give up on it."""
    while True:
        stamp = _stamp(path)
        if stamp is not None and stamp != before and _unlocked(path):
            logger.info("the draft was saved")
            return "saved"

        # timeout=0, so this takes only what is already buffered: `inkey`
        # blocks the thread it is called on, and this one has a websocket to
        # keep reading while somebody types.
        key = term.inkey(timeout=0)
        if key:
            if key.name == "KEY_ESCAPE" or key.lower() == "q":
                return "cancelled"
            if key.lower() == "o":
                return "reopen"

        await asyncio.sleep(WATCH_INTERVAL)


def _stamp(path: Path) -> tuple[int, int] | None:
    """Modification time and size, or None if the file is not there.

    Both, because plenty of editors save by writing a new file and renaming it
    over this one, and the path can be briefly missing while they do - which
    is also why a stamp of None is never read as a save.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def _unlocked(path: Path) -> bool:
    """Whether the file can be opened for writing, so nobody else holds it.

    Windows editors take the file exclusively while they write it, and reading
    during that window submits half a solution. Opened for append and written
    to with nothing, so asking cannot itself look like a save.
    """
    try:
        with path.open("a"):
            return True
    except OSError:
        return False
