"""The waiting room, as both ends see it.

The server sends the whole roster on every change rather than a join/leave
delta. A roster is small, and a client that rebuilds it from deltas is one
dropped or reordered event away from showing a player who has already left.

Layered below `actions`, which puts a `Lobby` in the join response and in the
event that announces every change to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The longest name a player may take. Chosen against the scoreboard, which is
# the narrowest place a name has to fit and the one where a run-on name would
# push the columns beside it off the panel.
MAX_NAME_LENGTH = 16


def clean_name(name: str) -> str:
    """A name as it will actually be shown, or "" if nothing usable is left.

    Both ends call this, because both need the same answer: the client stops a
    player typing past it, and the server refuses whatever a client that did
    not bother sends anyway. Trusting the client here means one player can
    decide how everybody else's screen is laid out.

    Whitespace is collapsed and unprintable characters are dropped rather than
    rejected. A name is drawn into a fixed-width row, so a tab or a newline in
    one does not make it long, it makes it a name that eats the rows below;
    and a terminal renders an escape sequence as an instruction rather than as
    text. Neither is something a player types on purpose, so neither is worth
    turning them away for.
    """
    # Whitespace first: `split` is what turns a newline or a tab into a word
    # break. Dropping unprintables before it would weld the words either side
    # of one together instead.
    collapsed = " ".join(name.split())
    printable = "".join(ch for ch in collapsed if ch.isprintable())
    # `rstrip` because the cut can land on a space, and a name that renders
    # with a trailing gap looks like the column is misaligned.
    return printable[:MAX_NAME_LENGTH].rstrip()


@dataclass(frozen=True)
class LobbyPlayer:
    """One player in the waiting room."""

    id: str
    name: str
    ready: bool = False


@dataclass(frozen=True)
class Lobby:
    """Everyone waiting, and what it would take to start.

    `minimum` and `capacity` travel with the roster so the client can say what
    it is waiting for without hard-coding the server's rules.
    """

    players: list[LobbyPlayer] = field(default_factory=list)
    minimum: int = 2
    capacity: int = 4

    @property
    def ready_count(self) -> int:
        return sum(1 for player in self.players if player.ready)

    @property
    def can_start(self) -> bool:
        """Enough players, and every one of them ready."""
        return len(self.players) >= self.minimum and self.ready_count == len(self.players)

    def waiting_for(self) -> str:
        """What is still missing, phrased for a player staring at the screen."""
        missing = self.minimum - len(self.players)
        if missing > 0:
            return f"waiting for {missing} more player{'s' if missing > 1 else ''}"
        not_ready = len(self.players) - self.ready_count
        if not_ready:
            return f"waiting on {not_ready} player{'s' if not_ready > 1 else ''} to ready up"
        return "starting ..."
