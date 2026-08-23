"""The end-of-phase report: whether a player can read their own game out of it.

The same report goes to everybody, so it has to be written from the reader's
side. These cases pin the two things that takes: saying which side is yours,
and saying where the ground is.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from client.hud import battles_popup, whereabouts
from protocol.rounds import BattleReport, DroppedOrder, Stack
from protocol.terrain import Cell, Terrain, Territory, WorldMap

ME = str(uuid4())
THEM = str(uuid4())
OTHER = str(uuid4())


def world_with(**spots: tuple[int, int]) -> WorldMap:
    """A 30x20 map with a territory centred at each given position."""
    highest = max(int(k.lstrip("t")) for k in spots) + 1
    placed = {int(k.lstrip("t")): v for k, v in spots.items()}
    territories = [Territory(id=i, cells=[placed.get(i, (0, 0))]) for i in range(highest)]
    grid = [[Cell(x=x, y=y, height=0.5, terrain=Terrain.GRASS) for x in range(30)] for y in range(20)]
    return WorldMap(width=30, height=20, grid=grid, territories=territories)


def text(lines: list[str]) -> str:
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Saying where
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spot", "expected"),
    [
        ((2, 2), "the north-west"),
        ((15, 2), "the north"),
        ((28, 2), "the north-east"),
        ((2, 10), "the west"),
        ((15, 10), "the middle"),
        ((28, 18), "the south-east"),
    ],
)
def test_whereabouts_names_a_corner_of_the_map(spot: tuple[int, int], expected: str) -> None:
    assert whereabouts(world_with(t0=spot), 0) == expected


def test_whereabouts_survives_a_territory_it_has_never_heard_of() -> None:
    """A report naming ground the client has no cell for must still render."""
    assert whereabouts(world_with(t0=(1, 1)), 99) == "territory 99"


def test_the_id_is_kept_alongside_the_bearing() -> None:
    """Nobody navigates by it, but the logs and the server speak in ids."""
    world = world_with(t0=(2, 2), t1=(2, 2))
    battle = BattleReport(territory=1, stacks=[Stack(owner=ME, name="me", troops=2)], winner=ME, winner_name="me", survivors=2)
    assert "the north-west  (1)" in text(battles_popup([battle], [], world, ME))


# --------------------------------------------------------------------------
# Saying whose
# --------------------------------------------------------------------------


def _fight(winner: str | None, survivors: int, defender: str | None = None) -> BattleReport:
    """Haakon 8 against Guus 3, over ground `defender` held going in."""
    return BattleReport(
        territory=0,
        stacks=[Stack(owner=ME, name="Haakon", troops=8), Stack(owner=THEM, name="Guus", troops=3)],
        winner=winner,
        winner_name="Haakon" if winner == ME else ("Guus" if winner else ""),
        survivors=survivors,
        defender=defender,
        defender_name={ME: "Haakon", THEM: "Guus"}.get(defender or "", "nobody"),
    )


def _say(battle: BattleReport, reader: str = ME) -> str:
    return text(battles_popup([battle], [], world_with(t0=(2, 2)), reader))


def test_your_own_stack_is_called_you() -> None:
    out = _say(_fight(ME, 5, defender=THEM))
    assert "you 8  vs  Guus 3" in out
    assert "Haakon" not in out, "your own name is the one thing you do not need telling"


def test_the_same_report_reads_the_other_way_round_for_the_other_player() -> None:
    out = _say(_fight(ME, 5, defender=THEM), reader=THEM)
    assert "Haakon 8  vs  you 3" in out
    assert "Haakon took it from you" in out


# --------------------------------------------------------------------------
# Taking, holding, and losing - the three that used to read the same
# --------------------------------------------------------------------------


def test_taking_ground_names_who_lost_it() -> None:
    assert "you took it from Guus, 5 left standing" in _say(_fight(ME, 5, defender=THEM))


def test_losing_ground_says_it_was_taken_from_you() -> None:
    assert "Guus took it from you, 5 left standing" in _say(_fight(THEM, 5, defender=ME))


def test_holding_ground_is_not_the_same_as_taking_it() -> None:
    """You already had it; nothing was taken from anybody."""
    out = _say(_fight(ME, 5, defender=ME))
    assert "you held it, 5 left standing" in out
    assert "took" not in out


def test_a_defender_holding_against_you_reads_as_a_hold() -> None:
    assert "Guus held it, 5 left standing" in _say(_fight(THEM, 5, defender=THEM))


def test_unowned_ground_has_nobody_to_take_it_from() -> None:
    assert "you took empty ground, 5 left standing" in _say(_fight(ME, 5, defender=None))


def test_a_mutual_wipe_out_says_whose_ground_it_was() -> None:
    assert "nobody holds it - it was yours, every side wiped out" in _say(_fight(None, 0, defender=ME))
    assert "nobody holds it - it was Guus's, every side wiped out" in _say(_fight(None, 0, defender=THEM))


def test_a_wipe_out_over_unowned_ground_claims_no_owner() -> None:
    assert "nobody holds it, every side wiped out" in _say(_fight(None, 0, defender=None))


def test_your_own_battles_come_first() -> None:
    world = world_with(t0=(2, 2), t1=(28, 18))
    theirs = BattleReport(
        territory=1,
        stacks=[Stack(owner=THEM, name="Guus", troops=4), Stack(owner=OTHER, name="Mason", troops=2)],
        winner=THEM,
        winner_name="Guus",
        survivors=2,
        defender=OTHER,
        defender_name="Mason",
    )
    lines = battles_popup([theirs, _fight(ME, 5, defender=THEM)], [], world, ME)
    yours = next(i for i, line in enumerate(lines) if "(0)" in line)
    other = next(i for i, line in enumerate(lines) if "(1)" in line)
    assert yours < other, "what happened to you is what you want to read first"


# --------------------------------------------------------------------------
# Placements that never landed
# --------------------------------------------------------------------------


def test_your_own_lost_placement_is_named_as_yours() -> None:
    world = world_with(t0=(15, 2))
    lost = DroppedOrder(player=ME, name="Haakon", territory=0, count=4, reason="out of reach when the phase ended")
    out = text(battles_popup([], [lost], world, ME))
    assert "4 of yours at the north  (0)" in out
    assert "out of reach when the phase ended" in out


def test_somebody_elses_lost_placement_keeps_their_name() -> None:
    world = world_with(t0=(15, 2))
    lost = DroppedOrder(player=THEM, name="Guus", territory=0, count=4, reason="out of reach when the phase ended")
    assert "4 of Guus's at the north" in text(battles_popup([], [lost], world, ME))


# --------------------------------------------------------------------------
# Nothing to report
# --------------------------------------------------------------------------


def test_a_quiet_phase_says_so_rather_than_showing_an_empty_box() -> None:
    out = battles_popup([], [], world_with(t0=(2, 2)), ME)
    assert "every placement landed unopposed" in text(out)
    assert "BATTLES" not in text(out)


def test_the_report_still_renders_without_a_world() -> None:
    """The popup is replayed by [v], and nothing should depend on that path."""
    out = text(battles_popup([_fight(ME, 5)]))
    assert "territory 0" in out


def test_every_line_fits_a_narrow_terminal() -> None:
    """`draw_panel` sizes to the longest line and clips; a run-on line hides."""
    world = world_with(t0=(2, 2), t1=(28, 18))
    lost = DroppedOrder(player=THEM, name="M" * 16, territory=1, count=999, reason="no longer yours when the phase ended")
    for line in battles_popup([_fight(ME, 5)], [lost], world, ME):
        assert len(line) <= 60, line


# --------------------------------------------------------------------------
# The short form the board hover uses
# --------------------------------------------------------------------------


def test_mark_note_says_which_way_the_ground_went() -> None:
    from client.hud import mark_note

    assert mark_note(_fight(ME, 5, defender=THEM), ME) == "you took it from Guus"
    assert mark_note(_fight(THEM, 5, defender=ME), ME) == "Guus took it from you"
    assert mark_note(_fight(ME, 5, defender=ME), ME) == "you held it"
    assert mark_note(_fight(ME, 5, defender=None), ME) == "you took empty ground"


def test_mark_note_covers_a_wipe_out() -> None:
    from client.hud import mark_note

    assert mark_note(_fight(None, 0, defender=ME), ME) == "wiped out - was yours"
    assert mark_note(_fight(None, 0, defender=None), ME) == "wiped out"


def test_mark_note_leaves_out_the_count_the_board_already_shows() -> None:
    from client.hud import mark_note

    assert "5" not in mark_note(_fight(ME, 5, defender=THEM), ME)


def test_mark_note_stays_within_two_names_and_a_verb() -> None:
    """The worst case is a fight between two strangers with maximal names."""
    from client.hud import mark_note
    from protocol.lobby import MAX_NAME_LENGTH

    longest = _fight(THEM, 5, defender=ME)
    object.__setattr__(longest, "winner_name", "M" * MAX_NAME_LENGTH)
    object.__setattr__(longest, "defender_name", "N" * MAX_NAME_LENGTH)
    assert len(mark_note(longest, OTHER)) <= 2 * MAX_NAME_LENGTH + len(" took it from ")


def test_dropped_note_names_whose_troops_were_lost() -> None:
    from client.hud import dropped_note

    mine = DroppedOrder(player=ME, name="Haakon", territory=0, count=4, reason="out of reach")
    theirs = DroppedOrder(player=THEM, name="Guus", territory=0, count=2, reason="out of reach")
    assert dropped_note(mine, ME) == "4 of yours never landed"
    assert dropped_note(theirs, ME) == "2 of Guus's never landed"
