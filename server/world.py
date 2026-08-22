"""Server-side world state for websocket broadcast.

Holds the terrain height field and claimable territories in plain dataclasses
so they serialise cleanly to JSON for clients.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# terrain.py lives at the repo root, one level above this package.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import terrain


@dataclass
class TerritoryState:
    """One claimable region on the board."""

    id: int
    owner: int
    cells: list[tuple[int, int]] = field(default_factory=list)
    land_neighbours: list[int] = field(default_factory=list)
    sea_neighbours: list[int] = field(default_factory=list)
    soldiers: int = 0
    owner: str | None = None

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
    # Parallel to heights: territory id per cell, or None for ocean / unclaimed
    territory_ids: list[list[int | None]]
    territories: list[TerritoryState] = field(default_factory=list)

    def cell_height(self, x: int, y: int) -> float:
        return self.heights[y][x]

    def territory_at(self, x: int, y: int) -> TerritoryState | None:
        tid = self.territory_ids[y][x]
        if tid is None:
            return None
        return self.territories[tid]

    def to_dict(self) -> dict:
        """JSON-ready payload for websocket.send(json.dumps(...))."""
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_terrain(
        cls,
        world_map: terrain.WorldMap,
        seed: int | None = None,
        colours: list[int] | None = None,
    ) -> World:
        """Build server state from a generated terrain.WorldMap."""
        heights = [[cell.height for cell in row] for row in world_map.grid]
        territory_ids = [[cell.territory for cell in row] for row in world_map.grid]

        territories: list[TerritoryState] = []
        for t in world_map.territories:
            colour = colours[t.id] if colours is not None else (t.id % 15) + 1
            territories.append(
                TerritoryState(
                    id=t.id,
                    name=f"Territory {t.id}",
                    continent=t.continent,
                    colour=colour,
                    cells=list(t.cells),
                    land_neighbours=sorted(t.land_neighbours),
                    sea_neighbours=sorted(t.sea_neighbours),
                )
            )

        return cls(
            width=world_map.width,
            height=world_map.height,
            seed=seed,
            heights=heights,
            territory_ids=territory_ids,
            territories=territories,
        )


def create_world(
    width: int = 80,
    height: int = 40,
    seed: int | None = 42,
    **generate_kwargs,
) -> World:
    """Generate a playable world ready to store on the server and send to clients."""
    world_map = terrain.generate(width=width, height=height, seed=seed, **generate_kwargs)
    return World.from_terrain(world_map, seed=seed)
