"""The rules of a commanding phase, exercised without a socket.

`resolve` is the whole game in one function: every placement a player committed
lands at once and the board that comes out is the next round's starting
position. It is also the only place the rules are written down, so these are
the tests that say what the game is.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from server import combat
from server.combat import IllegalMove, Plan, resolve
from tests.conftest import board

# --------------------------------------------------------------------------
# Placements
# --------------------------------------------------------------------------


def test_placement_on_your_own_ground_joins_the_garrison(alice: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 2), (None, 0))
    plan = Plan()
    plan.place(alice, 0, 3)
    resolve(world, plan, names)
    assert world.territories[0].soldiers == 5


def test_placement_next_door_is_an_assault(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 1), (bob, 2))
    plan = Plan()
    plan.place(alice, 1, 5)
    resolution = resolve(world, plan, names)
    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 3, "5 landing against a garrison of 2"
    assert [b.territory for b in resolution.battles] == [1]


def test_placement_out_of_reach_is_refused_before_it_is_promised(alice: UUID, bob: UUID) -> None:
    world = board((alice, 1), (bob, 2), (bob, 2), links={0: {1}, 1: {0}, 2: set()})
    with pytest.raises(IllegalMove, match="neither yours nor next to yours"):
        combat.check_placement(world, alice, 2, 1)


# --------------------------------------------------------------------------
# Resolution: a headcount, not a dice roll
# --------------------------------------------------------------------------


def test_landing_on_empty_ground_reports_no_battle(alice: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 5), (None, 0))
    plan = Plan()
    plan.place(alice, 1, 3)
    resolution = resolve(world, plan, names)

    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 3
    assert resolution.battles == []
    assert resolution.touched == (1,)


def test_winner_loses_as_many_as_the_runner_up_had(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 1), (bob, 4))
    plan = Plan()
    plan.place(alice, 1, 10)
    resolution = resolve(world, plan, names)

    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 6
    (battle,) = resolution.battles
    assert battle.winner == str(alice)
    assert battle.survivors == 6
    assert [(s.name, s.troops) for s in battle.stacks] == [("alice", 10), ("bob", 4)]


def test_a_tie_at_the_top_leaves_the_ground_unowned(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 1), (bob, 1), (None, 0))
    plan = Plan()
    plan.place(alice, 2, 4)
    plan.place(bob, 2, 4)
    resolution = resolve(world, plan, names)

    assert world.territories[2].owner is None
    assert world.territories[2].soldiers == 0
    (battle,) = resolution.battles
    assert battle.winner is None
    assert battle.survivors == 0


def test_the_third_stack_dies_without_costing_the_winner(alice: UUID, bob: UUID, carol: UUID, names: dict[UUID, str]) -> None:
    """Documents the current rule: the winner pays the runner-up only.

    Third place is spent for nothing, which makes ganging up on a leader a
    worse deal for whoever brings the smaller army. Change this and the test
    should change with it - it is here to make the choice visible, not because
    the number is obviously right.
    """
    world = board((alice, 1), (bob, 1), (carol, 1), (None, 0))
    plan = Plan()
    plan.place(alice, 3, 10)
    plan.place(bob, 3, 6)
    plan.place(carol, 3, 3)
    resolve(world, plan, names)

    assert world.territories[3].owner == alice
    assert world.territories[3].soldiers == 4, "10 - 6, with carol's 3 costing nothing"


def test_the_sitting_garrison_is_just_another_stack(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """No defender's bonus: equal numbers annihilate and the ground goes free."""
    world = board((alice, 1), (bob, 7))
    plan = Plan()
    plan.place(alice, 1, 7)
    resolve(world, plan, names)
    assert world.territories[1].owner is None


def test_an_owner_with_nothing_standing_is_not_a_side(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 3), (bob, 0))
    plan = Plan()
    plan.place(alice, 1, 2)
    resolution = resolve(world, plan, names)

    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 2
    assert resolution.battles == [], "walking onto empty ground is not a battle"


def test_reinforcing_and_assaulting_in_one_phase(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """A player can shore up one frontier and push another with the same award."""
    world = board((alice, 1), (bob, 4), (None, 0))
    plan = Plan()
    plan.place(alice, 0, 6)
    plan.place(alice, 1, 5)
    resolve(world, plan, names)

    assert world.territories[0].soldiers == 7, "reinforcements join the garrison outright"
    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 1, "5 against a garrison of 4"


def test_a_placement_out_of_reach_is_dropped_and_reported(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """Reach is rechecked at resolution: the board moves between promise and land."""
    world = board((alice, 3), (bob, 2), (bob, 2), links={0: {1}, 1: {0}, 2: set()})
    plan = Plan()
    plan.place(alice, 2, 4)
    resolution = resolve(world, plan, names)

    assert world.territories[2].owner == bob
    assert world.territories[2].soldiers == 2, "nothing landed"
    (dropped,) = resolution.dropped
    assert dropped.name == "alice" and dropped.count == 4
    assert "out of reach" in dropped.reason


# --------------------------------------------------------------------------
# Tearing up a plan
# --------------------------------------------------------------------------


def test_clearing_a_plan_returns_the_placed_troops(alice: UUID, bob: UUID) -> None:
    plan = Plan()
    plan.place(alice, 0, 4)
    plan.place(alice, 1, 2)
    plan.place(bob, 1, 3)

    assert plan.clear(alice) == 6
    assert plan.placements_for(alice) == []
    assert plan.placements_for(bob) == [combat.Placement(territory=1, count=3)], "one player's plan, not everyone's"


def test_a_cleared_plan_is_falsy(alice: UUID) -> None:
    plan = Plan()
    assert not plan
    plan.place(alice, 0, 1)
    assert plan
    _ = plan.clear(alice)
    assert not plan


# --------------------------------------------------------------------------
# Elimination
# --------------------------------------------------------------------------


def test_a_player_is_out_once_they_hold_no_ground(alice: UUID, bob: UUID) -> None:
    world = board((alice, 0), (bob, 3))
    assert not combat.eliminated(world, alice), "holding an empty territory is still holding"
    world.territories[0].owner = bob
    assert combat.eliminated(world, alice)


def test_a_stranger_holds_nothing(alice: UUID) -> None:
    assert combat.eliminated(board((alice, 3)), uuid4())




# --------------------------------------------------------------------------
# Who held the ground going in
# --------------------------------------------------------------------------


def test_a_battle_names_the_side_that_already_held_the_ground(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 1), (bob, 4))
    plan = Plan()
    plan.place(alice, 1, 10)
    (battle,) = resolve(world, plan, names).battles

    assert battle.defender == str(bob)
    assert battle.defender_name == "bob"
    assert not battle.held, "bob lost it"


def test_a_defender_who_survives_is_reported_as_holding(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 1), (bob, 10))
    plan = Plan()
    plan.place(alice, 1, 4)
    (battle,) = resolve(world, plan, names).battles

    assert battle.winner == str(bob)
    assert battle.held, "the ground never changed hands"
    assert "held it" in battle.summary()


def test_unowned_ground_has_no_defender(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 1), (bob, 1), (None, 0))
    plan = Plan()
    plan.place(alice, 2, 5)
    plan.place(bob, 2, 2)
    (battle,) = resolve(world, plan, names).battles

    assert battle.defender is None
    assert battle.defender_name == "nobody"
    assert not battle.held


def test_a_wipe_out_still_names_the_side_that_held_it(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """The one outcome with no winner is also the one where losing it hurts."""
    world = board((alice, 1), (bob, 7))
    plan = Plan()
    plan.place(alice, 1, 7)
    (battle,) = resolve(world, plan, names).battles

    assert battle.winner is None
    assert battle.defender == str(bob)
    assert not battle.held, "nobody held anything"


def test_the_defender_is_read_before_the_board_is_written(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """`_settle` reassigns `territory.owner`; the report must predate that."""
    world = board((alice, 1), (bob, 4))
    plan = Plan()
    plan.place(alice, 1, 10)
    (battle,) = resolve(world, plan, names).battles

    assert world.territories[1].owner == alice, "the board moved on"
    assert battle.defender == str(bob), "the report did not"
