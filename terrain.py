"""Terrain and territory generation for a top-down, Risk-style strategy map.

Perlin noise comes from `vnoise`, which evaluates the whole grid in one
vectorised call rather than per-cell.

The pipeline is three passes:

1. Perlin fBm produces a height field, biased so map edges fall into ocean.
2. Heights are classified into `Terrain` bands that carry gameplay data
   (passability, movement cost) rather than just a render symbol.
3. Land is cut into contiguous `Territory` regions grouped into `Continent`s,
   with an adjacency graph -- the board a Risk-like game actually plays on.

Usage:
    python terrain.py --width 80 --height 40 --seed 42 --view territories
"""

from __future__ import annotations

import argparse
import math
import random
import warnings
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from numpy.typing import NDArray

# vnoise 0.1.0 reads its version through pkg_resources at import time, which
# warns on every run. Nothing downstream depends on it, so keep it quiet.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning, module="vnoise")
    import vnoise

# --------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------


class Terrain(Enum):
    """A terrain type plus the gameplay rules and palette that ride on it."""

    DEEP_WATER = ("deep water", "~", False, 0, (14, 38, 78))
    SHALLOW_WATER = ("shallow water", "-", False, 0, (30, 78, 132))
    SAND = ("sand", ".", True, 1, (206, 188, 128))
    GRASS = ("grass", ",", True, 1, (96, 142, 68))
    FOREST = ("forest", "T", True, 2, (44, 92, 52))
    HILLS = ("hills", "n", True, 2, (124, 112, 82))
    MOUNTAIN = ("mountain", "A", False, 0, (152, 150, 156))

    def __init__(
        self,
        label: str,
        symbol: str,
        passable: bool,
        move_cost: int,
        color: tuple[int, int, int],
    ) -> None:
        self.label = label
        self.symbol = symbol
        self.passable = passable
        self.move_cost = move_cost
        self.color = color

    @property
    def is_water(self) -> bool:
        return self in (Terrain.DEEP_WATER, Terrain.SHALLOW_WATER)

    @property
    def is_land(self) -> bool:
        return not self.is_water


# (upper bound on normalised height, terrain type)
TERRAIN_BANDS = [
    (0.34, Terrain.DEEP_WATER),
    (0.44, Terrain.SHALLOW_WATER),
    (0.48, Terrain.SAND),
    (0.64, Terrain.GRASS),
    (0.77, Terrain.FOREST),
    (0.89, Terrain.HILLS),
    (1.01, Terrain.MOUNTAIN),
]


# Everything at or above this normalised height is land.
SEA_LEVEL = max(limit for limit, terrain in TERRAIN_BANDS if terrain.is_water)


def classify(height: float) -> Terrain:
    for limit, terrain in TERRAIN_BANDS:
        if height < limit:
            return terrain
    return TERRAIN_BANDS[-1][1]


@dataclass(slots=True)
class Cell:
    x: int
    y: int
    height: float  # normalised 0.0 - 1.0
    terrain: Terrain
    territory: int | None = None  # index into WorldMap.territories

    @property
    def symbol(self) -> str:
        return self.terrain.symbol


# --------------------------------------------------------------------------
# Board structure
# --------------------------------------------------------------------------


@dataclass
class Territory:
    """One claimable region: the unit of ownership in a Risk-like game."""

    id: int
    continent: int
    cells: list[tuple[int, int]] = field(default_factory=list)
    land_neighbours: set[int] = field(default_factory=set)
    sea_neighbours: set[int] = field(default_factory=set)

    @property
    def size(self) -> int:
        return len(self.cells)

    @property
    def neighbours(self) -> set[int]:
        return self.land_neighbours | self.sea_neighbours

    @property
    def centroid(self) -> tuple[float, float]:
        return (
            sum(x for x, _ in self.cells) / self.size,
            sum(y for _, y in self.cells) / self.size,
        )


@dataclass
class Continent:
    """A landmass. Holding all of it pays a reinforcement bonus, as in Risk."""

    id: int
    territories: list[int] = field(default_factory=list)

    @property
    def bonus(self) -> int:
        return max(1, round(len(self.territories) / 3))


@dataclass
class WorldMap:
    width: int
    height: int
    grid: list[list[Cell]]
    territories: list[Territory]
    continents: list[Continent]

    def cell(self, x: int, y: int) -> Cell:
        return self.grid[y][x]

    def cells(self) -> Iterator[Cell]:
        for row in self.grid:
            yield from row


_ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

# How far in from the border the ocean ramp reaches, as a share of each axis.
_BORDER_MARGIN = 0.16


def _edge_falloff(width: int, height: int) -> NDArray[np.float64]:
    """0 across the interior, ramping to 1 at the border.

    Subtracting this from the height field rings the map in ocean, so
    landmasses read as continents instead of running off the edge. It stays
    flat away from the border on purpose: a smooth centre-to-edge gradient
    would pile all the land into one central blob, which is the opposite of
    the several-continents board a Risk-like game wants.
    """
    xs = np.arange(width)
    ys = np.arange(height)
    dx = np.minimum(xs, width - 1 - xs) / max(width - 1, 1)
    dy = np.minimum(ys, height - 1 - ys) / max(height - 1, 1)
    depth = np.minimum(dx[None, :], dy[:, None]) / _BORDER_MARGIN
    return np.where(depth >= 1.0, 0.0, (1.0 - depth) ** 2)


def _fbm(seed: int | None, width: int, height: int, scale: float, octaves: int) -> NDArray[np.float64]:
    """Sample vnoise over the whole grid at once, returned as [y][x]."""
    noise = vnoise.Noise(seed)
    xs = np.arange(width) / scale
    ys = np.arange(height) / scale
    # grid_mode indexes [x][y]; the rest of this module works in rows of y.
    # vnoise is untyped, so pin the dtype on the way out.
    return np.asarray(noise.noise2(xs, ys, octaves=octaves, grid_mode=True), dtype=np.float64).T


def height_field(
    width: int,
    height: int,
    scale: float,
    octaves: int,
    seed: int | None,
    edge_bias: float,
    water_fraction: float,
    continent_scale: float,
    mask_weight: float,
) -> NDArray[np.float64]:
    """Two layers of Perlin fBm, shaped by the edge falloff, remapped to 0..1.

    A separate low-frequency mask decides *where* land is, while the detail
    layer decides what that land looks like. Splitting the two is what breaks
    the map into distinct continents: one noise layer at a single scale gives
    either one connected mass or scattered speckle, never a handful of
    coherent landmasses.

    The remap is by quantile rather than by min/max: the value at the
    `water_fraction` percentile becomes exactly sea level, and each side is
    rescaled around it. Straight min/max normalising leaves the land area at
    the mercy of the seed, which in practice always produced one blob covering
    half the map. Pinning the coastline instead makes `water_fraction` the dial
    that decides archipelago vs. supercontinent.
    """
    detail = _fbm(seed, width, height, scale, octaves)
    # Offsetting the seed keeps the mask uncorrelated with the detail layer.
    mask = _fbm(None if seed is None else seed + 1013, width, height, continent_scale, 2)

    raw = mask_weight * mask + (1.0 - mask_weight) * detail - edge_bias * _edge_falloff(width, height)

    lowest, highest = float(raw.min()), float(raw.max())
    sea = float(np.quantile(raw, water_fraction))
    below = (sea - lowest) or 1.0
    above = (highest - sea) or 1.0

    return np.where(
        raw < sea,
        SEA_LEVEL * (raw - lowest) / below,
        SEA_LEVEL + (1.0 - SEA_LEVEL) * (raw - sea) / above,
    )


def _find_continents(grid: list[list[Cell]]) -> list[list[tuple[int, int]]]:
    """Flood fill land into connected components, largest first."""
    height, width = len(grid), len(grid[0])
    seen = [[False] * width for _ in range(height)]
    components: list[list[tuple[int, int]]] = []

    for sy in range(height):
        for sx in range(width):
            if seen[sy][sx] or grid[sy][sx].terrain.is_water:
                continue
            component = []
            queue = deque([(sx, sy)])
            seen[sy][sx] = True
            while queue:
                x, y = queue.popleft()
                component.append((x, y))
                for dx, dy in _ORTHOGONAL:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height and not seen[ny][nx] and grid[ny][nx].terrain.is_land:
                        seen[ny][nx] = True
                        queue.append((nx, ny))
            components.append(component)

    components.sort(key=len, reverse=True)
    return components


def _pick_seeds(cells: list[tuple[int, int]], count: int, rng: random.Random) -> list[tuple[int, int]]:
    """Farthest-point sampling, so territory seeds spread out evenly."""
    points = np.asarray(cells)
    chosen = [rng.randrange(len(cells))]
    # Running squared distance from each cell to its nearest chosen seed.
    best = ((points - points[chosen[0]]) ** 2).sum(axis=1)

    while len(chosen) < count:
        index = int(best.argmax())
        chosen.append(index)
        np.minimum(best, ((points - points[index]) ** 2).sum(axis=1), out=best)

    return [(int(points[i][0]), int(points[i][1])) for i in chosen]


def _grow_territories(
    grid: list[list[Cell]],
    component: list[tuple[int, int]],
    seeds: list[tuple[int, int]],
    first_id: int,
) -> list[Territory]:
    """Multi-source BFS from the seeds: equal-ish, guaranteed-contiguous regions."""
    height, width = len(grid), len(grid[0])
    in_component = set(component)
    territories = [Territory(id=first_id + i, continent=-1) for i in range(len(seeds))]

    queue: deque[tuple[int, int, int]] = deque()
    for i, (x, y) in enumerate(seeds):
        grid[y][x].territory = first_id + i
        territories[i].cells.append((x, y))
        queue.append((x, y, i))

    while queue:
        x, y, i = queue.popleft()
        for dx, dy in _ORTHOGONAL:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) not in in_component or grid[ny][nx].territory is not None:
                continue
            grid[ny][nx].territory = first_id + i
            territories[i].cells.append((nx, ny))
            queue.append((nx, ny, i))

    return territories


def _link_land_neighbours(grid: list[list[Cell]], territories: list[Territory]) -> None:
    """Two territories are adjacent when any of their cells share an edge."""
    height, width = len(grid), len(grid[0])
    for y in range(height):
        for x in range(width):
            here = grid[y][x].territory
            if here is None:
                continue
            for dx, dy in ((1, 0), (0, 1)):  # right/down only; each pair seen once
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                there = grid[ny][nx].territory
                if there is not None and there != here:
                    territories[here].land_neighbours.add(there)
                    territories[there].land_neighbours.add(here)


def _link_sea_routes(
    territories: list[Territory],
    continents: list[Continent],
    max_distance: float,
) -> None:
    """Bridge continents with sea lanes.

    Adds every coastal pair within `max_distance`, then keeps adding the next
    shortest link until the whole board is one connected graph -- otherwise a
    player can be stranded on an unreachable continent.
    """
    if len(continents) < 2:
        return

    centroids = {t.id: t.centroid for t in territories}
    candidates = []
    for a in range(len(continents)):
        for b in range(a + 1, len(continents)):
            for ta in continents[a].territories:
                for tb in continents[b].territories:
                    ax, ay = centroids[ta]
                    bx, by = centroids[tb]
                    candidates.append((math.hypot(ax - bx, ay - by), ta, tb))
    candidates.sort()

    parent = list(range(len(territories)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for t in territories:
        for n in t.land_neighbours:
            parent[find(t.id)] = find(n)

    for distance, ta, tb in candidates:
        joins = find(ta) != find(tb)
        if not joins and distance > max_distance:
            continue
        territories[ta].sea_neighbours.add(tb)
        territories[tb].sea_neighbours.add(ta)
        if joins:
            parent[find(ta)] = find(tb)


def generate(
    width: int,
    height: int,
    scale: float = 24.0,
    octaves: int = 5,
    seed: int | None = None,
    edge_bias: float = 0.9,
    water_fraction: float = 0.62,
    continent_scale: float | None = None,
    mask_weight: float = 0.65,
    target_territory_size: int = 60,
    min_continent_size: int = 40,
    sea_route_distance: float = 12.0,
) -> WorldMap:
    """Build a playable board.

    `scale` controls feature size: bigger means larger, smoother continents;
    smaller means noisier, more fragmented terrain. `water_fraction` is the
    share of the map that ends up ocean, and is the main lever on how many
    separate continents you get. `continent_scale` (default `scale * 3`) sets
    how big those continents are, and `mask_weight` how strongly they dominate
    the finer terrain detail. `target_territory_size` is the rough cell count
    per territory, so it sets how many territories a continent is cut into.
    Landmasses under `min_continent_size` cells stay as scenery and are never
    claimable.
    """
    rng = random.Random(seed)
    field_ = height_field(
        width,
        height,
        scale,
        octaves,
        seed,
        edge_bias,
        water_fraction,
        continent_scale if continent_scale is not None else scale * 3.0,
        mask_weight,
    )
    grid = [[Cell(x, y, float(h), classify(float(h))) for x, h in enumerate(row)] for y, row in enumerate(field_)]

    territories: list[Territory] = []
    continents: list[Continent] = []

    for component in _find_continents(grid):
        if len(component) < min_continent_size:
            continue
        count = max(1, round(len(component) / target_territory_size))
        seeds = _pick_seeds(component, count, rng)
        grown = _grow_territories(grid, component, seeds, first_id=len(territories))

        continent = Continent(id=len(continents), territories=[t.id for t in grown])
        for t in grown:
            t.continent = continent.id
        continents.append(continent)
        territories.extend(grown)

    _link_land_neighbours(grid, territories)
    _link_sea_routes(territories, continents, sea_route_distance)

    return WorldMap(width, height, grid, territories, continents)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

# Cycled so adjacent territories rarely collide; unclaimed land shows as '#'.
_TERRITORY_GLYPHS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def render(world: WorldMap) -> str:
    """Terrain view."""
    return "\n".join("".join(cell.terrain.symbol for cell in row) for row in world.grid)


def render_territories(world: WorldMap) -> str:
    """Territory view: water blank, unclaimed land '#', else a per-territory glyph."""
    lines = []
    for row in world.grid:
        chars = []
        for cell in row:
            if cell.territory is not None:
                chars.append(_TERRITORY_GLYPHS[cell.territory % len(_TERRITORY_GLYPHS)])
            elif cell.terrain.is_land:
                chars.append("#")
            else:
                chars.append(" ")
        lines.append("".join(chars))
    return "\n".join(lines)


def summary(world: WorldMap) -> str:
    plural = "" if len(world.continents) == 1 else "s"
    lines = [f"{len(world.continents)} continent{plural}, {len(world.territories)} territories"]
    for continent in world.continents:
        count = len(continent.territories)
        noun = "territory" if count == 1 else "territories"
        lines.append(f"  continent {continent.id}: {count} {noun}, bonus +{continent.bonus}")
    return "\n".join(lines)


# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Risk-style map from Perlin noise.")
    parser.add_argument("--width", type=int, default=80)
    parser.add_argument("--height", type=int, default=40)
    parser.add_argument("--scale", type=float, default=24.0, help="feature size")
    parser.add_argument("--octaves", type=int, default=5)
    parser.add_argument("--water", type=float, default=0.62, help="share of the map that is ocean")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--territory-size", type=int, default=60, help="target cells per territory")
    parser.add_argument("--view", choices=("terrain", "territories", "both"), default="both")
    args = parser.parse_args()

    world = generate(
        width=args.width,
        height=args.height,
        scale=args.scale,
        octaves=args.octaves,
        seed=args.seed,
        water_fraction=args.water,
        target_territory_size=args.territory_size,
    )

    if args.view in ("terrain", "both"):
        print(render(world))
    if args.view == "both":
        print()
    if args.view in ("territories", "both"):
        print(render_territories(world))
    print()
    print(summary(world))


if __name__ == "__main__":
    main()
