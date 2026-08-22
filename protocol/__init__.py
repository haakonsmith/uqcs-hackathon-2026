"""Wire protocol shared by the client and the server.

Both ends import the same message definitions, so a change to a request, a
response or an event is a change on both sides at once - they cannot drift.

    from protocol import Connection, Join, Echo

    async with websockets.connect(url) as ws:
        conn = Connection(ws)
        asyncio.create_task(conn.run())
        joined = await conn.send(Join(room="lobby"))   # Joined | JoinRejected

Layered `core` -> `actions` -> `frames` -> `connection`, so the import graph
stays acyclic. Import from this package rather than the submodules.
"""

from protocol.actions import (
    ClientRequest,
    Echo,
    Echoed,
    Join,
    Joined,
    JoinRejected,
    PlayerJoined,
    PlayerLeft,
    ServerEvent,
    ServerResponse,
)
from protocol.connection import Connection
from protocol.core import ProtocolError, Request, response_types
from protocol.frames import (
    FRAME_ADAPTER,
    EvtFrame,
    Frame,
    ReqFrame,
    ResFrame,
    dump_frame,
    parse_frame,
)
from protocol.terrain import TerrainInfo, TerritoryState, World

__all__ = [
    "FRAME_ADAPTER",
    "ClientRequest",
    "Connection",
    "Echo",
    "Echoed",
    "EvtFrame",
    "Frame",
    "Join",
    "JoinRejected",
    "Joined",
    "PlayerJoined",
    "PlayerLeft",
    "ProtocolError",
    "ReqFrame",
    "Request",
    "ResFrame",
    "ServerEvent",
    "ServerResponse",
    "TerrainInfo",
    "TerritoryState",
    "World",
    "dump_frame",
    "parse_frame",
    "response_types",
]
