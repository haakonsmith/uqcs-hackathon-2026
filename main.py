"""Minimal example: generate a board, render it, point at it.

    python main.py

The board is larger than the terminal, so only a slice is on screen. Arrow
keys and the mouse wheel scroll it (shift+wheel scrolls sideways), the mouse
inspects the cell under the cursor, `o` toggles the ownership overlay, `q`
quits.

For live tweaking of the generation parameters, run `viewer.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import blessed

import terrain
from viewer import CELL_WIDTH, territory_color

SEED = 42

# Bigger than any terminal, so there is something to scroll around.
BOARD_WIDTH = 160
BOARD_HEIGHT = 90

SCROLL_KEYS = {"KEY_LEFT": (-1, 0), "KEY_RIGHT": (1, 0), "KEY_UP": (0, -1), "KEY_DOWN": (0, 1)}

# Cells per wheel notch. One would feel glacial next to the arrow keys.
WHEEL_STEP = 3


@dataclass
class Viewport:
    """Which slice of the board is on screen, measured in map cells."""

    x: int = 0
    y: int = 0
    cols: int = 1
    rows: int = 1

    def fit(self, term: blessed.Terminal, world: terrain.WorldMap) -> None:
        """Size the viewport to the terminal, leaving a row for the status bar."""
        self.cols = max(1, min(world.width, term.width // CELL_WIDTH))
        self.rows = max(1, min(world.height, term.height - 1))
        self.scroll(0, 0, world)

    def scroll(self, dx: int, dy: int, world: terrain.WorldMap) -> bool:
        """Move by (dx, dy) map cells, clamped. True if the view actually moved."""
        before = (self.x, self.y)
        self.x = max(0, min(self.x + dx, world.width - self.cols))
        self.y = max(0, min(self.y + dy, world.height - self.rows))
        return (self.x, self.y) != before

    def to_map(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        """Screen position to map cell. Columns are 2:1 to cells."""
        return self.x + screen_x // CELL_WIDTH, self.y + screen_y

    def covers(self, screen_y: int) -> bool:
        """False below the map, so the status bar isn't mistaken for board."""
        return 0 <= screen_y < self.rows


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
    highlight_territory: int | None,
) -> str:
    """Colour render of the visible slice, two columns per cell."""
    lines: list[str] = []
    for row in world.grid[view.y : view.y + view.rows]:
        parts: list[str] = []
        for cell in row[view.x : view.x + view.cols]:
            highlighted = highlight_territory is not None and cell.territory == highlight_territory
            if cell.territory is not None and (highlighted or overlay):
                parts.append(term.on_color_rgb(*territory_color(cell.territory)))
            else:
                parts.append(term.on_color_rgb(*cell.terrain.color))
            parts.append(" " * CELL_WIDTH)
        parts.append(term.normal)
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
    position = f"[{view.x},{view.y}] {view.cols}x{view.rows} of {world.width}x{world.height}"
    return f" {position}  {hover}   arrows/wheel scroll  [o]verlay  [q]uit "


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

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), term.mouse_enabled(report_motion=True):
        view.fit(term, world)
        size = (term.width, term.height)
        hover, highlight_territory = inspect(world, view, cursor)
        dirty = True

        while True:
            if dirty:
                print(term.home + render(term, world, view, overlay, highlight_territory), end="")
                print(
                    term.move_xy(0, view.rows) + term.ljust(status(world, view, hover)[: term.width]),
                    end="",
                    flush=True,
                )
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

            scrolled = False

            if key == "o":
                overlay = not overlay
                dirty = True
            elif key.name in SCROLL_KEYS:
                dx, dy = SCROLL_KEYS[key.name]
                scrolled = view.scroll(dx, dy, world)
            elif key.name and key.name.startswith("MOUSE_"):
                # Keystroke reports mouse position as mouse_xy, not .x / .y,
                # and gives (-1, -1) for anything that isn't a mouse event.
                mx, my = key.mouse_xy
                if (mx, my) != (-1, -1):
                    cursor = (mx, my)

                delta = wheel_delta(key.name)
                if delta is not None:
                    scrolled = view.scroll(*delta, world)
                elif (mx, my) != (-1, -1):
                    hover, highlight_territory = inspect(world, view, cursor)
                    dirty = True

            if scrolled:
                # The board moved under the cursor, so what it points at changed.
                hover, highlight_territory = inspect(world, view, cursor)
                dirty = True

    describe(world)


if __name__ == "__main__":
    main()
