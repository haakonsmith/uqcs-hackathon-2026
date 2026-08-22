"""Turning board state into escape sequences. Presentation only."""

from __future__ import annotations

import colorsys
from typing import TYPE_CHECKING

import blessed

from protocol import terrain

if TYPE_CHECKING:
    from client.viewport import Viewport

# Each map cell is drawn two columns wide, because terminal cells are roughly
# twice as tall as they are wide and a 1:1 mapping shears the map vertically.
CELL_WIDTH = 2

Color = tuple[int, int, int]

# A per-territory highlight: an explicit colour, True for the territory's own
# colour, or None/False to leave the terrain showing through.
Highlight = Color | bool | None

# Irrational step around the hue circle, so neighbouring ids never share a hue.
_HUE_STEP = 0.6180339887498949


def territory_color(territory_id: int) -> Color:
    hue = (territory_id * _HUE_STEP) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.88)
    return round(r * 255), round(g * 255), round(b * 255)


def cell_color(cell: terrain.Cell, overlay: bool, highlights: dict[int, Highlight]) -> Color:
    """The colour one cell is drawn in.

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


def render(
    term: blessed.Terminal,
    world: terrain.WorldMap,
    view: Viewport,
    overlay: bool,
    highlights: dict[int, Highlight],
) -> str:
    """Colour render of the visible slice, two columns per cell.

    At zoom > 1 this steps over the grid rather than reading all of it, so the
    work per frame is bounded by the screen, not by how much board is shown.
    """
    lines: list[str] = []
    for row in world.grid[view.y : view.y + view.span_y : view.zoom]:
        parts: list[str] = []
        for cell in row[view.x : view.x + view.span_x : view.zoom]:
            parts.append(term.on_color_rgb(*cell_color(cell, overlay, highlights)))
            parts.append(" " * CELL_WIDTH)
        # Reset first so the erase uses the default background, not the last
        # cell's colour, then wipe anything the previous frame left to the right.
        parts.append(term.normal)
        parts.append(term.clear_eol)
        lines.append("".join(parts))
    return "\n".join(lines)


def status(world: terrain.WorldMap, view: Viewport, hover: str) -> str:
    position = f"[{view.x},{view.y}] {view.span_x}x{view.span_y} of {world.width}x{world.height} @{view.zoom}x"
    return f" {position}  {hover}   arrows/wheel scroll  +/- zoom  [o]verlay  [q]uit "
