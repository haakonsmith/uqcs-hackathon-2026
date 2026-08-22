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
