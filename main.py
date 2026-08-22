"""Minimal example: generate a board, render it, point at it.

    python main.py

The board is larger than the terminal, so only a slice is on screen. Arrow
keys and the mouse wheel scroll it (shift+wheel scrolls sideways), `+` and
`-` zoom in and out by subsampling, the mouse inspects the cell under the
cursor, `o` toggles the ownership overlay, `q` quits.

For live tweaking of the generation parameters, run `viewer.py`.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import blessed

import terrain
from viewer import CELL_WIDTH, Color, territory_color

SEED = 42

# Bigger than any terminal, so there is something to scroll around.
BOARD_WIDTH = 160
BOARD_HEIGHT = 90

SCROLL_KEYS = {"KEY_LEFT": (-1, 0), "KEY_RIGHT": (1, 0), "KEY_UP": (0, -1), "KEY_DOWN": (0, 1)}

# Cells per wheel notch. One would feel glacial next to the arrow keys.
WHEEL_STEP = 3


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


# All-motion mouse tracking, and SGR coordinates so positions past column 95
# survive the encoding.
MOUSE_MODES = (1003, 1006)


@contextlib.contextmanager
def mouse_tracking(term: blessed.Terminal) -> Iterator[None]:
    """Turn on mouse tracking without asking the terminal's permission first.

    blessed's `mouse_enabled()` queries each mode with DECRQM and skips any the
    reply claims is already on. Ghostty answers that 1003 is enabled on a fresh
    terminal when it isn't, so blessed sends no enable and no motion events ever
    arrive - hover silently does nothing there while working elsewhere.

    These set the modes directly. They also update blessed's mode cache, which
    is what its input parser consults to decide whether to decode mouse
    sequences, so writing the raw escapes instead would enable the terminal but
    leave the decoder handing back `CSI`, `<`, `3`, `5` as separate keystrokes.
    """
    term._dec_mode_set_enabled(*MOUSE_MODES)
    try:
        yield
    finally:
        term._dec_mode_set_disabled(*MOUSE_MODES)


@dataclass
class Viewport:
    """Which slice of the board is on screen.

    `cols`/`rows` are screen cells; `zoom` is how many map cells each screen
    cell stands for. Zooming out subsamples - one map cell in every `zoom` is
    drawn and the rest are skipped, so the cost of a frame stays fixed no
    matter how much board is on screen.
    """

    x: int = 0
    y: int = 0
    cols: int = 1
    rows: int = 1
    zoom: int = 1
    # Screen cells actually drawn. Smaller than cols/rows when zoomed far
    # enough out that the board runs out before the screen does.
    drawn_cols: int = 1
    drawn_rows: int = 1

    @property
    def span_x(self) -> int:
        """Width of board covered, in map cells."""
        return self.cols * self.zoom

    @property
    def span_y(self) -> int:
        return self.rows * self.zoom

    def fit(self, term: blessed.Terminal, world: terrain.WorldMap) -> None:
        """Size the viewport to the terminal, leaving a row for the status bar."""
        self.cols = max(1, term.width // CELL_WIDTH)
        self.rows = max(1, term.height - 1)
        self.zoom = min(self.zoom, self.max_zoom(world))
        self.scroll(0, 0, world)

    def max_zoom(self, world: terrain.WorldMap) -> int:
        """The zoom at which the whole board fits. Beyond it just adds margin."""
        return max(1, _ceil_div(world.width, self.cols), _ceil_div(world.height, self.rows))

    def scroll(self, dx: int, dy: int, world: terrain.WorldMap) -> bool:
        """Move by (dx, dy) map cells, clamped. True if the view actually moved."""
        before = (self.x, self.y)
        self.x = max(0, min(self.x + dx, world.width - self.span_x))
        self.y = max(0, min(self.y + dy, world.height - self.span_y))
        self.drawn_cols = min(self.cols, _ceil_div(world.width - self.x, self.zoom))
        self.drawn_rows = min(self.rows, _ceil_div(world.height - self.y, self.zoom))
        return (self.x, self.y) != before

    def set_zoom(self, zoom: int, world: terrain.WorldMap) -> bool:
        """Change zoom, keeping the centre of the view put. True if it changed."""
        zoom = max(1, min(zoom, self.max_zoom(world)))
        if zoom == self.zoom:
            return False

        centre_x = self.x + self.span_x // 2
        centre_y = self.y + self.span_y // 2
        self.zoom = zoom
        self.x = centre_x - self.span_x // 2
        self.y = centre_y - self.span_y // 2
        self.scroll(0, 0, world)
        return True

    def to_map(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        """Screen position to map cell. Columns are 2:1 to cells."""
        return (
            self.x + (screen_x // CELL_WIDTH) * self.zoom,
            self.y + screen_y * self.zoom,
        )

    def covers(self, screen_y: int) -> bool:
        """False below the map, so the status bar isn't mistaken for board."""
        return 0 <= screen_y < self.drawn_rows


def wheel_delta(name: str) -> tuple[int, int] | None:
    """Scroll delta for a wheel event name, or None if it isn't one.

    blessed reports these as MOUSE_SCROLL_UP / MOUSE_SCROLL_DOWN, with
    modifiers folded into the name (MOUSE_SHIFT_SCROLL_UP). Shift scrolls
    sideways, matching how most terminal apps treat it.
    """
    if "SCROLL_UP" in name:
        step = -WHEEL_STEP
    elif "SCROLL_DOWN" in name:
        step = WHEEL_STEP
    else:
        return None
    return (step, 0) if "SHIFT" in name else (0, step)


def describe(world: terrain.WorldMap) -> None:
    """Poke at the board the way game logic would."""
    print(terrain.summary(world))

    home = world.territories[0]
    print(f"\nterritory {home.id} sits on continent {home.continent} and covers {home.size} cells")
    print(f"  borders by land: {sorted(home.land_neighbours)}")
    print(f"  reachable by sea: {sorted(home.sea_neighbours)}")

    # Terrain carries the movement rules, so pathfinding reads straight off it.
    blocked = sum(1 for x, y in home.cells if not world.cell(x, y).terrain.passable)
    print(f"  {blocked} of its cells are impassable (water or mountain)")


def render(
    term: blessed.Terminal,
    world: terrain.WorldMap,
    view: Viewport,
    overlay: bool,
    highlight_territories: dict[int, Color | bool | None],
) -> str:
    """Colour render of the visible slice, two columns per cell.

    At zoom > 1 this steps over the grid rather than reading all of it, so the
    work per frame is bounded by the screen, not by how much board is shown.
    """
    lines: list[str] = []
    for row in world.grid[view.y : view.y + view.span_y : view.zoom]:
        parts: list[str] = []
        for cell in row[view.x : view.x + view.span_x : view.zoom]:
            if cell.territory is not None:
                highlight = highlight_territories[cell.territory]
                if highlight:
                    color = territory_color(cell.territory) if highlight == True or overlay else highlight
                    parts.append(term.on_color_rgb(*color))

            else:
                parts.append(term.on_color_rgb(*cell.terrain.color))
            parts.append(" " * CELL_WIDTH)
        # Reset first so the erase uses the default background, not the last
        # cell's colour, then wipe anything the previous frame left to the right.
        parts.append(term.normal)
        parts.append(term.clear_eol)
        lines.append("".join(parts))
    return "\n".join(lines)


def cell_at_cursor(world: terrain.WorldMap, view: Viewport, x: int, y: int) -> terrain.Cell | None:
    """The cell under a screen position, or None if the cursor is off the board."""
    if not view.covers(y):
        return None

    map_x, map_y = view.to_map(x, y)
    if not (0 <= map_x < world.width and 0 <= map_y < world.height):
        return None

    return world.cell(map_x, map_y)


def inspect(
    world: terrain.WorldMap,
    view: Viewport,
    cursor: tuple[int, int] | None,
) -> tuple[str, int | None]:
    """Hover text and highlighted territory for the last known cursor position.

    Scrolling moves the board under a stationary mouse, so this is recomputed
    after every scroll rather than only on mouse motion.
    """
    if cursor is None:
        return "move the mouse over the map", None

    cell = cell_at_cursor(world, view, *cursor)
    if cell is None:
        return "off the board", None

    where = f"({cell.x},{cell.y}) {cell.terrain.label} height {cell.height:.2f}"
    if cell.territory is None:
        return f"{where} - unclaimed", None

    owner = world.territories[cell.territory]
    detail = f"territory {owner.id}, continent {owner.continent}, {len(owner.neighbours)} neighbours"
    return f"{where} - {detail}", owner.id


def status(world: terrain.WorldMap, view: Viewport, hover: str) -> str:
    position = f"[{view.x},{view.y}] {view.span_x}x{view.span_y} of {world.width}x{world.height} @{view.zoom}x"
    return f" {position}  {hover}   arrows/wheel scroll  +/- zoom  [o]verlay  [q]uit "


def main() -> None:
    world = terrain.generate(
        width=BOARD_WIDTH,
        height=BOARD_HEIGHT,
        scale=10,
        octaves=6,
        seed=SEED,
        water_fraction=0.68,
    )
    term = blessed.Terminal()

    view = Viewport()
    overlay = False
    cursor: tuple[int, int] | None = None

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), mouse_tracking(term):
        view.fit(term, world)
        size = (term.width, term.height)
        hover, highlight_territory = inspect(world, view, cursor)
        dirty = True

        while True:
            if dirty:
                print(term.home + render(term, world, view, overlay, highlight_territory), end="")
                print(
                    term.move_xy(0, view.drawn_rows) + term.ljust(status(world, view, hover)[: term.width]),
                    end="",
                )
                # Zooming out shrinks the map below the window, so wipe whatever
                # the previous frame left under the status bar. Skip it when the
                # bar is already on the last row: moving past the bottom clamps
                # back onto the bar and the erase would take the bar with it.
                if view.drawn_rows + 1 < term.height:
                    print(term.move_xy(0, view.drawn_rows + 1) + term.clear_eos, end="")
                sys.stdout.flush()
                dirty = False

            key = term.inkey(timeout=0.25)

            # A resize changes how much of the board fits, so refit before drawing.
            if (term.width, term.height) != size:
                size = (term.width, term.height)
                view.fit(term, world)
                hover, highlight_territory = inspect(world, view, cursor)
                dirty = True
                continue

            if not key:
                continue

            if key.lower() == "q":
                break

            moved = False

            if key == "o":
                overlay = not overlay
                dirty = True
            elif key in "+=":
                moved = view.set_zoom(view.zoom - 1, world)
            elif key in "-_":
                moved = view.set_zoom(view.zoom + 1, world)
            elif key.name in SCROLL_KEYS:
                # Step by one screen cell, so scrolling feels the same at any zoom.
                dx, dy = SCROLL_KEYS[key.name]
                moved = view.scroll(dx * view.zoom, dy * view.zoom, world)
            elif key.name and key.name.startswith("MOUSE_"):
                # Keystroke reports mouse position as mouse_xy, not .x / .y,
                # and gives (-1, -1) for anything that isn't a mouse event.
                mx, my = key.mouse_xy
                if (mx, my) != (-1, -1):
                    cursor = (mx, my)

                delta = wheel_delta(key.name)
                if delta is not None:
                    moved = view.scroll(delta[0] * view.zoom, delta[1] * view.zoom, world)
                elif (mx, my) != (-1, -1):
                    hover, highlight_territory = inspect(world, view, cursor)
                    dirty = True

            if moved:
                # The board shifted under the cursor, so what it points at changed.
                hover, highlight_territory = inspect(world, view, cursor)
                dirty = True

    describe(world)


if __name__ == "__main__":
    main()
