"""The waiting room, as both ends see it.

The server sends the whole roster on every change rather than a join/leave
delta. A roster is small, and a client that rebuilds it from deltas is one
dropped or reordered event away from showing a player who has already left.

Layered below `actions`, which puts a `Lobby` in the join response and in the
event that announces every change to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
