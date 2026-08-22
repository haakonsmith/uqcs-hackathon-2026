"""Turning board state into escape sequences. Presentation only."""

from __future__ import annotations

import colorsys
from collections import Counter
from typing import TYPE_CHECKING

from client.palette import Color, Factions, blend
from client.screen import Screen
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
#
# Two strengths, switched with [o]: the faint one to read the terrain, the bold
# one to see at a glance who holds what.
BORDER_ALPHA = 0.1
BORDER_ALPHA_BOLD = 0.5

# The same for a hovered territory, which is filled in its holder's colour so
# that pointing at ground says whose it is. Thinner than the frontier, so the
# border still draws a line around a holding the mouse is sitting on, and so
# the coast and the hills under the fill stay legible through it.
HOVER_ALPHA = 0.35

# Garrison counts, drawn over the middle of each territory. Bright enough to
# carry against a solid chip of the holder's colour, which is what makes a
# number legible over ground as light as sand or as dark as deep water.
GARRISON_LIGHT: Color = (245, 245, 245)
GARRISON_DARK: Color = (16, 16, 16)

# The legend, boxed in the top-right of the board.
LEGEND_TITLE = "FORCES"
KEYS_TITLE = "KEYS"
LEGEND_BG: Color = (18, 26, 46)
LEGEND_FG: Color = (226, 226, 216)
LEGEND_KEY: Color = (240, 196, 96)
LEGEND_SWATCH = "██"
LEGEND_PAD = " "

# What each key does, in the order you would reach for it during a round. The
# same keys are on the status bar, which only has room for the ones the current
# phase answers; this is the whole set, for a player who has not learnt them.
KEY_HELP: tuple[tuple[str, str], ...] = (
    ("arrows", "scroll the map"),
    ("+/-", "zoom in and out"),
    ("o", "border strength"),
    ("l", "this legend"),
    ("n/N", "pick a territory"),
    ("space", "place troops here"),
    ("p", "place all troops"),
    ("1-9", "how many at a time"),
    ("m", "march from here"),
    ("c", "clear your plan"),
    ("s", "write a solution"),
    ("f", "finish the phase"),
    ("tab", "scoreboard"),
    ("esc", "clear selection"),
    ("q", "quit"),
)

# A run of legend text, and the colour it is drawn in - None for the legend's
# own foreground, so only a swatch or a key name has to name a colour.
Segment = tuple[str, Color | None]

# A titled section of the legend, and the rows under it.
Block = tuple[str, list[list[Segment]]]


def territory_color(territory_id: int) -> Color:
    hue = (territory_id * _HUE_STEP) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55, 0.88)
    return round(r * 255), round(g * 255), round(b * 255)


def side_color(factions: Factions, territory_id: int) -> Color:
    """The colour a territory's holder is drawn in, or its own hue while unheld.

    One place, so a highlight and the frontier around it cannot end up asking
    for the same territory and getting two different colours.
    """
    side = factions.side_of.get(territory_id)
    if side is None:
        return territory_color(territory_id)
    return factions.colors[side]


def cell_color(cell: terrain.Cell, highlights: dict[int, Highlight], factions: Factions) -> Color:
    """The colour one cell is drawn in, before any frontier is laid over it.

    Total by construction: every branch returns a colour, so a cell can never
    be emitted without one and inherit whatever the previous cell left behind.
    """
    if cell.territory is None:
        return cell.terrain.color

    highlight = highlights.get(cell.territory)
    if isinstance(highlight, tuple):
        return highlight
    if highlight:
        # Its holder's colour rather than a hue of its own, so the fill under
        # the mouse and the line around the holding are the one colour and the
        # highlight reads as that side's ground lighting up.
        return blend(side_color(factions, cell.territory), cell.terrain.color, HOVER_ALPHA)
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


def board_colors(
    world: terrain.WorldMap,
    view: Viewport,
    bold_borders: bool,
    highlights: dict[int, Highlight],
    factions: Factions,
) -> list[list[Color]]:
    """The colour of every screen cell, without any escape sequences.

    Separated from the drawing so a frame can be compared with the one before
    it: two grids of tuples diff cheaply, two strings of escapes do not.
    """
    border_alpha = BORDER_ALPHA_BOLD if bold_borders else BORDER_ALPHA
    grid: list[list[Color]] = []
    for row in world.grid[view.y : view.y + view.span_y : view.zoom]:
        colors: list[Color] = []
        for cell in row[view.x : view.x + view.span_x : view.zoom]:
            color = cell_color(cell, highlights, factions)
            side = frontier_side(world, factions, cell, view.zoom)
            if side is not None:
                color = blend(factions.colors[side], color, border_alpha)
            colors.append(color)
        grid.append(colors)
    return grid


def draw_board(
    screen: Screen,
    world: terrain.WorldMap,
    view: Viewport,
    bold_borders: bool,
    highlights: dict[int, Highlight],
    factions: Factions,
) -> None:
    """Compose the visible slice of terrain into `screen`."""
    for y, colors in enumerate(board_colors(world, view, bold_borders, highlights, factions)):
        for x, color in enumerate(colors):
            # Two columns per map cell, so terrain is not sheared vertically.
            for column in range(CELL_WIDTH):
                screen.set(x * CELL_WIDTH + column, y, (" ", None, color, False))


def _readable_on(background: Color) -> Color:
    """Black or white, whichever the eye can pick out against `background`.

    Rec. 601 luma rather than a plain average: green carries most of what the
    eye reads as brightness and blue almost none, so averaging would call the
    allied blue light and print black on it.
    """
    luma = 0.299 * background[0] + 0.587 * background[1] + 0.114 * background[2]
    return GARRISON_DARK if luma > 140 else GARRISON_LIGHT


def garrison_anchor(world: terrain.WorldMap, territory: terrain.Territory) -> tuple[int, int]:
    """The map cell a territory's count is drawn on.

    Usually the centroid, but the centroid of a crescent lies outside it and a
    number there would sit on someone else's ground, labelling the wrong side.
    Falls back to whichever cell the territory actually owns is nearest that
    point, which only walks the region when the centroid misses.
    """
    centre_x, centre_y = territory.centroid
    x, y = round(centre_x), round(centre_y)
    if 0 <= x < world.width and 0 <= y < world.height and world.grid[y][x].territory == territory.id:
        return x, y
    return min(territory.cells, key=lambda cell: (cell[0] - centre_x) ** 2 + (cell[1] - centre_y) ** 2)


def draw_garrisons(
    screen: Screen,
    world: terrain.WorldMap,
    view: Viewport,
    factions: Factions,
    placements: dict[int, int] | None = None,
) -> None:
    """How many soldiers sit on each visible territory, over its middle.

    Composed after the terrain, the way the legend is, so the map stays a
    plain grid of cells and no row has to leave room for text midway along it.

    `placements` is troops this player has promised to a territory but not yet
    put on the board, drawn as a `(+n)` after the count. Separate from the count
    rather than added into it because the two are not the same thing: the count
    is what would defend the ground if the phase ended now, and the `(+n)` is
    what a plan the player can still tear up would add to it.

    Each count carries its holder's colour as a background rather than being
    bare text, so it reads as a chip on the map instead of a number floating
    over whatever happens to be underneath.

    Labels that would overlap are dropped rather than overprinted, since two
    counts sharing a cell read as one number that belongs to neither. They are
    placed in territory order, so which of a crowded pair survives stays put
    from frame to frame instead of flickering as the board moves.
    """
    board_width = view.drawn_cols * CELL_WIDTH
    taken: set[tuple[int, int]] = set()
    pending = placements or {}

    for territory in world.territories:
        # Unclaimed ground has no garrison to report, only a colour.
        if territory.owner is None:
            continue

        anchor_x, anchor_y = garrison_anchor(world, territory)
        if not view.shows(anchor_x, anchor_y):
            continue

        x, y = view.to_screen(anchor_x, anchor_y)
        if not view.covers(y):
            continue

        placed = pending.get(territory.id, 0)
        label = f"{territory.soldiers}(+{placed})" if placed else str(territory.soldiers)
        # Centred on the cell, which is two columns wide; a count wider than
        # that spreads either side of it rather than hanging off to the right.
        left = x + (CELL_WIDTH - len(label)) // 2
        if left < 0 or left + len(label) > board_width:
            continue

        span = {(left + offset, y) for offset in range(len(label))}
        if span & taken:
            continue
        taken |= span

        background = side_color(factions, territory.id)
        screen.text(left, y, label, fg=_readable_on(background), bg=background, bold=True)


def _force_rows(factions: Factions) -> list[list[Segment]]:
    """One row per side: frontier colour, who it is, how many territories."""
    held = Counter(factions.side_of.values())
    sides = [(factions.colors[side], factions.label(side), held[side]) for side in range(factions.count)]
    if not sides:
        return []

    # Every label the same length, so the box is a box and the counts line up.
    name_width = max(len(name) for _, name, _ in sides)
    count_width = max(len(str(count)) for _, _, count in sides)
    return [
        [(LEGEND_PAD, None), (LEGEND_SWATCH, color), (f" {name:<{name_width}}  {count:>{count_width}}{LEGEND_PAD}", None)]
        for color, name, count in sides
    ]


def _key_rows() -> list[list[Segment]]:
    """One row per key: the key itself in its own colour, then what it does."""
    key_width = max(len(key) for key, _ in KEY_HELP)
    return [[(LEGEND_PAD, None), (f"{key:>{key_width}}", LEGEND_KEY), (f"  {action}{LEGEND_PAD}", None)] for key, action in KEY_HELP]


def _row_width(row: list[Segment]) -> int:
    return sum(len(text) for text, _ in row)


def _box_size(blocks: list[Block]) -> tuple[int, int]:
    """How wide and tall the legend box is, in screen cells.

    The widest row, and every row plus the title above it. Measured from the
    text rather than counted out separately, which would be a second place to
    keep the box and its contents in step.
    """
    width = max(max(_row_width(row) for row in rows) for _, rows in blocks)
    return width, sum(1 + len(rows) for _, rows in blocks)


def _box_fits(blocks: list[Block], view: Viewport) -> bool:
    """Whether the box has room on the board, with a column of map either side."""
    width, height = _box_size(blocks)
    return width + 2 <= view.drawn_cols * CELL_WIDTH and height <= view.drawn_rows


def draw_legend(screen: Screen, view: Viewport, factions: Factions) -> None:
    """Which colour is whose and what to press, boxed over the top-right.

    Composed over the map rather than blitted into it, so the map stays a
    plain grid of cells.

    The keys are dropped, and then the whole box, when the board on screen is
    too small to hold them. Keys go first because which colour is whose cannot
    be worked out from anywhere else, while the keys are also on the status bar.
    """
    blocks: list[Block] = [(LEGEND_TITLE, _force_rows(factions)), (KEYS_TITLE, _key_rows())]
    blocks = [(title, rows) for title, rows in blocks if rows]

    while blocks and not _box_fits(blocks, view):
        _ = blocks.pop()
    if not blocks:
        return

    width, _height = _box_size(blocks)
    left = view.drawn_cols * CELL_WIDTH - width - 1

    row_y = 0
    for title, rows in blocks:
        screen.text(left, row_y, title.center(width), fg=LEGEND_FG, bg=LEGEND_BG, bold=True)
        row_y += 1
        for row in rows:
            # Pad to the widest row, so a short one does not leave a notch of
            # bare map inside the box.
            screen.text(left, row_y, " " * width, fg=LEGEND_FG, bg=LEGEND_BG)
            column = left
            for text, color in row:
                screen.text(column, row_y, text, fg=color or LEGEND_FG, bg=LEGEND_BG)
                column += len(text)
            row_y += 1
