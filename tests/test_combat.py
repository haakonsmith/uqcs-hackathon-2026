"""The rules of a commanding phase, exercised without a socket.

`resolve` is the whole game in one function: everything a player committed
lands at once and the board that comes out is the next round's starting
position. It is also the only place the rules are written down, so these are
the tests that say what the game is.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from protocol.rounds import MoveOrder
from server import combat
from server.combat import IllegalMove, Plan, resolve, sendable, validate
from tests.conftest import board

# --------------------------------------------------------------------------
# What a territory has left to send
# --------------------------------------------------------------------------


def test_sendable_is_garrison_plus_placed_less_committed(alice: UUID) -> None:
    world = board((alice, 5), (None, 0))
    plan = Plan()
    assert sendable(world, plan, alice, 0) == 5

    plan.place(alice, 0, 3)
    assert sendable(world, plan, alice, 0) == 8, "troops placed this phase can march out of it"

    plan.add(alice, MoveOrder(source=0, target=1, count=6))
    assert sendable(world, plan, alice, 0) == 2, "an ordered soldier cannot be sent twice"


def test_placing_twice_on_one_territory_is_one_reinforcement(alice: UUID) -> None:
    plan = Plan()
    plan.place(alice, 0, 2)
    plan.place(alice, 0, 3)
    assert plan.placements_for(alice) == [combat.Placement(territory=0, count=5)]


# --------------------------------------------------------------------------
# Refusing an order before it joins the plan
# --------------------------------------------------------------------------


def test_order_must_start_from_your_own_ground(alice: UUID, bob: UUID) -> None:
    world = board((bob, 5), (alice, 5))
    with pytest.raises(IllegalMove, match="not yours"):
        validate(world, Plan(), alice, MoveOrder(source=0, target=1, count=1))


def test_order_must_reach_a_neighbour(alice: UUID) -> None:
    world = board((alice, 5), (None, 0), (None, 0), links={0: {1}, 1: {0}, 2: set()})
    with pytest.raises(IllegalMove, match="does not border"):
        validate(world, Plan(), alice, MoveOrder(source=0, target=2, count=1))


def test_order_cannot_overdraw_the_garrison(alice: UUID) -> None:
    world = board((alice, 3), (None, 0))
    with pytest.raises(IllegalMove, match="has 3 left to send"):
        validate(world, Plan(), alice, MoveOrder(source=0, target=1, count=4))


def test_two_orders_cannot_share_the_same_soldiers(alice: UUID) -> None:
    world = board((alice, 5), (None, 0), (None, 0))
    plan = Plan()
    order = MoveOrder(source=0, target=1, count=4)
    validate(world, plan, alice, order)
    plan.add(alice, order)
    with pytest.raises(IllegalMove, match="has 1 left to send"):
        validate(world, plan, alice, MoveOrder(source=0, target=2, count=2))


def test_an_order_may_spend_troops_placed_this_phase(alice: UUID) -> None:
    world = board((alice, 1), (None, 0))
    plan = Plan()
    plan.place(alice, 0, 4)
    validate(world, plan, alice, MoveOrder(source=0, target=1, count=5))


def test_unowned_territory_ids_are_refused(alice: UUID) -> None:
    world = board((alice, 5), (None, 0))
    with pytest.raises(IllegalMove, match="no territory 9"):
        validate(world, Plan(), alice, MoveOrder(source=0, target=9, count=1))


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


def test_uncontested_march_into_empty_ground_reports_no_battle(alice: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 5), (None, 0))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=1, count=3))
    resolution = resolve(world, plan, names)

    assert world.territories[0].soldiers == 2
    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 3
    assert resolution.battles == []
    assert resolution.touched == (0, 1)


def test_winner_loses_as_many_as_the_runner_up_had(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 10), (bob, 4))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=1, count=10))
    resolution = resolve(world, plan, names)

    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 6
    (battle,) = resolution.battles
    assert battle.winner == str(alice)
    assert battle.survivors == 6
    assert [(s.name, s.troops) for s in battle.stacks] == [("alice", 10), ("bob", 4)]


def test_a_tie_at_the_top_leaves_the_ground_unowned(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 4), (bob, 4), (None, 0))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=2, count=4))
    plan.add(bob, MoveOrder(source=1, target=2, count=4))
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
    world = board((alice, 10), (bob, 6), (carol, 3), (None, 0))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=3, count=10))
    plan.add(bob, MoveOrder(source=1, target=3, count=6))
    plan.add(carol, MoveOrder(source=2, target=3, count=3))
    resolve(world, plan, names)

    assert world.territories[3].owner == alice
    assert world.territories[3].soldiers == 4, "10 - 6, with carol's 3 costing nothing"


def test_the_sitting_garrison_is_just_another_stack(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """No defender's bonus: equal numbers annihilate and the ground goes free."""
    world = board((alice, 7), (bob, 7))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=1, count=7))
    resolve(world, plan, names)
    assert world.territories[1].owner is None


def test_an_owner_with_nothing_standing_is_not_a_side(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 3), (bob, 0))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=1, count=2))
    resolution = resolve(world, plan, names)

    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 2
    assert resolution.battles == [], "walking into an empty territory is not a battle"


def test_marching_everything_out_keeps_ownership_of_an_empty_territory(alice: UUID, names: dict[UUID, str]) -> None:
    world = board((alice, 5), (None, 0))
    plan = Plan()
    plan.add(alice, MoveOrder(source=0, target=1, count=5))
    resolve(world, plan, names)

    assert world.territories[0].owner == alice
    assert world.territories[0].soldiers == 0, "held, but there for the taking"


def test_placements_land_before_anybody_marches(alice: UUID, bob: UUID, names: dict[UUID, str]) -> None:
    """The whole point of one phase for both: reinforce, then attack with it."""
    world = board((alice, 1), (bob, 4))
    plan = Plan()
    plan.place(alice, 0, 6)
    plan.add(alice, MoveOrder(source=0, target=1, count=7))
    resolve(world, plan, names)

    assert world.territories[0].soldiers == 0
    assert world.territories[1].owner == alice
    assert world.territories[1].soldiers == 3


# --------------------------------------------------------------------------
# Tearing up a plan
# --------------------------------------------------------------------------


def test_clearing_a_plan_returns_placed_troops_and_forgets_orders(alice: UUID, bob: UUID) -> None:
    plan = Plan()
    plan.place(alice, 0, 4)
    plan.add(alice, MoveOrder(source=0, target=1, count=2))
    plan.place(bob, 1, 3)

    assert plan.clear(alice) == 4
    assert plan.orders_for(alice) == []
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
