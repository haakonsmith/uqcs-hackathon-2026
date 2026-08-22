"""A round, as both ends see it.

A round is two phases on a clock the server owns:

    submitting -> commanding -> submitting -> ...

`submitting` poses a programming problem and takes solutions, as many attempts
as a player wants. It ends after `SUBMIT_SECONDS`, or `GRACE_SECONDS` after the
first perfect solution lands - whoever solves it first sets a deadline for
everybody else rather than ending the phase from under them.

`commanding` is a player's whole turn on the board: the troops those solutions
earned go onto owned territories or onto ground bordering them - reinforcing
the first, assaulting the second. It ends early once every player says they are
done, so a room that is ready does not sit watching a clock.

Placing is the only way troops reach the board, and the only way ground changes
hands. Troops already standing stay where they are, so each round is a decision
about where to spend that round's award rather than about redeploying what is
already down. There is one round of allocation per round of play.

A placement does not happen when it is asked for. It is held as a plan and
carried out with everybody else's when the phase ends, which is what makes two
players able to commit to the same territory on the same turn, and it means
nobody sees anybody else's reinforcements coming: until the phase ends a
placement shows only on its own player's screen, as a `(+n)` beside the
garrison it will join.

Deadlines travel as `seconds_left` rather than as timestamps: the two ends have
no common clock, and a countdown that disagrees with the server by the client's
clock skew is worse than one that drifts by the network latency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# How long a phase runs before the server moves everyone on regardless.
SUBMIT_SECONDS = 60.0
# Long enough to look at the board and decide where the round's troops go,
# which is the only decision this phase asks for.
COMMAND_SECONDS = 150.0

# Once somebody solves the problem, how long everyone else gets to finish.
# Necessarily well short of `SUBMIT_SECONDS`: the grace only ever shortens the
# deadline, so a value at or above the phase length would never shorten
# anything and solving first would stop meaning anything at all.
GRACE_SECONDS = 15.0

type Phase = Literal["submitting", "commanding"]

PHASE_ORDER: tuple[Phase, ...] = ("submitting", "commanding")


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


type CaseStatus = Literal["passed", "wrong", "timeout", "crashed", "flooded"]

# What each status is called on screen, kept here so both ends agree.
CASE_LABELS: dict[CaseStatus, str] = {
    "passed": "passed",
    "wrong": "wrong answer",
    "timeout": "timed out",
    "crashed": "crashed",
    "flooded": "printed too much",
}


@dataclass(frozen=True)
class CaseResult:
    """How one hidden test case went.

    Carries the player's own stderr when their code crashed - that is their
    traceback and telling them is the whole point - but never the expected
    output, which would turn one wrong submission into a correct one.
    """

    index: int
    status: CaseStatus
    detail: str = ""
    milliseconds: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def label(self) -> str:
        return CASE_LABELS.get(self.status, self.status)


@dataclass(frozen=True)
class Verdict:
    """How one submission did against the hidden tests."""

    passed: int
    total: int
    error: str | None = None
    # One per hidden case, in order. Empty from a judge that does not run them.
    cases: list[CaseResult] = field(default_factory=list)

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
    # Earned by the last submitting phase and not yet promised to a territory.
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
class Placement:
    """Troops of this round's award promised to a territory already owned.

    One per territory rather than one per request: placing twice on the same
    ground is one reinforcement of `count`, not two the client has to add up.
    """

    territory: int
    count: int


@dataclass(frozen=True)
class Stack:
    """One player's troops standing in a territory when the dust settles."""

    owner: str | None
    name: str
    troops: int


@dataclass(frozen=True)
class DroppedOrder:
    """Something committed this phase that could not be carried out.

    A plan is checked when it is given and carried out a phase later, so a
    promise can stop being legal in between - ground that was within reach may
    have changed hands first. Those troops are simply gone, and a player who is
    not told reads it as the game losing their plan.
    """

    player: str | None
    name: str
    territory: int
    count: int
    reason: str


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
