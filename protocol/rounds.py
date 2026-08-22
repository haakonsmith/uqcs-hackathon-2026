"""A round, as both ends see it.

A round is three phases on a clock the server owns:

    submitting -> allocating -> moving -> submitting -> ...

`submitting` poses a programming problem and takes solutions, as many attempts
as a player wants. It ends after `SUBMIT_SECONDS`, or `GRACE_SECONDS` after the
first perfect solution lands - whoever solves it first sets a deadline for
everybody else rather than ending the phase from under them.

`allocating` hands out the troops those solutions earned, to place on owned
territories. `moving` takes marching orders and resolves them all at once when
the phase ends. Both end early once every player says they are done, so a room
that is ready does not sit watching a clock.

Orders are collected and resolved together rather than applied as they arrive,
because that is what makes two players able to march into the same territory on
the same turn. Nobody sees anybody else's orders until they land.

Deadlines travel as `seconds_left` rather than as timestamps: the two ends have
no common clock, and a countdown that disagrees with the server by the client's
clock skew is worse than one that drifts by the network latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# How long a phase runs before the server moves everyone on regardless.
SUBMIT_SECONDS = 300.0
ALLOCATE_SECONDS = 90.0
MOVE_SECONDS = 120.0

# Once somebody solves the problem, how long everyone else gets to finish.
GRACE_SECONDS = 60.0

type Phase = Literal["submitting", "allocating", "moving"]

PHASE_ORDER: tuple[Phase, ...] = ("submitting", "allocating", "moving")


@dataclass(frozen=True)
class Problem:
    """A programming problem, as posed to the players.

    The test cases are deliberately absent: they stay on the server, so the
    only way to find out whether a solution passes is to submit it.
    """

    id: str
    title: str
    statement: str
    signature: str
    examples: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    """How one submission did against the hidden tests."""

    passed: int
    total: int
    error: str | None = None

    @property
    def perfect(self) -> bool:
        return self.total > 0 and self.passed == self.total

    @property
    def ratio(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def summary(self) -> str:
        if self.error:
            return f"{self.passed}/{self.total} - {self.error}"
        return f"{self.passed}/{self.total} tests passed"


@dataclass(frozen=True)
class PlayerRound:
    """One player's progress through the current round."""

    player_id: str
    name: str
    submissions: int = 0
    best: Verdict | None = None
    # Earned by the last submitting phase, still waiting to be placed.
    troops: int = 0
    # Said they are finished with the phase that is running now.
    done: bool = False

    @property
    def solved(self) -> bool:
        return self.best is not None and self.best.perfect


@dataclass(frozen=True)
class RoundState:
    """Everything a client needs to draw the round it is in."""

    number: int
    phase: Phase
    seconds_left: float
    players: list[PlayerRound] = field(default_factory=list)
    # Only during `submitting`; there is nothing to solve in the other phases.
    problem: Problem | None = None

    def player(self, player_id: str) -> PlayerRound | None:
        return next((p for p in self.players if p.player_id == player_id), None)

    def waiting_on(self) -> int:
        """How many players have yet to say they are done."""
        return sum(1 for player in self.players if not player.done)


@dataclass(frozen=True)
class TerritoryUpdate:
    """One territory changed hands, or changed garrison.

    Sent instead of the whole board: a board is around 90 KB on the wire and a
    round produces a handful of these.
    """

    id: int
    owner: str | None
    soldiers: int


@dataclass(frozen=True)
class MoveOrder:
    """Troops ordered from one territory to a neighbouring one."""

    source: int
    target: int
    count: int


@dataclass(frozen=True)
class Stack:
    """One player's troops standing in a territory when the dust settles."""

    owner: str | None
    name: str
    troops: int


@dataclass(frozen=True)
class BattleReport:
    """Two or more players ended the phase in the same territory.

    The biggest stack takes the ground and loses as many troops as the next
    biggest had. A tie at the top wipes both out and leaves the territory
    unowned, which is the only outcome where nobody walks away with it.
    """

    territory: int
    stacks: list[Stack] = field(default_factory=list)
    winner: str | None = None
    winner_name: str = ""
    survivors: int = 0

    def summary(self) -> str:
        sides = ", ".join(f"{s.name} {s.troops}" for s in self.stacks)
        if self.winner is None:
            return f"territory {self.territory}: {sides} - wiped out, nobody holds it"
        return f"territory {self.territory}: {sides} - {self.winner_name} holds it with {self.survivors}"
