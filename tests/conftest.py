"""Boards small enough to reason about, for the combat rules.

`create_world` needs numpy, a seed and a few hundred milliseconds to produce a
board nobody can hold in their head. The rules under test only ever read
`owner`, `soldiers` and `neighbours`, so the fixtures here build territories
directly and leave the grid empty.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from protocol.terrain import Cell, Terrain, Territory, WorldMap


def board(*specs: tuple[UUID | None, int], links: dict[int, set[int]] | None = None) -> WorldMap:
    """A board of `len(specs)` territories, each `(owner, soldiers)`.

    Without `links` every territory borders every other, which is what most
    cases want: adjacency is validated elsewhere and forcing each test to draw
    a map would bury the rule it is checking.
    """
    count = len(specs)
    territories = [Territory(id=i, cells=[(i, 0)], owner=owner, soldiers=soldiers) for i, (owner, soldiers) in enumerate(specs)]
    for territory in territories:
        wanted = links[territory.id] if links is not None else {i for i in range(count) if i != territory.id}
        territory.land_neighbours = set(wanted)

    # One row of cells, territory `i` at x == i. The rules never look at the
    # grid, but anything driven through the cursor resolves a screen position
    # to a cell, so it has to be a real one.
    grid = [[Cell(x=i, y=0, height=0.5, terrain=Terrain.GRASS, territory=i) for i in range(count)]]
    return WorldMap(width=count, height=1, grid=grid, territories=territories)


@pytest.fixture
def alice() -> UUID:
    return uuid4()


@pytest.fixture
def bob() -> UUID:
    return uuid4()


@pytest.fixture
def carol() -> UUID:
    return uuid4()


@pytest.fixture
def names(alice: UUID, bob: UUID, carol: UUID) -> dict[UUID, str]:
    return {alice: "alice", bob: "bob", carol: "carol"}
