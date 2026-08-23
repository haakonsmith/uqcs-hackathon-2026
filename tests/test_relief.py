"""The board's relief: where a shadow falls, and what it must not break.

The board is a flat grid of colours, so depth comes from shading rather than
geometry. What matters is that the shading is a drop shadow - offset, on one
side only - and that laying it under the frontier does not cost the frontier
the contrast it goes to some trouble to guarantee.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from client.palette import Factions
from client.render import (
    BORDER_ALPHA,
    BORDER_CONTRAST,
    COAST_SHADOW_ALPHA,
    EDGE_SHADOW_ALPHA,
    RIM_LIGHT_ALPHA,
    SHADOW_REACH,
    board_colors,
    cell_color,
    contrast,
    frontier_color,
    frontier_side,
    relief,
    relief_grid,
    shaded,
)
from client.viewport import Viewport
from protocol.terrain import Cell, Terrain, Territory, WorldMap

ROWS = 3
MIDDLE = 1


def strip(row: str, owners: dict[str, UUID | None] | None = None) -> WorldMap:
    """A board from a sketch: `~` sea, `.` unclaimed land, digits held.

    Each distinct digit is its own territory, so `~..11~` is water, two cells
    of nobody's land, a two-cell holding and water again.

    The sketch is repeated down `ROWS` rows, so every cell in the middle row
    has the same tile above it as beside it. That leaves the horizontal
    neighbour as the only thing the shading can be reacting to - a single row
    would put the board edge, which reads as open sea, over everything.
    """
    owners = owners or {}
    territories: list[Territory] = []
    by_digit: dict[str, int] = {}
    grid: list[list[Cell]] = []
    for y in range(ROWS):
        cells: list[Cell] = []
        for x, char in enumerate(row):
            if char.isdigit():
                if char not in by_digit:
                    by_digit[char] = len(territories)
                    territories.append(Territory(id=len(territories), owner=owners.get(char, uuid4())))
                tid = by_digit[char]
                territories[tid].cells.append((x, y))
                cells.append(Cell(x=x, y=y, height=0.6, terrain=Terrain.GRASS, territory=tid))
            elif char == ".":
                cells.append(Cell(x=x, y=y, height=0.6, terrain=Terrain.GRASS, territory=None))
            else:
                cells.append(Cell(x=x, y=y, height=0.2, terrain=Terrain.DEEP_WATER, territory=None))
        grid.append(cells)
    return WorldMap(width=len(row), height=ROWS, grid=grid, territories=territories)


def shading(row: str) -> list[float]:
    world = strip(row)
    return [round(relief(world, cell, 1), 4) for cell in world.grid[MIDDLE]]


# --------------------------------------------------------------------------
# Where the shadow falls
# --------------------------------------------------------------------------


def test_water_east_of_a_coast_is_in_shadow() -> None:
    """Light comes from the top-left, so land throws its shadow right."""
    values = shading("~11~~~~~")
    assert values[3] == -COAST_SHADOW_ALPHA, "the cell against the coast is darkest"
    assert values[3] < values[4] < values[5] < 0, "and it fades with distance"


def test_water_west_of_a_coast_is_untouched() -> None:
    """A shadow on both sides is an outline, not a shadow."""
    values = shading("~~~~11~~")
    assert values[0] == values[1] == values[2] == 0.0


def test_the_shadow_stops_reaching(monkeypatch: pytest.MonkeyPatch) -> None:
    values = shading("~1" + "~" * 10)
    lit = [v for v in values[2:] if v != 0.0]
    assert len(lit) == SHADOW_REACH


def test_the_shore_facing_the_light_is_lit() -> None:
    values = shading("~~11~~")
    assert values[2] == RIM_LIGHT_ALPHA, "the first cell of land catches the light"
    assert values[3] == 0.0, "and only the first"


def test_a_neighbouring_holding_casts_a_soft_shadow() -> None:
    values = shading("1122")
    assert values[2] == -EDGE_SHADOW_ALPHA
    assert values[3] == 0.0, "one cell only - a holding is not a cliff"


def test_a_holding_is_flat_inside() -> None:
    assert shading("1111")[1:] == [0.0, 0.0, 0.0]


def test_a_coast_reads_stronger_than_a_border() -> None:
    """Land over water is the edge worth seeing; a border is a hint."""
    assert COAST_SHADOW_ALPHA > EDGE_SHADOW_ALPHA


def test_unclaimed_land_is_still_land() -> None:
    """It is ground standing above the sea, whoever does or does not hold it."""
    assert shading("~..~")[3] == -COAST_SHADOW_ALPHA


def test_the_board_edge_reads_as_open_sea() -> None:
    """Otherwise the top and left rows are ringed in shadow for no reason."""
    assert shading("11~~")[0] == RIM_LIGHT_ALPHA


# --------------------------------------------------------------------------
# Applying it
# --------------------------------------------------------------------------


def test_shading_darkens_and_lightens() -> None:
    grey = (128, 128, 128)
    assert shaded(grey, 0.0) == grey
    assert sum(shaded(grey, -0.5)) < sum(grey)
    assert sum(shaded(grey, 0.5)) > sum(grey)


def test_relief_can_be_turned_off() -> None:
    world = strip("~11~~~")
    factions = Factions.assign(world, "")
    view = Viewport()
    view.fit(len(world.grid[0]) * 2, 8, world, reserved=0)

    off = board_colors(world, view, False, {}, factions, shadows=False)
    on = board_colors(world, view, False, {}, factions, shadows=True)
    assert off != on, "the toggle has to do something"


def test_the_cached_grid_agrees_with_the_function() -> None:
    world = strip("~1122..~~~")
    grid = relief_grid(world, 1)
    assert grid[MIDDLE] == [relief(world, cell, 1) for cell in world.grid[MIDDLE]]


def test_a_second_board_is_not_served_the_first_ones_relief() -> None:
    """The cache holds one entry, matched by identity."""
    first = strip("~11~~~")
    second = strip("~~~11~")
    assert relief_grid(first, 1) != relief_grid(second, 1)
    assert relief_grid(first, 1)[MIDDLE] == [relief(first, cell, 1) for cell in first.grid[MIDDLE]]


def test_zooming_out_recomputes_the_relief() -> None:
    """Neighbours are sampled `step` away, so the shading depends on zoom."""
    world = strip("~1~1~1~1~1")
    assert relief_grid(world, 1) != relief_grid(world, 2)


# --------------------------------------------------------------------------
# What it must not break
# --------------------------------------------------------------------------


def test_the_frontier_still_clears_its_contrast_target_over_shaded_ground() -> None:
    """Shading goes under the frontier so `frontier_color` can allow for it."""
    world = strip("~1122~~", owners={"1": uuid4(), "2": uuid4()})
    factions = Factions.assign(world, "")
    for cell in world.grid[0]:
        side = frontier_side(world, factions, cell, 1)
        if side is None:
            continue
        ground = shaded(cell_color(cell, {}, factions), relief(world, cell, 1))
        edge = frontier_color(factions.colors[side], ground, BORDER_ALPHA, BORDER_CONTRAST)
        assert contrast(edge, ground) >= BORDER_CONTRAST


def test_every_cell_still_gets_a_colour() -> None:
    world = strip("~1122..~~")
    factions = Factions.assign(world, "")
    view = Viewport()
    view.fit(len(world.grid[0]) * 2, 8, world, reserved=0)
    for row in board_colors(world, view, False, {}, factions, shadows=True):
        for color in row:
            assert len(color) == 3
            assert all(0 <= channel <= 255 for channel in color)
