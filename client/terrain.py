"""Client-facing re-exports of map types and local-viewer helpers.

Terrain generation lives in `server.world`. Multiplayer clients should receive
a `World` JSON payload over websockets rather than calling `generate` locally.
These imports keep `main.py` / `viewer.py` working for offline map previews.
"""

from server.world import (  # noqa: F401
    SEA_LEVEL,
    TERRAIN_BANDS,
    Cell,
    Continent,
    Terrain,
    Territory,
    WorldMap,
    classify,
    generate,
    height_field,
    render,
    render_territories,
    summary,
)
