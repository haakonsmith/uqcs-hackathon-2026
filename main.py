"""Minimal example: generate a board, look at it, render it.

    python main.py

For the interactive version with live parameter tweaking, run `viewer.py`.
"""

from __future__ import annotations

import blessed

import terrain

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


def render_colour(world: terrain.WorldMap) -> None:
    """One-shot colour render. Two columns per cell keeps the aspect square."""
    term = blessed.Terminal()
    for row in world.grid:
        print("".join(term.on_color_rgb(*cell.terrain.color) + "  " for cell in row) + term.normal)


def main() -> None:
    world = terrain.generate(width=60, height=28, scale=7, octaves=6, seed=SEED, water_fraction=0.68)

    print(terrain.render(world))  # plain ASCII
    print()
    print(terrain.render_territories(world))  # who owns what
    print()
    render_colour(world)
    print()
    describe(world)


if __name__ == "__main__":
    main()
