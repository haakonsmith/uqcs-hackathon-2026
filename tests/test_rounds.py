"""The round loop: phases, awards, and the ways a game ends.

`Round` takes its judge as an argument, so all of this runs without a socket
or a sandbox. The cases that matter most are the ones about players leaving:
a game that cannot end is worse than one that ends wrongly, because nobody in
it can tell the difference between waiting and being stuck.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from protocol.rounds import Verdict
from server import combat
from server.judge import PlaceholderJudge
from server.rounds import BASE_TROOPS, FIRST_BLOOD_TROOPS, SOLUTION_TROOPS, Outcome, Round
from tests.conftest import board


class StubJudge:
    """Answers with whatever the test wants, and counts what it was asked."""

    def __init__(self, verdict: Verdict | None = None) -> None:
        self.verdict = verdict or Verdict(passed=1, total=1)
        self.calls = 0

    async def evaluate(self, problem: object, code: str) -> Verdict:
        self.calls += 1
        return self.verdict


def game(alice: UUID, bob: UUID, judge: object | None = None) -> Round:
    world = board((alice, 3), (bob, 3))
    round_ = Round(board=world, judge=judge or PlaceholderJudge())  # pyright: ignore[reportArgumentType]
    round_.start({alice: "alice", bob: "bob"})
    return round_


@pytest.fixture
def alice() -> UUID:
    return uuid4()


@pytest.fixture
def bob() -> UUID:
    return uuid4()


# --------------------------------------------------------------------------
# Ending the game
# --------------------------------------------------------------------------


def test_a_game_in_progress_has_no_outcome(alice: UUID, bob: UUID) -> None:
    assert game(alice, bob).outcome() is None


def test_holding_every_territory_wins(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.board.territories[1].owner = alice
    assert round_.outcome() == Outcome(winner=alice)


def test_a_player_who_leaves_releases_their_ground(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    assert round_.drop(bob) == (1,)
    assert round_.board.territories[1].owner is None
    assert round_.board.territories[1].soldiers == 3, "a leaver's garrison still defends"


def test_the_last_player_left_wins_rather_than_waiting_forever(alice: UUID, bob: UUID) -> None:
    """The deadlock: a leaver's territories used to stay theirs, so the board
    kept two owners and `outcome` never fired, whoever held what."""
    round_ = game(alice, bob)
    _ = round_.drop(bob)
    assert round_.outcome() == Outcome(winner=alice)


def test_a_solo_game_is_never_over(alice: UUID) -> None:
    world = board((alice, 3))
    round_ = Round(board=world, judge=PlaceholderJudge())
    round_.start({alice: "alice"})
    assert round_.outcome() is None, "one player holding everything has not beaten anybody"


def test_a_board_with_no_owners_left_ends_without_a_winner(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    for territory in round_.board.territories:
        territory.owner = None
    assert round_.outcome() == Outcome(winner=None)


def test_leaving_takes_the_plan_with_it(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.phase = "commanding"
    round_.progress[bob].troops = 5
    round_.place(bob, 1, 2)
    _ = round_.drop(bob)
    assert round_.plan.placements_for(bob) == []


# --------------------------------------------------------------------------
# Submitting
# --------------------------------------------------------------------------


async def test_one_submission_at_a_time(alice: UUID, bob: UUID) -> None:
    """A player holding the submit key must not queue a sandbox run per press."""
    round_ = game(alice, bob)
    round_._judging.add(alice)
    with pytest.raises(combat.IllegalMove, match="still being judged"):
        _ = await round_.submit(alice, "print(1)")


async def test_the_in_flight_guard_is_released_afterwards(alice: UUID, bob: UUID) -> None:
    judge = StubJudge()
    round_ = game(alice, bob, judge)
    _ = await round_.submit(alice, "print(1)")
    _ = await round_.submit(alice, "print(2)")
    assert judge.calls == 2
    assert round_._judging == set()


async def test_the_guard_is_released_even_when_judging_raises(alice: UUID, bob: UUID) -> None:
    class Exploding:
        async def evaluate(self, problem: object, code: str) -> Verdict:
            raise RuntimeError("sandbox died")

    round_ = game(alice, bob, Exploding())
    with pytest.raises(RuntimeError):
        _ = await round_.submit(alice, "print(1)")
    assert round_._judging == set(), "otherwise one crash locks the player out for the round"


async def test_only_the_best_attempt_is_kept(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob, StubJudge(Verdict(passed=3, total=5)))
    _ = await round_.submit(alice, "good")
    round_.judge = StubJudge(Verdict(passed=1, total=5))  # pyright: ignore[reportAttributeAccessIssue]
    _ = await round_.submit(alice, "worse")

    best = round_.progress[alice].best
    assert best is not None and best.passed == 3
    assert round_.progress[alice].submissions == 2


async def test_solutions_are_refused_outside_the_submitting_phase(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.phase = "commanding"
    with pytest.raises(combat.IllegalMove, match="submitting phase"):
        _ = await round_.submit(alice, "print(1)")


# --------------------------------------------------------------------------
# Troops
# --------------------------------------------------------------------------


def test_everyone_gets_a_base_award_even_with_no_solution(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_._award_troops()
    # One territory each, which is below the per-territory threshold.
    assert round_.progress[alice].troops == BASE_TROOPS


async def test_a_perfect_solution_earns_the_full_share_and_first_blood(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob, StubJudge(Verdict(passed=4, total=4)))
    _ = await round_.submit(alice, "solved")
    round_._award_troops()
    assert round_.progress[alice].troops == BASE_TROOPS + SOLUTION_TROOPS + FIRST_BLOOD_TROOPS
    assert round_.progress[bob].troops == BASE_TROOPS, "bob solved nothing and is not first"


def test_a_player_holding_nothing_earns_nothing(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.board.territories[1].owner = alice
    round_._award_troops()
    assert round_.progress[bob].troops == 0


def test_an_award_replaces_what_is_in_hand_rather_than_adding(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.progress[alice].troops = 99
    round_._award_troops()
    assert round_.progress[alice].troops == BASE_TROOPS, "troops cannot be saved up between rounds"


# --------------------------------------------------------------------------
# The clock
# --------------------------------------------------------------------------


def test_a_player_with_no_ground_is_not_waited_on(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.board.territories[1].owner = alice
    round_.progress[alice].done = True
    assert round_.everyone_done(), "bob is out; the room should not sit on his clock"


def test_a_phase_does_not_end_while_somebody_is_still_thinking(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.progress[alice].done = True
    assert not round_.everyone_done()


def test_placing_takes_back_being_finished(alice: UUID, bob: UUID) -> None:
    round_ = game(alice, bob)
    round_.phase = "commanding"
    round_.progress[alice].troops = 5
    round_.finish(alice)
    round_.place(alice, 0, 1)
    assert not round_.progress[alice].done
