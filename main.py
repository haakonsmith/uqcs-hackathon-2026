"""Minimal example: generate a board, render it, point at it.

    python main.py

The board is larger than the terminal, so only a slice is on screen. Arrow
keys scroll it, the mouse inspects the cell under the cursor, `o` toggles the
ownership overlay, `q` quits.

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

    def scroll(self, dx: int, dy: int, world: terrain.WorldMap) -> None:
        """Move by (dx, dy) map cells, clamped so the view stays on the board."""
        self.x = max(0, min(self.x + dx, world.width - self.cols))
        self.y = max(0, min(self.y + dy, world.height - self.rows))

    def to_map(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        """Screen position to map cell. Columns are 2:1 to cells."""
        return self.x + screen_x // CELL_WIDTH, self.y + screen_y

    def covers(self, screen_y: int) -> bool:
        """False below the map, so the status bar isn't mistaken for board."""
        return 0 <= screen_y < self.rows


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


def at_cursor(world: terrain.WorldMap, view: Viewport, x: int, y: int) -> str:
    """Describe whatever the mouse is over."""
    cell = cell_at_cursor(world, view, x, y)
    if cell is None:
        return ""

    where = f"({cell.x},{cell.y}) {cell.terrain.label} height {cell.height:.2f}"
    if cell.territory is None:
        return f"{where} - unclaimed"

    owner = world.territories[cell.territory]
    return f"{where} - territory {owner.id}, continent {owner.continent}, {len(owner.neighbours)} neighbours"


def status(world: terrain.WorldMap, view: Viewport, hover: str) -> str:
    position = f"[{view.x},{view.y}] {view.cols}x{view.rows} of {world.width}x{world.height}"
    return f" {position}  {hover}   arrows scroll  [o]verlay  [q]uit "


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
    hover = "move the mouse over the map"
    highlight_territory: int | None = None

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), term.mouse_enabled(report_motion=True):
        view.fit(term, world)
        size = (term.width, term.height)
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
                dirty = True
                continue

            if not key:
                continue

            if key.lower() == "q":
                break

            if key == "o":
                overlay = not overlay
                dirty = True
            elif key.name in SCROLL_KEYS:
                dx, dy = SCROLL_KEYS[key.name]
                before = (view.x, view.y)
                view.scroll(dx, dy, world)
                # Skip the redraw when already hard against an edge.
                dirty = (view.x, view.y) != before
            elif key.name and key.name.startswith("MOUSE_"):
                # Keystroke reports mouse position as mouse_xy, not .x / .y,
                # and gives (-1, -1) for anything that isn't a mouse event.
                mx, my = key.mouse_xy
                if (mx, my) != (-1, -1):
                    hover = at_cursor(world, view, mx, my)
                    cell = cell_at_cursor(world, view, mx, my)
                    highlight_territory = cell.territory if cell is not None else None
                    dirty = True

    describe(world)


if __name__ == "__main__":
    main()
