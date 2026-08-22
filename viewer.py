"""Interactive terminal renderer for the generated map.

Run it and tweak generation parameters live:

    python viewer.py --scale 8 --water 0.7

Keys:
    q / Esc   quit
    v         cycle view: terrain / territories / height
    n         new random seed
    r         regenerate with the current seed
    [ / ]     smaller / larger terrain features
    - / +     less / more ocean
    , / .     smaller / larger territories
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass

import blessed

from client import terrain
from client.render import CELL_WIDTH, Color, territory_color

VIEWS = ("terrain", "territories", "height")

_UNCLAIMED = (70, 70, 70)

# Grey levels in the height view.
_HEIGHT_BANDS = 16


@dataclass
class Settings:
    """Generation parameters the viewer edits live."""

    scale: float
    octaves: int
    seed: int
    water: float
    territory_size: int


def _blend(a: Color, b: Color, t: float) -> Color:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def cell_color(cell: terrain.Cell, view: str) -> Color:
    if view == "height":
        # Quantised into bands: reads as a contour map, and the run-length
        # skip in frame() has something to work with.
        shade = round(cell.height * (_HEIGHT_BANDS - 1)) * (255 // (_HEIGHT_BANDS - 1))
        return shade, shade, shade

    if view == "territories":
        if cell.territory is not None:
            # Tint the territory hue with the terrain underneath, so the
            # coastline and mountains stay readable through the ownership layer.
            return _blend(territory_color(cell.territory), cell.terrain.color, 0.35)
        if cell.terrain.is_land:
            return _UNCLAIMED

    return cell.terrain.color


def frame(term: blessed.Terminal, world: terrain.WorldMap, view: str) -> str:
    """Build the whole map as one string; one write per frame avoids flicker."""
    lines: list[str] = []
    for row in world.grid:
        parts: list[str] = []
        previous: Color | None = None
        for cell in row:
            color = cell_color(cell, view)
            # Only emit an escape sequence when the colour actually changes.
            if color != previous:
                parts.append(term.on_color_rgb(*color))
                previous = color
            parts.append(" " * CELL_WIDTH)
        parts.append(term.normal)
        lines.append("".join(parts))
    return "\n".join(lines)


def status(term: blessed.Terminal, world: terrain.WorldMap, view: str, settings: Settings) -> str:
    text = (
        f" {view}  seed {settings.seed}  {world.width}x{world.height}"
        f"  scale {settings.scale:.0f}  ocean {settings.water:.0%}"
        f"  {len(world.continents)} continents  {len(world.territories)} territories"
        "   [v]iew [n]ew [r]egen [ ] scale  -/+ ocean  ,/. size  [q]uit "
    )
    return term.black_on_white(term.ljust(text[: term.width])) + term.normal


def build(term: blessed.Terminal, settings: Settings) -> terrain.WorldMap:
    """Generate a map sized to fill the current terminal."""
    width = max(8, term.width // CELL_WIDTH)
    height = max(8, term.height - 1)  # leave a row for the status bar
    return terrain.generate(
        width=width,
        height=height,
        scale=settings.scale,
        octaves=settings.octaves,
        seed=settings.seed,
        water_fraction=settings.water,
        target_territory_size=settings.territory_size,
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _apply_key(key: str, settings: Settings) -> bool:
    """Fold a parameter key into `settings`. Returns False if the key isn't one."""
    if key == "n":
        settings.seed = random.randrange(1_000_000)
    elif key == "[":
        settings.scale = _clamp(settings.scale - 1, 2, 60)
    elif key == "]":
        settings.scale = _clamp(settings.scale + 1, 2, 60)
    elif key in "-_":
        settings.water = _clamp(settings.water - 0.02, 0.1, 0.95)
    elif key in "+=":
        settings.water = _clamp(settings.water + 0.02, 0.1, 0.95)
    elif key == ",":
        settings.territory_size = int(_clamp(settings.territory_size - 10, 20, 500))
    elif key == ".":
        settings.territory_size = int(_clamp(settings.territory_size + 10, 20, 500))
    elif key != "r":
        return False
    return True


def run(settings: Settings) -> None:
    term = blessed.Terminal()
    view = "terrain"

    with term.cbreak(), term.hidden_cursor(), term.fullscreen():
        world = build(term, settings)
        size = (term.width, term.height)
        dirty = True

        while True:
            if dirty:
                print(
                    term.home + frame(term, world, view) + "\n" + status(term, world, view, settings),
                    end="",
                    flush=True,
                )
                dirty = False

            key = term.inkey(timeout=0.25)

            # Rebuild on resize, otherwise the map no longer fits the window.
            if (term.width, term.height) != size:
                size = (term.width, term.height)
                world = build(term, settings)
                dirty = True
                continue

            if not key:
                continue

            if key.lower() == "q" or key.name == "KEY_ESCAPE":
                return

            if key == "v":
                view = VIEWS[(VIEWS.index(view) + 1) % len(VIEWS)]
                dirty = True
            elif _apply_key(str(key), settings):
                world = build(term, settings)
                dirty = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive viewer for the Risk-style map generator.")
    parser.add_argument("--scale", type=float, default=10.0, help="feature size")
    parser.add_argument("--octaves", type=int, default=6)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--water", type=float, default=0.62, help="share of the map that is ocean")
    parser.add_argument("--territory-size", type=int, default=60, help="target cells per territory")
    args = parser.parse_args()

    run(
        Settings(
            scale=args.scale,
            octaves=args.octaves,
            seed=args.seed if args.seed is not None else random.randrange(1_000_000),
            water=args.water,
            territory_size=args.territory_size,
        )
    )


if __name__ == "__main__":
    main()
