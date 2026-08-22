"""The round state machine: phases, clocks, scoring and troop awards.

Deliberately knows nothing about sockets. It holds the board and the players,
answers moves, and says when a phase is over; the server does the talking. That
split is what lets a whole game be played in a test in milliseconds, with the
clock wound forward by hand.

The loop is:

    submitting -> allocating -> moving -> submitting -> ...

Each phase has a deadline. `submitting` also ends early once somebody solves
the problem, after a grace period that gives everyone else a last go, and any
phase ends early once every player still in the game says they are done.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from uuid import UUID

from protocol.rounds import (
    ALLOCATE_SECONDS,
    GRACE_SECONDS,
    MOVE_SECONDS,
    SUBMIT_SECONDS,
    MoveOrder,
    Phase,
    PlayerRound,
    Problem,
    RoundState,
    TerritoryUpdate,
    Verdict,
)
from protocol.terrain import WorldMap
from server import combat
from server.judge import Judge
from server.problems import ProblemDealer

logger = logging.getLogger("Rounds")

PHASE_SECONDS: dict[Phase, float] = {
    "submitting": SUBMIT_SECONDS,
    "allocating": ALLOCATE_SECONDS,
    "moving": MOVE_SECONDS,
}

NEXT_PHASE: dict[Phase, Phase] = {
    "submitting": "allocating",
    "allocating": "moving",
    "moving": "submitting",
}

# Troops for turning up, before anything is earned by solving.
BASE_TROOPS = 3
# One extra per this many territories held, as in Risk.
TERRITORIES_PER_TROOP = 3
# The most a perfect solution is worth, scaled down by how much passed.
SOLUTION_TROOPS = 5
# On top of that, for being first to a perfect solution.
FIRST_BLOOD_TROOPS = 3


@dataclass
class Progress:
    """One player's mutable state for the round in progress."""

    player_id: UUID
    name: str
    submissions: int = 0
    best: Verdict | None = None
    troops: int = 0
    done: bool = False
    solved_at: float | None = None

    def snapshot(self) -> PlayerRound:
        return PlayerRound(
            player_id=str(self.player_id),
            name=self.name,
            submissions=self.submissions,
            best=self.best,
            troops=self.troops,
            done=self.done,
        )


@dataclass
class Round:
    """The game in progress: a board, a phase, and everyone's standing."""

    board: WorldMap
    judge: Judge
    dealer: ProblemDealer = field(default_factory=ProblemDealer)
    rng: random.Random = field(default_factory=random.Random)

    number: int = 0
    phase: Phase = "submitting"
    problem: Problem | None = None
    progress: dict[UUID, Progress] = field(default_factory=dict)

    deadline: float = 0.0
    # Marching orders for the phase in progress, resolved all at once when
    # it ends. Empty outside the moving phase.
    orders: combat.Orders = field(default_factory=combat.Orders)
    _grace_started: bool = False

    def start(self, players: dict[UUID, str]) -> None:
        """Open the first round for the given players, by id and name."""
        self.progress = {pid: Progress(player_id=pid, name=name) for pid, name in players.items()}
        self.number = 0
        self._open_round()

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------

    def seconds_left(self, now: float | None = None) -> float:
        return max(0.0, self.deadline - (now if now is not None else time.monotonic()))

    def expired(self, now: float | None = None) -> bool:
        return self.seconds_left(now) <= 0.0

    def everyone_done(self) -> bool:
        """True when nobody still in the game has anything left to say.

        A player with no territories left is counted as done: there is nothing
        for them to place or move, and waiting on them would stall the room.
        """
        live = [p for p in self.progress.values() if not self.is_out(p.player_id)]
        return bool(live) and all(p.done for p in live)

    def should_advance(self, now: float | None = None) -> bool:
        return self.expired(now) or self.everyone_done()

    def is_out(self, player_id: UUID) -> bool:
        return combat.eliminated(self.board, player_id)

    # ------------------------------------------------------------------
    # Moves
    # ------------------------------------------------------------------

    async def submit(self, player_id: UUID, code: str) -> Verdict:
        """Judge one solution. Only the player's best attempt is kept."""
        progress = self._require(player_id)
        if self.phase != "submitting":
            raise combat.IllegalMove("solutions are only taken during the submitting phase")
        if self.problem is None:
            raise combat.IllegalMove("no problem has been posed")

        verdict = await self.judge.evaluate(self.problem, code)
        progress.submissions += 1
        if progress.best is None or verdict.passed > progress.best.passed:
            progress.best = verdict

        if verdict.perfect and progress.solved_at is None:
            progress.solved_at = time.monotonic()
            self._begin_grace(progress)

        return verdict

    def place(self, player_id: UUID, territory: int, count: int) -> int:
        """Put troops on the board. Returns how many the player has left."""
        progress = self._require(player_id)
        if self.phase != "allocating":
            raise combat.IllegalMove("troops are only placed during the allocating phase")
        if count < 1:
            raise combat.IllegalMove("place at least one soldier")
        if count > progress.troops:
            raise combat.IllegalMove(f"you have {progress.troops} troops, not {count}")

        combat.place(self.board, player_id, territory, count)
        progress.troops -= count
        if progress.troops == 0:
            progress.done = True
        return progress.troops

    def order(self, player_id: UUID, source: int, target: int, count: int) -> list[MoveOrder]:
        """Queue a march. Nothing moves until the phase ends.

        Returns this player's orders so far, so a client can show the plan it
        has committed to without keeping its own copy in step.
        """
        progress = self._require(player_id)
        if self.phase != "moving":
            raise combat.IllegalMove("troops are only moved during the moving phase")

        order = MoveOrder(source=source, target=target, count=count)
        combat.validate(self.board, self.orders, player_id, order)
        self.orders.add(player_id, order)
        # Giving an order is a change of mind about being finished.
        progress.done = False
        return self.orders.for_player(player_id)

    def cancel_orders(self, player_id: UUID) -> list[MoveOrder]:
        _ = self._require(player_id)
        self.orders.clear(player_id)
        return []

    def orders_for(self, player_id: UUID) -> list[MoveOrder]:
        return self.orders.for_player(player_id)

    def finish(self, player_id: UUID) -> None:
        self._require(player_id).done = True

    def drop(self, player_id: UUID) -> None:
        """Forget a player who has disconnected, so nobody waits on them."""
        _ = self.progress.pop(player_id, None)

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------

    def advance(self) -> combat.Resolution | None:
        """Close the current phase and open the next.

        Returns what the marching orders did, when there were any: the caller
        has to push the battles and the board delta before anyone sees the new
        phase, or the board on screen disagrees with the story told about it.
        """
        resolution: combat.Resolution | None = None
        if self.phase == "submitting":
            self._award_troops()
        elif self.phase == "moving":
            resolution = combat.resolve(self.board, self.orders, self.names())
            self.orders = combat.Orders()
            for battle in resolution.battles:
                logger.info(battle.summary())

        next_phase = NEXT_PHASE[self.phase]
        if next_phase == "submitting":
            self._open_round()
        else:
            self._open_phase(next_phase)
        return resolution

    def names(self) -> dict[UUID, str]:
        return {pid: progress.name for pid, progress in self.progress.items()}

    def _open_round(self) -> None:
        self.number += 1
        self.problem = self.dealer.next()
        for progress in self.progress.values():
            progress.submissions = 0
            progress.best = None
            progress.solved_at = None
        self._grace_started = False
        self._open_phase("submitting")
        logger.info(f"round {self.number} open: {self.problem.title}")

    def _open_phase(self, phase: Phase) -> None:
        self.phase = phase
        self.deadline = time.monotonic() + PHASE_SECONDS[phase]
        for progress in self.progress.values():
            # A player with nothing to place is done before the phase starts,
            # or an all-solved room would sit through the whole allocation
            # clock waiting for troops nobody has.
            progress.done = phase == "allocating" and progress.troops == 0
        if phase == "allocating":
            # The problem is done with; keeping it would leave it on screen
            # through phases that have nothing to submit to.
            self.problem = None

    def _begin_grace(self, solver: Progress) -> None:
        """First perfect solution shortens the phase rather than ending it."""
        if self._grace_started:
            return
        self._grace_started = True
        self.deadline = min(self.deadline, time.monotonic() + GRACE_SECONDS)
        logger.info(f"{solver.name} solved it - {GRACE_SECONDS:.0f}s grace for everyone else")

    def _award_troops(self) -> None:
        """Turn the round's solutions into troops to place.

        Everyone gets a base and a share for ground held, so a bad round is a
        setback rather than an elimination; the solution is what makes the
        difference between players.
        """
        first = min(
            (p for p in self.progress.values() if p.solved_at is not None),
            key=lambda p: p.solved_at or 0.0,
            default=None,
        )

        for progress in self.progress.values():
            if self.is_out(progress.player_id):
                progress.troops = 0
                continue

            held = len(self.board.owned_by(progress.player_id))
            troops = BASE_TROOPS + held // TERRITORIES_PER_TROOP
            if progress.best is not None:
                troops += round(SOLUTION_TROOPS * progress.best.ratio)
            if first is progress:
                troops += FIRST_BLOOD_TROOPS
            progress.troops = troops
            logger.info(f"{progress.name} earned {troops} troops ({held} territories)")

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    def state(self) -> RoundState:
        return RoundState(
            number=self.number,
            phase=self.phase,
            seconds_left=self.seconds_left(),
            players=[p.snapshot() for p in self.progress.values()],
            problem=self.problem,
        )

    def updates(self, territory_ids: tuple[int, ...]) -> list[TerritoryUpdate]:
        out: list[TerritoryUpdate] = []
        for territory_id in territory_ids:
            territory = self.board.territories[territory_id]
            owner = str(territory.owner) if territory.owner is not None else None
            out.append(TerritoryUpdate(id=territory_id, owner=owner, soldiers=territory.soldiers))
        return out

    def winner(self) -> UUID | None:
        """The last player holding ground, once everyone else is out."""
        standing = {t.owner for t in self.board.territories if t.owner is not None}
        if len(standing) == 1 and len(self.progress) > 1:
            return next(iter(standing))
        return None

    def _require(self, player_id: UUID) -> Progress:
        progress = self.progress.get(player_id)
        if progress is None:
            raise combat.IllegalMove("you are not in this game")
        return progress
