"""Turning board state into escape sequences. Presentation only."""

from __future__ import annotations

import colorsys
from collections import Counter
from typing import TYPE_CHECKING

import blessed

from client.palette import Color, Factions, blend
from protocol import terrain

if TYPE_CHECKING:
    from client.viewport import Viewport

# Each map cell is drawn two columns wide, because terminal cells are roughly
# twice as tall as they are wide and a 1:1 mapping shears the map vertically.
CELL_WIDTH = 2

# A per-territory highlight: an explicit colour, True for the territory's own
# colour, or None/False to leave the terrain showing through.
Highlight = Color | bool | None

# Irrational step around the hue circle, so neighbouring ids never share a hue.
_HUE_STEP = 0.6180339887498949

# How much of a frontier cell is the side's colour rather than the ground under
# it. Short of opaque so the border reads as a line drawn on the map instead of
# a ring of territory in its own right - the terrain still shows through it.
BORDER_ALPHA = 0.72

# The legend, boxed in the top-right of the board.
LEGEND_TITLE = "FORCES"
LEGEND_BG: Color = (18, 26, 46)
LEGEND_FG: Color = (226, 226, 216)
LEGEND_SWATCH = "██"
LEGEND_PAD = " "


def territory_color(territory_id: int) -> Color:
    hue = (territory_id * _HUE_STEP) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.88)
    return round(r * 255), round(g * 255), round(b * 255)


def cell_color(cell: terrain.Cell, overlay: bool, highlights: dict[int, Highlight]) -> Color:
    """The colour one cell is drawn in, before any frontier is laid over it.

    Total by construction: every branch returns a colour, so a cell can never
    be emitted without one and inherit whatever the previous cell left behind.
    """
    if cell.territory is None:
        return cell.terrain.color

    highlight = highlights.get(cell.territory)
    if isinstance(highlight, tuple):
        return highlight
    if highlight or overlay:
        return territory_color(cell.territory)
    return cell.terrain.color


def side_at(world: terrain.WorldMap, factions: Factions, x: int, y: int) -> int | None:
    """Which side holds a cell. None off the board, so the map edge is a frontier."""
    if not (0 <= x < world.width and 0 <= y < world.height):
        return None

    territory = world.grid[y][x].territory
    if territory is None:
        return None
    return factions.side_of.get(territory)


def frontier_side(world: terrain.WorldMap, factions: Factions, cell: terrain.Cell, step: int) -> int | None:
    """The side whose frontier this cell sits on, or None well inside a holding.

    Two touching territories held by the same player answer the same side, so
    nothing is drawn between them: the line follows the outline of everything
    one player holds rather than of each region separately.

    Neighbours are read `step` cells away rather than one. At zoom > 1 only one
    cell in every `step` is drawn, so this compares a drawn cell with the cells
    drawn either side of it; a border tested at one-cell spacing would be
    sampled straight out of the picture at anything but full zoom.
    """
    side = side_at(world, factions, cell.x, cell.y)
    if side is None:
        return None

    for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step)):
        if side_at(world, factions, cell.x + dx, cell.y + dy) != side:
            return side
    return None


def render(
    term: blessed.Terminal,
    world: terrain.WorldMap,
    view: Viewport,
    overlay: bool,
    highlights: dict[int, Highlight],
    factions: Factions,
) -> str:
    """Colour render of the visible slice, two columns per cell.

    At zoom > 1 this steps over the grid rather than reading all of it, so the
    work per frame is bounded by the screen, not by how much board is shown.
    """
    lines: list[str] = []
    for row in world.grid[view.y : view.y + view.span_y : view.zoom]:
        parts: list[str] = []
        for cell in row[view.x : view.x + view.span_x : view.zoom]:
            color = cell_color(cell, overlay, highlights)
            side = frontier_side(world, factions, cell, view.zoom)
            if side is not None:
                color = blend(factions.colors[side], color, BORDER_ALPHA)
            parts.append(term.on_color_rgb(*color))
            parts.append(" " * CELL_WIDTH)
        # Reset first so the erase uses the default background, not the last
        # cell's colour, then wipe anything the previous frame left to the right.
        parts.append(term.normal)
        parts.append(term.clear_eol)
        lines.append("".join(parts))
    return "\n".join(lines)


def _legend_rows(factions: Factions) -> list[tuple[Color, str, int]]:
    """One row per side: frontier colour, who it is, how many territories."""
    held = Counter(factions.side_of.values())
    return [(factions.colors[side], factions.label(side), held[side]) for side in range(factions.count)]


def legend_panel(term: blessed.Terminal, view: Viewport, factions: Factions) -> str:
    """Which colour is whose, boxed over the top-right of the board.

    Laid over the map after it is drawn rather than blitted into it, so the map
    stays a plain grid of cells. Drawn every frame because the frame under it
    paints the whole board, and skipped entirely when the board on screen is
    too small to hold the box - a legend that overhangs the map would be
    half-erased by the next `clear_eol`.
    """
    rows = _legend_rows(factions)
    if not rows:
        return ""

    # Every label the same length, so the box is a box and the counts line up.
    # The width follows from the text rather than being counted out separately,
    # which would be a second place to keep the two in step.
    name_width = max(len(name) for _, name, _ in rows)
    count_width = max(len(str(count)) for _, _, count in rows)
    labels = [f" {name:<{name_width}}  {count:>{count_width}}{LEGEND_PAD}" for _, name, count in rows]
    width = len(LEGEND_PAD) + len(LEGEND_SWATCH) + len(labels[0])

    board_width = view.drawn_cols * CELL_WIDTH
    # A column of board either side, so the box reads as sitting on the map.
    if width + 2 > board_width or len(rows) + 1 > view.drawn_rows:
        return ""

    left = board_width - width - 1
    background = term.on_color_rgb(*LEGEND_BG)
    foreground = term.color_rgb(*LEGEND_FG)

    output: list[str] = [term.move_xy(left, 0) + background + foreground + term.bold + LEGEND_TITLE.center(width) + term.normal]
    for row, ((color, _name, _count), label) in enumerate(zip(rows, labels, strict=True), start=1):
        # Each row restates the background: `term.normal` at the end of the one
        # before it clears the colour along with the bold and the swatch.
        output.append(term.move_xy(left, row) + background + LEGEND_PAD + term.color_rgb(*color) + LEGEND_SWATCH + foreground + label + term.normal)
    return "".join(output)


def status(world: terrain.WorldMap, view: Viewport, hover: str) -> str:
    position = f"[{view.x},{view.y}] {view.span_x}x{view.span_y} of {world.width}x{world.height} @{view.zoom}x"
    return f" {position}  {hover}   arrows/wheel scroll  +/- zoom  [o]verlay  [l]egend  [q]uit "
