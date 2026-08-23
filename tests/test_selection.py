"""The white highlight: what picks a territory, and what lets go of it.

`selected` is a standing choice - drawn white until something clears it, and
what the phase keys act on. A click is not one: it says where troops go once.
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


# --------------------------------------------------------------------------
# A panel that arrives unasked cannot be closed by a key already in flight
# --------------------------------------------------------------------------


async def test_a_held_panel_survives_a_keystroke(app: App) -> None:
    """The reveal lands on the server's clock, not the player's."""
    app._show_popup(["REPORT", "", "[any key] back to the board"], hold=5.0)
    await app.handle_key(blessed.keyboard.Keystroke("x"))
    assert app.popup is not None, "the key was aimed at whatever was on screen before this"


async def test_an_unheld_panel_closes_on_the_first_key(app: App) -> None:
    app._show_popup(["HELP", "", "[any key] back to the board"])
    await app.handle_key(blessed.keyboard.Keystroke("x"))
    assert app.popup is None


async def test_a_panel_closes_once_its_hold_has_run_out(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    import client.state as state_module

    app._show_popup(["REPORT", "", "[any key] back to the board"], hold=5.0)
    later = app.popup_hold_until + 1.0
    monkeypatch.setattr(state_module.time, "monotonic", lambda: later)

    await app.handle_key(blessed.keyboard.Keystroke("x"))
    assert app.popup is None
    assert app.popup_hold_until == 0.0


def test_a_held_panel_says_when_it_will_take_a_key(app: App) -> None:
    app._show_popup(["REPORT", "", "[any key] back to the board"], hold=5.0)
    assert app._panel()[-1].startswith("[any key] in ")


def test_an_unheld_panel_keeps_its_own_last_line(app: App) -> None:
    lines = ["HELP", "", "[any key] back to the board"]
    app._show_popup(lines)
    assert app._panel() == lines


def test_a_held_panel_keeps_repainting_so_the_countdown_moves(app: App) -> None:
    app._show_popup(["REPORT", "", "[any key] back to the board"], hold=5.0)
    app.dirty = False
    app.tick()
    assert app.dirty, "a frozen countdown reads as the game having hung"


def test_the_hold_is_only_long_enough_to_swallow_a_key_in_flight() -> None:
    from client import hud
    from protocol.rounds import BattleReport

    one = hud.reveal_hold([BattleReport(territory=0)])
    many = hud.reveal_hold([BattleReport(territory=i) for i in range(5)])
    assert one == many == hud.REVEAL_HOLD_SECONDS, "a longer report is not a reason to lock the board"
    assert one < 1.0, "the next real keypress closes it"


def test_a_hold_too_short_to_read_says_nothing_about_itself() -> None:
    """Otherwise the panel flashes a countdown nobody could act on."""
    from client import hud

    assert hud.hold_notice(hud.REVEAL_HOLD_SECONDS) is None
    assert hud.hold_notice(5.0) == "[any key] in 5s"


def test_a_phase_with_nothing_to_report_is_not_held_at_all() -> None:
    from client import hud

    assert hud.reveal_hold([], []) == 0.0, "there is nothing to protect from a stray key"


# --------------------------------------------------------------------------
# A lit territory has to be able to say what happened to it
# --------------------------------------------------------------------------


def test_hovering_a_marked_territory_says_what_happened(app: App) -> None:
    app.battle_notes = {1: "you took it from Guus"}
    point_at(app, 1)
    app.refresh_hover()
    assert "you took it from Guus" in app.hover


def test_an_unmarked_territory_says_nothing_extra(app: App) -> None:
    app.battle_notes = {1: "you took it from Guus"}
    point_at(app, 0)
    app.refresh_hover()
    assert "took it" not in app.hover


def test_the_hover_says_nothing_about_the_tile_under_the_cursor(app: App) -> None:
    """Terrain and height decide how the board is generated, not how it plays."""
    point_at(app, 1)
    app.refresh_hover()
    for tile_detail in ("height", "grass", "(1,0)"):
        assert tile_detail not in app.hover, app.hover


def test_the_hover_says_who_holds_it_and_how_strongly(app: App) -> None:
    point_at(app, 1)
    app.refresh_hover()
    assert "territory 1" in app.hover
    assert "2 soldiers" in app.hover


def test_the_hover_counts_what_this_phase_would_add(app: App) -> None:
    app.placements = {1: 3}
    point_at(app, 1)
    app.refresh_hover()
    assert "+3 placed" in app.hover


def test_pointing_at_open_water_says_there_is_nothing_there(app: App) -> None:
    app.world.grid[0][1].territory = None
    point_at(app, 1)
    app.refresh_hover()
    assert app.hover == "nothing here"


def test_open_water_still_reports_a_keyboard_pick(app: App) -> None:
    """The pick is a standing choice; the mouse wandering off it changes nothing."""
    app.world.grid[0][1].territory = None
    app.selected = 2
    point_at(app, 1)
    app.refresh_hover()
    assert "territory 2" in app.hover


def test_the_keyboard_pick_reports_the_note_too(app: App) -> None:
    app.battle_notes = {2: "you held it"}
    app.cursor = None
    app.selected = 2
    app.refresh_hover()
    assert "you held it" in app.hover


def test_a_marked_territory_is_reachable_with_the_keyboard(app: App, me: UUID) -> None:
    """Ground taken off you outright borders nothing of yours any more."""
    stranger = uuid4()
    app.world.territories[1].owner = stranger
    app.world.territories[1].land_neighbours = set()
    app.world.territories[0].land_neighbours = {2}
    app.world.territories[2].land_neighbours = {0}

    assert 1 not in app._selectable(), "nothing of yours touches it"
    app.battle_notes = {1: "Guus took it from you"}
    assert 1 in app._selectable(), "reading the mark should not need a mouse"


def test_a_marked_territory_is_only_offered_once(app: App) -> None:
    app.battle_notes = {0: "you held it"}
    options = app._selectable()
    assert options.count(0) == 1


def test_marks_are_lit_on_the_board(app: App) -> None:
    app.battle_notes = {1: "you took it from Guus"}
    app.refresh_hover()
    assert app.highlights.get(1) == (200, 110, 45)


def test_the_pick_is_drawn_over_a_mark(app: App) -> None:
    """The pick is what you are doing now; a mark already happened."""
    app.battle_notes = {1: "you took it from Guus"}
    app.selected = 1
    app.refresh_hover()
    assert app.highlights[1] == (245, 245, 245)


def test_the_hover_and_the_keys_no_longer_share_a_row() -> None:
    """They were cutting each other off on any terminal under ~120 columns."""
    from client.state import HUD_ROWS

    assert HUD_ROWS == 3, "phase, hover and keys need a row each"


def test_the_key_line_gives_up_hints_rather_than_being_cut(app: App) -> None:
    """`Screen.row` cuts mid-word, and the tail is where [?] lives."""
    from client import hud

    for width in (50, 60, 70, 80, 100, 200):
        line = hud.key_line(app.round, width)
        assert len(line) <= width or width < len("  [?] help  [q]uit "), (width, line)
        assert "[?] help" in line, "the hint that leads to all the others"
        assert "[q]uit" in line


def test_a_wide_terminal_keeps_every_hint(app: App) -> None:
    from client import hud

    full = hud.key_line(app.round)
    assert hud.key_line(app.round, 200) == full
    for hint in hud.KEYS["commanding"]:
        assert hint in full


def test_the_least_useful_hint_is_the_first_to_go(app: App) -> None:
    from client import hud

    narrow = hud.key_line(app.round, 70)
    assert "click to place" in narrow, "the core action survives"
    assert "[p] all" not in narrow, "a convenience does not"


def test_no_width_means_no_limit(app: App) -> None:
    from client import hud

    assert hud.key_line(app.round) == hud.key_line(app.round, 0)
