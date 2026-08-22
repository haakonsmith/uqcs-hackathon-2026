"""The half of a march that happens before anything reaches the server.

An order is built from two picks and a count, and every one of them can be
wrong in a way the client already knows about. What is tested here is that it
says so, and that being told does not cost the player the half-order they had
already built.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import blessed
import pytest

from client.palette import Factions
from client.state import App
from protocol import MoveOrder, Refused
from tests.conftest import board


class FakeConnection:
    """A connection that answers however the test wants, and remembers asks."""

    def __init__(self, reply: object = None) -> None:
        self.reply = reply
        self.sent: list[object] = []

    async def send(self, request: object) -> object:
        self.sent.append(request)
        return self.reply


@pytest.fixture
def me() -> UUID:
    return uuid4()


@pytest.fixture
def app(me: UUID) -> App:
    """A commanding-phase app holding territory 0, bordering 1 (theirs) and 2 (ours)."""
    them = uuid4()
    world = board((me, 6), (them, 2), (me, 1), (them, 9), links={0: {1, 2}, 1: {0}, 2: {0}, 3: set()})
    app = App(term=blessed.Terminal(), world=world, factions=Factions.assign(world, str(me)))
    app.me = str(me)
    app.conn = FakeConnection()  # pyright: ignore[reportAttributeAccessIssue]
    # `_order` reads the phase off the round only through `_selectable`; every
    # other path it takes is guarded by `move_from` alone.
    return app


# --------------------------------------------------------------------------
# Picking a source
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_order_starts_only_on_your_own_ground(app: App) -> None:
    app.selected = 1
    await app._order()
    assert app.move_from is None
    assert "your own territories" in app.message


@pytest.mark.asyncio
async def test_a_source_with_nothing_to_send_is_refused_before_it_is_picked(app: App) -> None:
    app.world.territories[0].soldiers = 0
    app.selected = 0
    await app._order()
    assert app.move_from is None, "picking it would strand the player mid-order"
    assert "nothing left to send" in app.message


@pytest.mark.asyncio
async def test_picking_a_source_says_how_many_it_can_send(app: App) -> None:
    app.selected = 0
    await app._order()
    assert app.move_from == 0
    assert "6 to send" in app.message


# --------------------------------------------------------------------------
# Getting out of a half-made order
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_picking_the_source_again_calls_the_march_off(app: App) -> None:
    app.move_from = 0
    app.selected = 0
    await app._order()

    assert app.move_from is None
    assert "called off" in app.message
    assert app.conn.sent == [], "nothing should reach the server"  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_a_destination_that_does_not_border_keeps_the_source(app: App) -> None:
    app.move_from = 0
    app.selected = 3
    await app._order()

    assert app.move_from == 0, "one bad click should cost one click, not the order"
    assert "does not border" in app.message
    assert app.conn.sent == []  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_a_refusal_keeps_the_source(app: App) -> None:
    app.conn = FakeConnection(Refused(reason="not this phase"))  # pyright: ignore[reportAttributeAccessIssue]
    app.move_from = 0
    app.selected = 1
    await app._order()
    assert app.move_from == 0


@pytest.mark.asyncio
async def test_an_accepted_order_clears_the_source(app: App) -> None:
    app.move_from = 0
    app.selected = 1
    await app._order()
    assert app.move_from is None
    assert len(app.conn.sent) == 1  # pyright: ignore[reportAttributeAccessIssue]


# --------------------------------------------------------------------------
# How many actually march
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clamped_count_is_said_rather_than_applied_quietly(app: App) -> None:
    app.world.territories[0].soldiers = 2
    app.move_count = 7
    app.move_from = 0
    app.selected = 1
    await app._order()

    sent = app.conn.sent[0]  # pyright: ignore[reportAttributeAccessIssue]
    assert sent.count == 2
    assert app.note is not None and "only 2 left" in app.note


@pytest.mark.asyncio
async def test_an_unclamped_count_says_nothing_extra(app: App) -> None:
    app.move_count = 3
    app.move_from = 0
    app.selected = 1
    await app._order()

    assert app.conn.sent[0].count == 3  # pyright: ignore[reportAttributeAccessIssue]
    assert app.note is None


@pytest.mark.asyncio
async def test_troops_placed_this_phase_are_available_to_march(app: App) -> None:
    app.placements = {0: 4}
    app.move_count = 9
    app.move_from = 0
    app.selected = 1
    await app._order()
    assert app.conn.sent[0].count == 9, "6 standing plus 4 placed covers the 9 asked for"  # pyright: ignore[reportAttributeAccessIssue]


def test_sendable_subtracts_orders_already_given(app: App) -> None:
    app.orders = [MoveOrder(source=0, target=1, count=4)]
    assert app._sendable(0) == 2


# --------------------------------------------------------------------------
# What the board and the hover slot say about a plan
# --------------------------------------------------------------------------


def test_outbound_sums_every_order_out_of_a_territory(app: App) -> None:
    app.orders = [
        MoveOrder(source=0, target=1, count=2),
        MoveOrder(source=0, target=2, count=1),
        MoveOrder(source=2, target=0, count=1),
    ]
    assert app._outbound() == {0: 3, 2: 1}


def test_the_hover_slot_reports_both_halves_of_a_plan(app: App) -> None:
    app.placements = {0: 2}
    app.orders = [MoveOrder(source=0, target=1, count=3)]
    assert app._placed_suffix(0) == ", +2 placed, -3 marching out"


def test_a_territory_with_no_plan_gets_no_suffix(app: App) -> None:
    assert app._placed_suffix(0) == ""


# --------------------------------------------------------------------------
# The preview, which is the whole point of picking a source first
# --------------------------------------------------------------------------


def test_no_preview_when_no_order_is_being_made(app: App) -> None:
    assert app._order_preview(1) == ""


def test_the_preview_names_the_count_and_calls_an_assault_an_assault(app: App) -> None:
    app.move_from = 0
    app.move_count = 3
    assert app._order_preview(1) == " - assault with 3 from 0"


def test_marching_onto_your_own_ground_is_a_reinforcement(app: App) -> None:
    app.move_from = 0
    app.move_count = 2
    assert app._order_preview(2) == " - reinforce with 2 from 0"


def test_the_preview_clamps_to_what_is_left(app: App) -> None:
    app.world.territories[0].soldiers = 1
    app.move_from = 0
    app.move_count = 5
    assert app._order_preview(1) == " - assault with 1 from 0"


def test_the_preview_says_when_a_territory_is_out_of_reach(app: App) -> None:
    app.move_from = 0
    assert app._order_preview(3) == " - does not border 0"


def test_the_source_previews_nothing_against_itself(app: App) -> None:
    app.move_from = 0
    assert app._order_preview(0) == ""


# --------------------------------------------------------------------------
# What [n] offers mid-order
# --------------------------------------------------------------------------


def test_cycling_mid_order_offers_your_own_neighbours_too(app: App, me: UUID) -> None:
    """Marching onto your own ground is how a frontier gets reinforced."""
    from protocol.rounds import RoundState

    app.round = RoundState(number=1, phase="commanding", seconds_left=10.0, players=[])
    app.move_from = 0
    assert app._selectable() == [1, 2], "theirs first, then ours - both legal"
