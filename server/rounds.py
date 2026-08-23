"""The round state machine: phases, clocks, scoring and troop awards.

Deliberately knows nothing about sockets. It holds the board and the players,
answers moves, and says when a phase is over; the server does the talking. That
split is what lets a whole game be played in a test in milliseconds, with the
clock wound forward by hand.

The loop is:

    submitting -> commanding -> submitting -> ...

Each phase has a deadline. `submitting` also ends early once somebody solves
the problem, after a grace period that gives everyone else a last go, and
either phase ends early once every player still in the game says they are done.

`commanding` is where a round's troops are spent, once. Nothing it is told
happens when it is told: placements go into `plan` and are carried out together
when the phase closes, which is why `place` writes no soldiers onto the board
and why cancelling gives them back rather than taking them off it.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from uuid import UUID

from protocol.rounds import (
    COMMAND_SECONDS,
    GRACE_SECONDS,
    SUBMIT_SECONDS,
    Phase,
    Placement,
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
    "commanding": COMMAND_SECONDS,
}

NEXT_PHASE: dict[Phase, Phase] = {
    "submitting": "commanding",
    "commanding": "submitting",
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


@dataclass(frozen=True)
class Outcome:
    """The game has ended. `winner` is None when nobody was left to win it.

    A win and a mutual wipe-out are the same event as far as the room is
    concerned - the round loop has to stop either way - so they are one type
    rather than a `UUID | None` the caller has to know how to read.
    """

    winner: UUID | None


@dataclass
class Round:
    """The game in progress: a board, a phase, and everyone's standing."""

    board: WorldMap
    judge: Judge
    dealer: ProblemDealer = field(default_factory=ProblemDealer)
    rng: random.Random = field(default_factory=random.Random)

    number: int = 0
    phase: Phase = "submitting"
    # A solo game has no winner to declare, and counting `progress` instead
    # would declare one the moment everybody but the last player disconnected.
    started_with: int = 0
    problem: Problem | None = None
    progress: dict[UUID, Progress] = field(default_factory=dict)

    deadline: float = 0.0
    # Placements for the phase in progress, carried out all at once when it
    # ends. Empty outside the commanding phase.
    plan: combat.Plan = field(default_factory=combat.Plan)
    _grace_started: bool = False
    # Players with a solution in the judge right now. Judging is awaited inside
    # the request, so without this a player holding the submit key queues a
    # sandbox run per keystroke and starves everyone else out of the slots.
    _judging: set[UUID] = field(default_factory=set)

    def start(self, players: dict[UUID, str]) -> None:
        """Open the first round for the given players, by id and name."""
        self.progress = {pid: Progress(player_id=pid, name=name) for pid, name in players.items()}
        self.started_with = len(players)
        self.number = 0
        self._open_round()

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

    async def submit(self, player_id: UUID, code: str) -> Verdict:
        """Judge one solution. Only the player's best attempt is kept."""
        progress = self._require(player_id)
        if self.phase != "submitting":
            raise combat.IllegalMove("solutions are only taken during the submitting phase")
        if self.problem is None:
            raise combat.IllegalMove("no problem has been posed")
        if player_id in self._judging:
            raise combat.IllegalMove("your last solution is still being judged")

        problem = self.problem
        self._judging.add(player_id)
        try:
            verdict = await self.judge.evaluate(problem, code)
        finally:
            self._judging.discard(player_id)

        progress.submissions += 1
        if progress.best is None or verdict.passed > progress.best.passed:
            progress.best = verdict

        if verdict.perfect and progress.solved_at is None:
            progress.solved_at = time.monotonic()
            self._begin_grace(progress)

        return verdict

    def place(self, player_id: UUID, territory: int, count: int) -> int:
        """Promise troops to a territory. Returns how many the player has left.

        Nothing reaches the board here. The placement joins the plan and lands
        when the phase ends, so nobody sees it coming and it can be torn up
        for nothing right up until then.
        """
        progress = self._require(player_id)
        if self.phase != "commanding":
            raise combat.IllegalMove("troops are only placed during the commanding phase")

        combat.check_placement(self.board, player_id, territory, count)
        if count > progress.troops:
            raise combat.IllegalMove(f"you have {progress.troops} troops, not {count}")

        self.plan.place(player_id, territory, count)
        progress.troops -= count
        # Committing troops is a change of mind about being finished.
        progress.done = False
        return progress.troops

    def cancel_plan(self, player_id: UUID) -> None:
        """Tear up the phase: placed troops go back into hand."""
        progress = self._require(player_id)
        progress.troops += self.plan.clear(player_id)
        # As in `place`: a player who has thrown their plan away has something
        # left to decide, and a room should not advance the phase out from
        # under them.
        progress.done = False

    def placements_for(self, player_id: UUID) -> list[Placement]:
        return self.plan.placements_for(player_id)

    def troops_left(self, player_id: UUID) -> int:
        """Troops earned this round and not yet promised to a territory."""
        return self._require(player_id).troops

    def finish(self, player_id: UUID) -> None:
        self._require(player_id).done = True

    def drop(self, player_id: UUID) -> tuple[int, ...]:
        """Forget a player who has disconnected. Returns the ground they held.

        Their territories go neutral rather than staying theirs. Left owned,
        they are ground nobody can ever take a turn with and nobody can win
        past: the last player standing would hold everything that moves and
        still not be the only owner on the board, so the game would never end.

        The garrisons stay where they are. Neutral troops defend - `_settle`
        counts an unowned stack like any other - so a player walking off the
        board does not hand their frontier away for free.
        """
        _ = self.progress.pop(player_id, None)
        # Their plan goes with them: landing troops for somebody who left would
        # put soldiers on the board the room has no opponent to attribute to.
        _ = self.plan.clear(player_id)

        released = tuple(t.id for t in self.board.territories if t.owner == player_id)
        for territory_id in released:
            self.board.territories[territory_id].owner = None
        return released

    def advance(self) -> combat.Resolution | None:
        """Close the current phase and open the next.

        Returns what the plan did, when there was one: the caller has to push
        the battles and the board delta before anyone sees the new phase, or the
        board on screen disagrees with the story told about it.
        """
        resolution: combat.Resolution | None = None
        if self.phase == "submitting":
            self._award_troops()
        elif self.phase == "commanding":
            resolution = combat.resolve(self.board, self.plan, self.names())
            self.plan = combat.Plan()
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
            # Nobody has said anything about a phase that has just opened.
            progress.done = False
        if phase == "commanding":
            # The problem is done with; keeping it would leave it on screen
            # through a phase that has nothing to submit to.
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

        The award replaces what a player is holding rather than adding to it:
        troops earned for a round and never placed in it are gone, so there is
        no saving them up for a turn with nothing else to spend on.
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

    def outcome(self) -> Outcome | None:
        """Whether the game has ended, and who took it. None while it runs.

        Ground held is what counts, not who is still connected: a player whose
        client died is out of the game the moment their last territory falls,
        and one who holds the board has won it whether or not the others are
        still there to watch.
        """
        if self.started_with < 2:
            return None
        standing = {t.owner for t in self.board.territories if t.owner is not None}
        if len(standing) == 1:
            return Outcome(winner=next(iter(standing)))
        if not standing:
            # Every territory neutral. Nobody can place - a placement needs
            # ground of your own or ground beside it - so there is no position
            # left to play out.
            return Outcome(winner=None)
        return None

    def _require(self, player_id: UUID) -> Progress:
        progress = self.progress.get(player_id)
        if progress is None:
            raise combat.IllegalMove("you are not in this game")
        return progress
