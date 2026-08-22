"""Server-owned world: terrain generation plus websocket-serialisable state.

Perlin noise is generated here, vectorised over numpy so the whole grid is
filled in one pass per octave rather than cell by cell.

The pipeline is four passes:

1. Domain-warped Perlin fBm produces a height field, biased so map edges fall
   into ocean. A second, independent field gives moisture.
2. Height and moisture are classified into `Terrain` bands that carry gameplay
   data (passability, movement cost) rather than just a render symbol.
3. Each landmass is cut into contiguous, near-equal-area `Territory` regions --
   the board a Risk-like game actually plays on. There are no continents and no
   continent bonuses: a territory is the only unit of ownership.
4. Territories are dealt out so every player starts with the same number of
   them, the same total land area (as near as the partition allows) and the
   same number of soldiers. Owners are `Player.id` UUIDs, and the roster is
   read once here rather than stored on the map.

`create_world()` returns a `World` dataclass that serialises cleanly to JSON
for clients over websockets.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import deque
from collections.abc import Sequence
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray

from protocol.terrain import Cell, Terrain, Territory, World, WorldMap

# Used when a board is generated without a lobby to take player ids from.
PLAYER_COUNT = 4

# Territories each player owns at the start. Total board size is the product
# of the two, which keeps the split exactly even.
TERRITORIES_PER_PLAYER = 8

# Soldiers per player at the start, expressed per owned territory.
SOLDIERS_PER_TERRITORY = 3

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

# Moisture above this turns lowland into forest, below it into grass.
_FOREST_MOISTURE = 0.5


def classify(height: float) -> Terrain:
    for limit, terrain in TERRAIN_BANDS:
        if height < limit:
            return terrain
    return TERRAIN_BANDS[-1][1]


def classify_cell(height: float, moisture: float) -> Terrain:
    """Height picks the band; moisture decides grass vs forest within it.

    Driving tree cover off elevation alone produces obvious contour rings.
    Letting a separate noise field choose instead gives forests that sprawl
    across the lowlands the way real ones do.
    """
    terrain = classify(height)
    if terrain in (Terrain.GRASS, Terrain.FOREST):
        return Terrain.FOREST if moisture >= _FOREST_MOISTURE else Terrain.GRASS
    return terrain


_ORTHOGONAL = ((1, 0), (-1, 0), (0, 1), (0, -1))


# --------------------------------------------------------------------------
# Noise
# --------------------------------------------------------------------------

# How far in from the border the ocean ramp reaches, as a share of each axis.
_BORDER_MARGIN = 0.16


def _edge_falloff(width: int, height: int) -> NDArray[np.float64]:
    """0 across the interior, ramping to 1 at the border.

    Subtracting this from the height field rings the map in ocean, so
    landmasses do not run off the edge. It stays flat away from the border
    on purpose: a smooth centre-to-edge gradient would pile all the land
    into one central blob.
    """
    xs = np.arange(width)
    ys = np.arange(height)
    dx = np.minimum(xs, width - 1 - xs) / max(width - 1, 1)
    dy = np.minimum(ys, height - 1 - ys) / max(height - 1, 1)
    depth = np.minimum(dx[None, :], dy[:, None]) / _BORDER_MARGIN
    return np.where(depth >= 1.0, 0.0, (1.0 - depth) ** 2)


# Eight unit-length gradient vectors, one per corner direction.
_GRADIENTS = np.array(
    [
        (1.0, 0.0),
        (-1.0, 0.0),
        (0.0, 1.0),
        (0.0, -1.0),
        (0.7071067811865476, 0.7071067811865476),
        (-0.7071067811865476, 0.7071067811865476),
        (0.7071067811865476, -0.7071067811865476),
        (-0.7071067811865476, -0.7071067811865476),
    ],
    dtype=np.float64,
)


def _fade(t: NDArray[np.float64]) -> NDArray[np.float64]:
    """Ken Perlin's smoothstep: 6t^5 - 15t^4 + 10t^3."""
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a: NDArray[np.float64], b: NDArray[np.float64], t: NDArray[np.float64]) -> NDArray[np.float64]:
    return a + t * (b - a)


def _permutation(seed: int | None) -> NDArray[np.int64]:
    table = np.random.default_rng(seed).permutation(256)
    # Doubled so lookups of index+1 never run off the end.
    return np.concatenate([table, table]).astype(np.int64)


def _perlin(
    perm: NDArray[np.int64],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Classic 2D Perlin noise sampled at the coordinates (x, y).

    `x` and `y` are arbitrary same-shaped (or broadcastable) coordinate
    arrays rather than axis ranges, which is what lets the height field warp
    its own input coordinates. Every step is whole-array, so one call fills
    the entire map.
    """
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    xf, yf = x - x0, y - y0
    xi, yi = x0 & 255, y0 & 255

    # Hash the four cell corners. Values stay under 512, which is why the
    # permutation table is doubled.
    aa = perm[perm[xi] + yi]
    ab = perm[perm[xi] + yi + 1]
    ba = perm[perm[xi + 1] + yi]
    bb = perm[perm[xi + 1] + yi + 1]

    def dot_grad(hashed: NDArray[np.int64], dx: NDArray[np.float64], dy: NDArray[np.float64]) -> NDArray[np.float64]:
        gradient = _GRADIENTS[hashed & 7]
        gx: NDArray[np.float64] = gradient[..., 0]
        gy: NDArray[np.float64] = gradient[..., 1]
        return gx * dx + gy * dy

    u, v = _fade(xf), _fade(yf)
    top = _lerp(dot_grad(aa, xf, yf), dot_grad(ba, xf - 1, yf), u)
    bottom = _lerp(dot_grad(ab, xf, yf - 1), dot_grad(bb, xf - 1, yf - 1), u)
    # sqrt(2) scales the theoretical +/-0.707 range up to about +/-1.
    scaled: NDArray[np.float64] = _lerp(top, bottom, v) * math.sqrt(2)
    return scaled


def _fbm(
    perm: NDArray[np.int64],
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    octaves: int,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
) -> NDArray[np.float64]:
    """Fractional Brownian motion: stacked octaves of Perlin noise.

    Each octave doubles the frequency (lacunarity) and halves the amplitude
    (persistence), which adds fine detail on top of the broad shapes. All
    octaves share one permutation table, so the layers stay coherent.
    """
    total = np.zeros(np.broadcast_shapes(x.shape, y.shape), dtype=np.float64)
    amplitude = 1.0
    frequency = 1.0
    max_amplitude = 0.0

    for _ in range(octaves):
        total += _perlin(perm, x * frequency, y * frequency) * amplitude
        max_amplitude += amplitude
        amplitude *= persistence
        frequency *= lacunarity

    return total / max_amplitude


def _coordinates(width: int, height: int, scale: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Sample coordinates for a `height` x `width` grid, as a row and a column.

    Left un-expanded at (1, W) and (H, 1): `_perlin` and `_fbm` broadcast
    their inputs, so numpy pairs them up on the way through and every result
    still comes out (H, W). `np.broadcast_arrays` would do the same pairing
    eagerly, and is typed `tuple[NDArray[Any], ...]` - a variadic tuple of
    unknown dtype, which no annotation here can narrow.
    """
    xs: NDArray[np.float64] = np.arange(width, dtype=np.float64) / scale
    ys: NDArray[np.float64] = np.arange(height, dtype=np.float64) / scale
    return xs[None, :], ys[:, None]


# Arbitrary offsets that decorrelate fields sharing one permutation table.
_WARP_OFFSET_X = (5.2, 1.3)
_WARP_OFFSET_Y = (8.7, 2.9)
_MOISTURE_OFFSET = (37.1, 91.7)


def height_field(
    width: int,
    height: int,
    scale: float,
    octaves: int,
    seed: int | None,
    edge_bias: float,
    water_fraction: float,
    warp_strength: float = 0.55,
) -> NDArray[np.float64]:
    """Perlin fBm shaped by the edge falloff, remapped to 0..1.

    Before sampling, the coordinates are displaced by a second low-frequency
    noise field ("domain warping"). Plain fBm produces recognisably round,
    bubbly islands; warping the input drags those outlines sideways into the
    peninsulas, inlets and bays that make a coastline read as real.

    The remap is by quantile rather than by min/max: the value at the
    `water_fraction` percentile becomes exactly sea level, and each side is
    rescaled around it. Straight min/max normalising leaves the land area at
    the mercy of the seed. Pinning the coastline instead makes
    `water_fraction` the dial for ocean vs land.
    """
    perm = _permutation(seed)
    x, y = _coordinates(width, height, scale)

    if warp_strength:
        warp_x = _fbm(perm, x + _WARP_OFFSET_X[0], y + _WARP_OFFSET_X[1], octaves=2)
        warp_y = _fbm(perm, x + _WARP_OFFSET_Y[0], y + _WARP_OFFSET_Y[1], octaves=2)
        x = x + warp_strength * warp_x
        y = y + warp_strength * warp_y

    raw = _fbm(perm, x, y, octaves) - edge_bias * _edge_falloff(width, height)

    lowest, highest = float(raw.min()), float(raw.max())
    sea = float(np.quantile(raw, water_fraction))
    below = (sea - lowest) or 1.0
    above = (highest - sea) or 1.0

    return np.where(
        raw < sea,
        SEA_LEVEL * (raw - lowest) / below,
        SEA_LEVEL + (1.0 - SEA_LEVEL) * (raw - sea) / above,
    )


def moisture_field(width: int, height: int, scale: float, seed: int | None) -> NDArray[np.float64]:
    """A second, coarser noise field in 0..1 deciding where forests grow."""
    perm = _permutation(None if seed is None else seed + 1)
    x, y = _coordinates(width, height, scale)
    raw = _fbm(perm, x + _MOISTURE_OFFSET[0], y + _MOISTURE_OFFSET[1], octaves=3)
    lowest, highest = float(raw.min()), float(raw.max())
    return (raw - lowest) / ((highest - lowest) or 1.0)


# --------------------------------------------------------------------------
# Territories
# --------------------------------------------------------------------------


def _landmasses(grid: list[list[Cell]]) -> list[list[tuple[int, int]]]:
    """Flood fill every connected run of land, largest first."""
    height, width = len(grid), len(grid[0])
    seen = [[False] * width for _ in range(height)]
    found: list[list[tuple[int, int]]] = []

    for sy in range(height):
        for sx in range(width):
            if seen[sy][sx] or grid[sy][sx].terrain.is_water:
                continue
            component: list[tuple[int, int]] = []
            seen[sy][sx] = True
            queue = deque([(sx, sy)])
            while queue:
                x, y = queue.popleft()
                component.append((x, y))
                for dx, dy in _ORTHOGONAL:
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    if seen[ny][nx] or grid[ny][nx].terrain.is_water:
                        continue
                    seen[ny][nx] = True
                    queue.append((nx, ny))
            found.append(component)

    found.sort(key=len, reverse=True)
    return found


def _share_out(sizes: list[int], total: int) -> list[int]:
    """Split `total` territories across landmasses of the given sizes.

    Territories go out one at a time to whichever landmass would still have
    the most land per territory -- the divisor method used for apportioning
    parliamentary seats. It drives every landmass towards the same area per
    territory, so no continent ends up with regions twice the size of another's.

    A landmass can finish with zero, and that is deliberate: a small island
    only earns a territory once it holds as much land as everyone else's
    regions do, which keeps specks out of the board instead of turning them
    into runt territories that unbalance the split.
    """
    counts = [0] * len(sizes)
    for _ in range(total):
        best, best_share = -1, 0.0
        for i, size in enumerate(sizes):
            if counts[i] >= size:  # cannot have more territories than cells
                continue
            share = size / (counts[i] + 1)
            if share > best_share:
                best, best_share = i, share
        if best < 0:
            break
        counts[best] += 1
    return counts


def _pick_seeds(cells: list[tuple[int, int]], count: int, rng: random.Random) -> list[tuple[int, int]]:
    """Farthest-point sampling, so territory seeds spread out evenly."""
    points = np.asarray(cells)
    chosen = [rng.randrange(len(cells))]
    # Running squared distance from each cell to its nearest chosen seed.
    best = ((points - points[chosen[0]]) ** 2).sum(axis=1)

    while len(chosen) < count:
        index = int(best.argmax())
        if best[index] == 0:  # every cell already sits on a seed
            break
        chosen.append(index)
        np.minimum(best, ((points - points[index]) ** 2).sum(axis=1), out=best)

    return [(int(points[i][0]), int(points[i][1])) for i in chosen]


def _grow_regions(
    component: list[tuple[int, int]],
    seeds: list[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    """Capacity-capped Voronoi growth: contiguous regions of near-equal area.

    Every region grows outwards from its seed in order of straight-line
    distance, all of them at once from one shared heap, so at any moment each
    is roughly a disc and they meet along the midlines between seeds. Growth
    stops at a shared capacity, which keeps the areas within a cell or two of
    each other.

    Ordering by distance rather than by discovery is what makes the regions
    round. A FIFO frontier expands by graph distance in whatever order cells
    happened to be queued, so a region reaches around an obstacle and comes
    back on itself; the heap always takes the nearest unclaimed cell instead.

    Cells only ever enter the heap as neighbours of a claimed cell, so a
    region cannot jump a gap. Anything left over - walled off while its only
    neighbour was full - is swept up afterwards.
    """
    members = set(component)
    owner: dict[tuple[int, int], int] = {}
    regions: list[list[tuple[int, int]]] = [[] for _ in seeds]
    capacity = -(-len(component) // len(seeds))  # ceil

    # (distance from seed, region, cell). The region index breaks distance
    # ties, so the result does not depend on heap insertion order.
    frontier: list[tuple[float, int, tuple[int, int]]] = [(0.0, i, seed) for i, seed in enumerate(seeds)]
    heapq.heapify(frontier)

    while frontier:
        _, i, cell = heapq.heappop(frontier)
        if cell in owner or len(regions[i]) >= capacity:
            continue

        owner[cell] = i
        regions[i].append(cell)
        seed_x, seed_y = seeds[i]
        for neighbour in _free_neighbours(cell, members, owner):
            distance = math.hypot(neighbour[0] - seed_x, neighbour[1] - seed_y)
            heapq.heappush(frontier, (distance, i, neighbour))

    _sweep_leftovers(component, members, owner, regions)
    return regions


def _free_neighbours(
    cell: tuple[int, int],
    members: set[tuple[int, int]],
    owner: dict[tuple[int, int], int],
) -> list[tuple[int, int]]:
    x, y = cell
    return [n for n in ((x + dx, y + dy) for dx, dy in _ORTHOGONAL) if n in members and n not in owner]


def _sweep_leftovers(
    component: list[tuple[int, int]],
    members: set[tuple[int, int]],
    owner: dict[tuple[int, int], int],
    regions: list[list[tuple[int, int]]],
) -> None:
    """Hand any unclaimed cell to its smallest adjacent region, ignoring caps.

    Only ever assigns a cell touching an existing region, so regions stay
    contiguous. The component is connected, so this always terminates.
    """
    centres = [_centroid(region) if region else (0.0, 0.0) for region in regions]

    # Same distance-ordered spread as the growth pass, measured from each
    # region's centre rather than its seed, and with no capacity to stop it.
    # The region index breaks ties so the result does not depend on the order
    # cells were pushed.
    frontier: list[tuple[float, int, tuple[int, int]]] = []
    for cell in component:
        if cell in owner:
            continue
        x, y = cell
        for dx, dy in _ORTHOGONAL:
            i = owner.get((x + dx, y + dy))
            if i is not None:
                heapq.heappush(frontier, (_distance_sq(cell, centres[i]), i, cell))

    while frontier:
        _, i, cell = heapq.heappop(frontier)
        if cell in owner:
            continue
        owner[cell] = i
        regions[i].append(cell)
        for neighbour in _free_neighbours(cell, members, owner):
            heapq.heappush(frontier, (_distance_sq(neighbour, centres[i]), i, neighbour))


def _rebalance(regions: list[list[tuple[int, int]]], passes: int = 6) -> None:
    """Redraw the borders until no neighbour is bigger than another, in place.

    Growth alone cannot always come out even: a region that gets walled in
    early leaves a pocket only one neighbour can reach, and that neighbour
    swallows the lot. So afterwards, any region at least two cells larger than
    a neighbour hands a border cell over, provided losing it does not split
    the region in two. Every move shrinks the gap between the pair, so this
    settles rather than oscillating.
    """
    if len(regions) < 2:
        return

    owner = {cell: i for i, region in enumerate(regions) for cell in region}

    for _ in range(passes):
        moved = False
        # Biggest first: they have the most to give, and each hand-off makes
        # the next comparison fairer.
        for i in sorted(range(len(regions)), key=lambda i: -len(regions[i])):
            while (transfer := _find_transfer(regions, owner, i)) is not None:
                cell, j = transfer
                regions[i].remove(cell)
                regions[j].append(cell)
                owner[cell] = j
                moved = True
        if not moved:
            break


def _find_transfer(
    regions: list[list[tuple[int, int]]],
    owner: dict[tuple[int, int], int],
    i: int,
) -> tuple[tuple[int, int], int] | None:
    """The border cell of `i` a smaller neighbour can take with the best shape.

    Every safe hand-off evens the areas out equally well, so the choice is
    free to be made on geometry: prefer the cell furthest from its own
    region's centre and nearest the receiver's, which shaves off the tip of a
    spur rather than biting a notch out of a side. Taking the first candidate
    found instead - the obvious thing - undoes most of the roundness the
    growth pass worked for, because region order is arbitrary.
    """
    if len(regions[i]) < 2:
        return None

    centre = _centroid(regions[i])
    centres: dict[int, tuple[float, float]] = {}
    candidates: list[tuple[float, tuple[int, int], int]] = []

    for cell in regions[i]:
        x, y = cell
        for dx, dy in _ORTHOGONAL:
            j = owner.get((x + dx, y + dy))
            # Only downhill, and only by two or more: swapping a single cell
            # back and forth between equals would never settle.
            if j is None or j == i or len(regions[j]) >= len(regions[i]) - 1:
                continue
            if j not in centres:
                centres[j] = _centroid(regions[j])
            gain = _distance_sq(cell, centre) - _distance_sq(cell, centres[j])
            candidates.append((gain, cell, j))

    # Connectivity is the expensive test, so rank first and pay for it only
    # until one candidate passes.
    candidates.sort(key=lambda candidate: -candidate[0])
    for _, cell, j in candidates:
        if _stays_connected(regions[i], cell):
            return cell, j
    return None


def _centroid(region: list[tuple[int, int]]) -> tuple[float, float]:
    return (
        sum(x for x, _ in region) / len(region),
        sum(y for _, y in region) / len(region),
    )


def _distance_sq(cell: tuple[int, int], point: tuple[float, float]) -> float:
    return (cell[0] - point[0]) ** 2 + (cell[1] - point[1]) ** 2


def _stays_connected(region: list[tuple[int, int]], without: tuple[int, int]) -> bool:
    remaining = set(region)
    remaining.discard(without)
    if not remaining:
        return False
    start = next(iter(remaining))
    stack, seen = [start], {start}
    while stack:
        x, y = stack.pop()
        for dx, dy in _ORTHOGONAL:
            n = (x + dx, y + dy)
            if n in remaining and n not in seen:
                seen.add(n)
                stack.append(n)
    return len(seen) == len(remaining)


def _partition(
    component: list[tuple[int, int]],
    count: int,
    rng: random.Random,
    relaxations: int,
) -> list[list[tuple[int, int]]]:
    """Cut one landmass into `count` compact, near-equal-area regions.

    Lloyd relaxation: grow regions from the seeds, move each seed to the cell
    nearest its region's centroid, repeat. A couple of passes is enough to
    turn the ragged first attempt into regions that look deliberately drawn;
    beyond that it stops paying, because the shapes are held back by the
    coastline rather than by the seed positions.

    The final rebalance is a deliberate trade: it costs a little roundness to
    pull the areas from a 13-to-67 cell spread down to about 27-to-44. A
    five-fold difference in size is a worse board than a slightly lumpy one.
    """
    count = max(1, min(count, len(component)))
    seeds = _pick_seeds(component, count, rng)

    regions = _grow_regions(component, seeds)
    for _ in range(relaxations):
        seeds = [_nearest_to_centroid(region) for region in regions]
        if len(set(seeds)) < len(seeds):  # relaxation collapsed two regions
            break
        regions = _grow_regions(component, seeds)

    _rebalance(regions)
    return regions


def _nearest_to_centroid(region: list[tuple[int, int]]) -> tuple[int, int]:
    # The centroid of a concave region can sit outside it, so snap to a member.
    centre = _centroid(region)
    return min(region, key=lambda cell: _distance_sq(cell, centre))


def _build_territories(
    grid: list[list[Cell]],
    total: int,
    min_landmass_size: int,
    rng: random.Random,
    relaxations: int,
) -> list[Territory]:
    """Carve every worthwhile landmass into territories.

    Islands smaller than `min_landmass_size` stay scenery, as do any the
    apportionment passes over: claimable specks would wreck the even split,
    and a one-cell territory is not a fight worth having.
    """
    landmasses = [c for c in _landmasses(grid) if len(c) >= min_landmass_size]
    if not landmasses:
        return []

    # Sorted largest first, so the trim drops only the least interesting
    # islands -- no landmass can win more than one territory per cell anyway.
    landmasses = landmasses[:total]
    counts = _share_out([len(c) for c in landmasses], total)

    territories: list[Territory] = []
    for component, count in zip(landmasses, counts):
        if count == 0:
            continue
        for region in _partition(component, count, rng, relaxations):
            territory = Territory(id=len(territories), cells=region)
            for x, y in region:
                grid[y][x].territory = territory.id
            territories.append(territory)

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


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self._parent[ra] = rb
        return True


def _link_sea_routes(territories: list[Territory], max_distance: float) -> None:
    """Bridge landmasses with sea lanes.

    Adds every cross-landmass pair within `max_distance`, then keeps adding
    the next shortest link until the whole board is one connected graph --
    otherwise a player can be stranded on an unreachable island.
    """
    if len(territories) < 2:
        return

    groups = _DisjointSet(len(territories))
    for t in territories:
        for n in t.land_neighbours:
            groups.union(t.id, n)

    centroids = {t.id: t.centroid for t in territories}
    candidates = []
    for i, a in enumerate(territories):
        for b in territories[i + 1 :]:
            if groups.find(a.id) == groups.find(b.id):
                continue  # same landmass: already walkable
            ax, ay = centroids[a.id]
            bx, by = centroids[b.id]
            candidates.append((math.hypot(ax - bx, ay - by), a.id, b.id))
    candidates.sort()

    for distance, a, b in candidates:
        joins = groups.find(a) != groups.find(b)
        if not joins and distance > max_distance:
            continue
        territories[a].sea_neighbours.add(b)
        territories[b].sea_neighbours.add(a)
        groups.union(a, b)


def _divide(
    territories: list[Territory],
    players: list[UUID],
    soldiers_per_territory: int,
    rng: random.Random,
) -> None:
    """Deal the board out evenly: same territory count, area and army size.

    Territories go out largest first to whichever player currently holds the
    least land. Dealing at random gives equal counts but lets one player draw
    all the big regions; this evens out area too. Quotas are hard, so counts
    never differ by more than one even when the board does not divide cleanly.
    """
    if not territories or not players:
        return

    quotas = [len(territories) // len(players)] * len(players)
    for slot in rng.sample(range(len(players)), len(territories) % len(players)):
        quotas[slot] += 1

    areas = [0] * len(players)
    counts = [0] * len(players)
    # Shuffle first so equal-sized territories do not always favour one player.
    order = sorted(rng.sample(territories, len(territories)), key=lambda t: -t.size)

    for territory in order:
        slot = min(
            (s for s in range(len(players)) if counts[s] < quotas[s]),
            key=lambda s: areas[s],
        )
        territory.owner = players[slot]
        counts[slot] += 1
        areas[slot] += territory.size

    _place_soldiers(territories, players, quotas, soldiers_per_territory, rng)


def _place_soldiers(
    territories: list[Territory],
    players: list[UUID],
    quotas: list[int],
    soldiers_per_territory: int,
    rng: random.Random,
) -> None:
    """Every player fields the same army: one soldier per territory, rest random."""
    budget = max(quotas) * soldiers_per_territory
    for player in players:
        owned = [t for t in territories if t.owner == player]
        if not owned:
            continue
        for territory in owned:
            territory.soldiers = 1
        for _ in range(budget - len(owned)):
            rng.choice(owned).soldiers += 1


def _player_ids(players: Sequence[UUID] | None, player_count: int) -> list[UUID]:
    """Use the lobby's ids when there is one, otherwise mint placeholders."""
    if players is not None:
        return list(dict.fromkeys(players))
    return [uuid4() for _ in range(player_count)]


def generate(
    width: int,
    height: int,
    players: Sequence[UUID] | None = None,
    player_count: int = PLAYER_COUNT,
    territories_per_player: int = TERRITORIES_PER_PLAYER,
    soldiers_per_territory: int = SOLDIERS_PER_TERRITORY,
    scale: float = 24.0,
    octaves: int = 5,
    seed: int | None = None,
    edge_bias: float = 0.9,
    water_fraction: float = 0.62,
    warp_strength: float = 0.55,
    moisture_scale: float = 30.0,
    min_landmass_size: int = 40,
    sea_route_distance: float = 14.0,
    relaxations: int = 2,
) -> WorldMap:
    """Build a playable board.

    `players` is the lobby's `Player.id` list; leave it out and the board is
    dealt to `player_count` freshly minted ids instead, which is handy for
    previewing a map before anyone has joined. The list is only read here, to
    decide how many territories to cut and who gets them -- from then on
    ownership lives on the territories.

    `scale` controls feature size: bigger means larger, smoother landforms;
    smaller means noisier, more fragmented terrain. `water_fraction` is the
    share of the map that ends up ocean, and `warp_strength` how contorted the
    coastlines get. The board is always cut into one territory per player per
    `territories_per_player`, so the split comes out exactly even. Landmasses
    under `min_landmass_size` cells stay as scenery and are never claimable.
    """
    rng = random.Random(seed)
    ids = _player_ids(players, player_count)
    heights = height_field(
        width,
        height,
        scale,
        octaves,
        seed,
        edge_bias,
        water_fraction,
        warp_strength,
    )
    moisture = moisture_field(width, height, moisture_scale, seed)

    grid = [[Cell(x, y, float(h), classify_cell(float(h), float(moisture[y][x]))) for x, h in enumerate(row)] for y, row in enumerate(heights)]

    territories = _build_territories(
        grid,
        len(ids) * territories_per_player,
        min_landmass_size,
        rng,
        relaxations,
    )
    _link_land_neighbours(grid, territories)
    _link_sea_routes(territories, sea_route_distance)
    _divide(territories, ids, soldiers_per_territory, rng)

    return WorldMap(width, height, grid, territories)


def create_world(
    width: int = 80,
    height: int = 40,
    seed: int | None = 42,
    players: Sequence[UUID] | None = None,
    player_count: int = PLAYER_COUNT,
    **generate_kwargs,
) -> World:
    """Generate a playable world ready to store on the server and send to clients.

    Pass the lobby's ids as `players` (`[p.id for p in players.values()]`) so
    each territory comes back owned by a real player.
    """
    world_map = generate(
        width=width,
        height=height,
        seed=seed,
        players=players,
        player_count=player_count,
        **generate_kwargs,
    )
    return World.from_map(world_map, seed=seed)
