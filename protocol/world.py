"""The board as it travels over the wire.

`terrain.WorldMap` is the shape the game logic and the renderer want: objects
per cell, sets of neighbours, `Terrain` members. That is a poor fit for JSON,
so this is the same board flattened into parallel grids and plain lists.

It lives in `protocol` rather than on the server because both ends have to
agree on it byte for byte: the server builds one with `World.from_map()` after
generating a board, the client turns it back with `.to_map()` and draws it.
Generation itself stays on the server - it needs numpy, and a client has no
business inventing a board.

Layered below `actions`, which puts a `World` inside `Joined`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from protocol.terrain import Cell, Terrain, Territory, WorldMap


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
    def from_map(cls, world_map: WorldMap, seed: int | None = None) -> World:
        """Build serialisable state from a generated WorldMap."""
        return cls(
            width=world_map.width,
            height=world_map.height,
            seed=seed,
            heights=[[cell.height for cell in row] for row in world_map.grid],
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
