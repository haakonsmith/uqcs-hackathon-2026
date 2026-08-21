"""Minimal example: generate a board, render it, point at it.

    python main.py

Move the mouse to inspect the territory under the cursor, `o` toggles the
ownership overlay, `q` quits.

For live tweaking of the generation parameters, run `viewer.py`.
"""

from __future__ import annotations

import blessed

import terrain
from viewer import CELL_WIDTH, territory_color

SEED = 42


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


def render(term: blessed.Terminal, world: terrain.WorldMap, overlay: bool) -> str:
    """Colour render, two columns per cell so the map isn't sheared vertically."""
    lines: list[str] = []
    for row in world.grid:
        parts: list[str] = []
        for cell in row:
            if overlay and cell.territory is not None:
                parts.append(term.on_color_rgb(*territory_color(cell.territory)))
            else:
                parts.append(term.on_color_rgb(*cell.terrain.color))
            parts.append(" " * CELL_WIDTH)
        parts.append(term.normal)
        lines.append("".join(parts))
    return "\n".join(lines)


def at_cursor(world: terrain.WorldMap, x: int, y: int) -> str:
    """Describe whatever the mouse is over. Screen columns are 2:1 to map cells."""
    map_x, map_y = x // CELL_WIDTH, y
    if not (0 <= map_x < world.width and 0 <= map_y < world.height):
        return ""

    cell = world.cell(map_x, map_y)
    where = f"({map_x},{map_y}) {cell.terrain.label} height {cell.height:.2f}"
    if cell.territory is None:
        return f"{where} - unclaimed"

    owner = world.territories[cell.territory]
    return f"{where} - territory {owner.id}, continent {owner.continent}, {len(owner.neighbours)} neighbours"


def main() -> None:
    world = terrain.generate(width=60, height=28, scale=7, octaves=6, seed=SEED, water_fraction=0.68)
    term = blessed.Terminal()

    overlay = False
    hover = "move the mouse over the map - [o]verlay [q]uit"
    dirty = True

    with term.fullscreen(), term.cbreak(), term.hidden_cursor(), term.mouse_enabled(report_motion=True):
        while True:
            if dirty:
                print(term.home + render(term, world, overlay), end="")
                print(term.move_xy(0, world.height) + term.ljust(hover[: term.width]), end="", flush=True)
                dirty = False

            key = term.inkey()

            if key.lower() == "q":
                break

            if key == "o":
                overlay = not overlay
                dirty = True
            elif key.name and key.name.startswith("MOUSE_"):
                # Keystroke reports mouse position as mouse_xy, not .x / .y,
                # and gives (-1, -1) for anything that isn't a mouse event.
                mx, my = key.mouse_xy
                if (mx, my) != (-1, -1):
                    hover = at_cursor(world, mx, my)
                    dirty = True

    describe(world)


if __name__ == "__main__":
    main()
