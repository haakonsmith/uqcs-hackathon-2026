"""The white highlight: what picks a territory, and what lets go of it.

`selected` is a standing choice - it is drawn white until something clears it
and it is what the phase keys act on. A click is not one of those things: it
says where troops go once. Conflating the two left every territory a player had
ever clicked highlighted for the rest of the phase.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import blessed
import pytest

from client.palette import Factions
from client.render import CELL_WIDTH
from client.state import App
from protocol.rounds import PlayerRound, RoundState
from tests.conftest import board


class FakeConnection:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, request: object) -> object:
        self.sent.append(request)
        return None


@pytest.fixture
def me() -> UUID:
    return uuid4()


@pytest.fixture
def app(me: UUID) -> App:
    them = uuid4()
    world = board((me, 6), (them, 2), (me, 1), links={0: {1, 2}, 1: {0}, 2: {0}})
    app = App(term=blessed.Terminal(), world=world, factions=Factions.assign(world, str(me)))
    app.me = str(me)
    app.conn = FakeConnection()  # pyright: ignore[reportAttributeAccessIssue]
    app.round = RoundState(
        number=1,
        phase="commanding",
        seconds_left=10.0,
        players=[PlayerRound(player_id=str(me), name="me", troops=9)],
    )
    return app


def point_at(app: App, territory: int) -> None:
    """Put the cursor over a territory. Cells are `CELL_WIDTH` columns wide."""
    app.cursor = (territory * CELL_WIDTH, 0)


def white(app: App) -> set[int]:
    """Territories drawn with the standing-pick highlight."""
    app.refresh_hover()
    return {t for t, colour in app.highlights.items() if colour == (245, 245, 245)}


# --------------------------------------------------------------------------
# Clicking
# --------------------------------------------------------------------------


async def test_placing_by_click_does_not_leave_the_territory_highlighted(app: App) -> None:
    point_at(app, 0)
    await app._click()

    assert app.selected is None
    assert white(app) == set(), "a click says where troops go, it does not pick anything"


async def test_clicking_several_territories_highlights_none_of_them(app: App) -> None:
    for territory in (0, 1, 2):
        point_at(app, territory)
        await app._click()
    assert white(app) == set()


async def test_a_click_still_places_where_it_was_aimed(app: App) -> None:
    point_at(app, 1)
    app.move_count = 3
    await app._click()

    (sent,) = app.conn.sent  # pyright: ignore[reportAttributeAccessIssue]
    assert sent.territory == 1 and sent.count == 3


async def test_a_click_outside_the_commanding_phase_still_picks(app: App) -> None:
    """Nothing to place, so the click is only ever a way to look at something."""
    app.round = RoundState(number=1, phase="submitting", seconds_left=10.0, players=[])
    point_at(app, 2)
    await app._click()
    assert app.selected == 2


# --------------------------------------------------------------------------
# Picking with the keyboard, and letting go
# --------------------------------------------------------------------------


def test_cycling_picks_a_territory_and_draws_it_white(app: App) -> None:
    app._cycle(1)
    assert app.selected is not None
    assert white(app) == {app.selected}


async def test_escape_lets_go_of_the_pick(app: App) -> None:
    app._cycle(1)
    assert app.selected is not None

    await app.handle_key(blessed.keyboard.Keystroke("\x1b", name="KEY_ESCAPE"))
    assert app.selected is None
    assert white(app) == set()


async def test_escape_with_nothing_picked_says_nothing_about_it(app: App) -> None:
    app.message = "unchanged"
    await app.handle_key(blessed.keyboard.Keystroke("\x1b", name="KEY_ESCAPE"))
    assert app.message == "unchanged"


def test_only_one_territory_is_ever_picked(app: App) -> None:
    app._cycle(1)
    first = app.selected
    app._cycle(1)
    assert app.selected != first
    assert white(app) == {app.selected}, "cycling moves the pick rather than adding one"


# --------------------------------------------------------------------------
# What the phase keys act on
# --------------------------------------------------------------------------


async def test_the_keyboard_pick_beats_the_cursor(app: App) -> None:
    app.selected = 2
    point_at(app, 0)
    await app._place(app._target_territory(), 1)

    (sent,) = app.conn.sent  # pyright: ignore[reportAttributeAccessIssue]
    assert sent.territory == 2, "[space] acts on what [n] picked, not on what the mouse is over"


async def test_with_no_pick_the_phase_keys_fall_back_to_the_cursor(app: App) -> None:
    point_at(app, 1)
    await app._place(app._target_territory(), 1)

    (sent,) = app.conn.sent  # pyright: ignore[reportAttributeAccessIssue]
    assert sent.territory == 1
