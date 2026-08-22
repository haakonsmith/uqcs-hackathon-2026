from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID


class Terrain(Enum):
    """A terrain type plus the gameplay rules and palette that ride on it."""

    DEEP_WATER = ("deep water", "~", False, 0, (14, 38, 78))
    SHALLOW_WATER = ("shallow water", "-", False, 0, (30, 78, 132))
    SAND = ("sand", ".", True, 1, (206, 188, 128))
    GRASS = ("grass", ",", True, 1, (96, 142, 68))
    FOREST = ("forest", "T", True, 2, (44, 92, 52))
    HILLS = ("hills", "n", True, 2, (124, 112, 82))
    MOUNTAIN = ("mountain", "A", False, 0, (152, 150, 156))

    label: str
    symbol: str
    passable: bool
    move_cost: int
    color: tuple[int, int, int]

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


@dataclass
class Territory:
    """One claimable region: the unit of ownership in a Risk-like game."""

    id: int
    cells: list[tuple[int, int]] = field(default_factory=list)
    land_neighbours: set[int] = field(default_factory=set)
    sea_neighbours: set[int] = field(default_factory=set)
    owner: UUID | None = None  # Player.id, or None while unclaimed
    soldiers: int = 0

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
class WorldMap:
    width: int
    height: int
    grid: list[list[Cell]]
    territories: list[Territory]

    def cell(self, x: int, y: int) -> Cell:
        return self.grid[y][x]

    def cells(self) -> Iterator[Cell]:
        for row in self.grid:
            yield from row

    @property
    def owners(self) -> list[UUID]:
        """Every player holding ground, first appearance first.

        Ownership lives on the territories themselves, so the roster handed to
        `generate()` is never kept around -- read it back off the board.
        """
        return list(dict.fromkeys(t.owner for t in self.territories if t.owner is not None))

    def owned_by(self, player: UUID) -> list[Territory]:
        return [t for t in self.territories if t.owner == player]


# --------------------------------------------------------------------------
# The same board, flattened for the wire
#
# `WorldMap` above is the shape the renderer and the game logic want: an object
# per cell, sets of neighbours, `Terrain` members. None of that survives JSON,
# so `World` below is the same board as parallel grids and plain lists, and the
# two are converted with `World.from_map()` and `World.to_map()`. The pairing
# is deliberate - `TerritoryState` is not a stray duplicate of `Territory`.
# --------------------------------------------------------------------------

# Heights are noise output, drawn as one of seven terrain bands and shown to
# two decimals. Full float64 text triples the size of a board on the wire to
# carry precision nothing reads.
HEIGHT_DIGITS = 3


@dataclass
class TerrainInfo:
    """Palette and rules for one terrain type, so clients can render it."""

    name: str
    label: str
    symbol: str
    passable: bool
    move_cost: int
    colour: tuple[int, int, int]


@dataclass
class TerritoryState:
    """One claimable region on the board."""

    id: int
    cells: list[tuple[int, int]] = field(default_factory=list)
    land_neighbours: list[int] = field(default_factory=list)
    sea_neighbours: list[int] = field(default_factory=list)
    soldiers: int = 0
    owner: UUID | None = None  # Player.id, serialised as a string

    @property
    def size(self) -> int:
        return len(self.cells)


@dataclass
class World:
    """Full map snapshot the server owns and can push over websockets."""

    width: int
    height: int
    seed: int | None
    # Row-major heights in [0.0, 1.0] — heights[y][x]
    heights: list[list[float]]
    # Parallel to heights: Terrain member name per cell
    terrain: list[list[str]]
    # Parallel to heights: territory id per cell, or None for ocean / unclaimed
    territory_ids: list[list[int | None]]
    territories: list[TerritoryState] = field(default_factory=list)
    terrain_types: list[TerrainInfo] = field(default_factory=list)

    @property
    def owners(self) -> list[UUID]:
        """Every player holding ground, first appearance first."""
        return list(dict.fromkeys(t.owner for t in self.territories if t.owner is not None))

    def cell_height(self, x: int, y: int) -> float:
        return self.heights[y][x]

    def territory_at(self, x: int, y: int) -> TerritoryState | None:
        tid = self.territory_ids[y][x]
        if tid is None:
            return None
        return self.territories[tid]

    def owned_by(self, player: UUID) -> list[TerritoryState]:
        return [t for t in self.territories if t.owner == player]

    def to_map(self) -> WorldMap:
        """Rebuild the renderable board. The inverse of `from_map`.

        Territory ids are indices into `territories`, and `from_map` wrote
        them in order, so the grid can point straight at the rebuilt objects
        without a lookup table.
        """
        grid = [
            [
                Cell(
                    x=x,
                    y=y,
                    height=self.heights[y][x],
                    terrain=Terrain[self.terrain[y][x]],
                    territory=self.territory_ids[y][x],
                )
                for x in range(self.width)
            ]
            for y in range(self.height)
        ]
        territories = [
            Territory(
                id=state.id,
                cells=[(x, y) for x, y in state.cells],
                land_neighbours=set(state.land_neighbours),
                sea_neighbours=set(state.sea_neighbours),
                owner=state.owner,
                soldiers=state.soldiers,
            )
            for state in self.territories
        ]
        return WorldMap(width=self.width, height=self.height, grid=grid, territories=territories)

    @classmethod
    def from_map(cls, world_map: WorldMap, seed: int | None = None) -> "World":
        """Build serialisable state from a generated WorldMap."""
        return cls(
            width=world_map.width,
            height=world_map.height,
            seed=seed,
            heights=[[round(cell.height, HEIGHT_DIGITS) for cell in row] for row in world_map.grid],
            terrain=[[cell.terrain.name for cell in row] for row in world_map.grid],
            territory_ids=[[cell.territory for cell in row] for row in world_map.grid],
            territories=[
                TerritoryState(
                    id=t.id,
                    cells=list(t.cells),
                    land_neighbours=sorted(t.land_neighbours),
                    sea_neighbours=sorted(t.sea_neighbours),
                    soldiers=t.soldiers,
                    owner=t.owner,
                )
                for t in world_map.territories
            ],
            terrain_types=[
                TerrainInfo(
                    name=terrain.name,
                    label=terrain.label,
                    symbol=terrain.symbol,
                    passable=terrain.passable,
                    move_cost=terrain.move_cost,
                    colour=terrain.color,
                )
                for terrain in Terrain
            ],
        )
