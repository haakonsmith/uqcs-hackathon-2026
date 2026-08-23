"""The waiting room, drawn over the menu until the server starts the game.

Same queue-and-`match` shape as `run_menu` and `play()`, so keystrokes here
travel the path they do everywhere else. The roster is never patched locally:
it is replaced wholesale from whatever the server last sent, which is the only
copy that can be trusted once more than one player is moving.
"""

from __future__ import annotations

import asyncio

import blessed

from client.input import AppEvent, KeyPress, Received, Resized
from client.menu import notice
from client.sound import beep
from protocol import Connection, GameStarted, Lobby, LobbyChanged, SetReady, World

# Room enough for a name and its status without the panel jumping about as
# players come and go.
NAME_WIDTH = 18


def _lines(lobby: Lobby, me: str) -> list[str]:
    rows = ["=== LOBBY ===", ""]
    for player in lobby.players:
        mark = "READY" if player.ready else "..."
        name = player.name if player.id != me else f"{player.name} (you)"
        rows.append(f"{name[:NAME_WIDTH]:<{NAME_WIDTH}} {mark}")

    for _ in range(lobby.capacity - len(lobby.players)):
        rows.append(f"{'- empty -':<{NAME_WIDTH}}")

    rows += ["", lobby.waiting_for(), "", "[space] ready up   [esc] leave"]
    return rows


def _am_ready(lobby: Lobby, me: str) -> bool:
    return any(player.ready for player in lobby.players if player.id == me)


async def run_lobby(
    term: blessed.Terminal,
    events: asyncio.Queue[AppEvent],
    conn: Connection,
    lobby: Lobby,
    me: str,
) -> tuple[World, Lobby] | None:
    """Wait for everyone to ready up. None if the player left instead.

    Returns the board and the roster it was dealt to: the board carries player
    ids but no names, so whoever draws it needs the last roster the server sent
    to put a name to a colour.

    `lobby` is the roster that came back on the join, so the screen is correct
    on its first frame rather than blank until the first push.
    """
    notice(term, *_lines(lobby, me))

    while True:
        match await events.get():
            case Resized():
                pass
            case Received(event=GameStarted(world=world)):
                beep()
                return world, lobby
            case Received(event=LobbyChanged(lobby=updated)):
                lobby = updated
            case Received():
                continue
            case KeyPress(key=key):
                name = key.name or ""
                if name == "KEY_ESCAPE" or key.lower() == "q":
                    return None
                if key != " " and name not in ("KEY_ENTER", "KEY_RETURN"):
                    continue
                # The answer carries the roster the toggle produced, so the
                # screen never shows a ready flag the server has not recorded.
                lobby = (await conn.send(SetReady(ready=not _am_ready(lobby, me)))).lobby

        notice(term, *_lines(lobby, me))
