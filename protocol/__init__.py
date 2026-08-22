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
    Acknowledged,
    BoardChanged,
    CancelOrders,
    ClientRequest,
    Echo,
    Echoed,
    FinishPhase,
    GameOver,
    GameStarted,
    Join,
    Joined,
    JoinRejected,
    Judged,
    LobbyChanged,
    MovesResolved,
    MoveTroops,
    Ordered,
    Placed,
    PlaceTroops,
    ReadySet,
    Refused,
    RoundChanged,
    ServerEvent,
    ServerResponse,
    SetReady,
    SubmitSolution,
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
from protocol.lobby import Lobby, LobbyPlayer
from protocol.rounds import BattleReport, MoveOrder, Phase, PlayerRound, Problem, RoundState, TerritoryUpdate, Verdict
from protocol.terrain import TerrainInfo, TerritoryState, World

__all__ = [
    "FRAME_ADAPTER",
    "Acknowledged",
    "BattleReport",
    "BoardChanged",
    "CancelOrders",
    "ClientRequest",
    "Connection",
    "Echo",
    "Echoed",
    "EvtFrame",
    "FinishPhase",
    "Frame",
    "GameOver",
    "GameStarted",
    "Join",
    "JoinRejected",
    "Joined",
    "Judged",
    "Lobby",
    "LobbyChanged",
    "LobbyPlayer",
    "MoveOrder",
    "MoveTroops",
    "MovesResolved",
    "Ordered",
    "Phase",
    "PlaceTroops",
    "Placed",
    "PlayerRound",
    "Problem",
    "ProtocolError",
    "ReadySet",
    "Refused",
    "ReqFrame",
    "Request",
    "ResFrame",
    "RoundChanged",
    "RoundState",
    "ServerEvent",
    "ServerResponse",
    "SetReady",
    "SubmitSolution",
    "TerrainInfo",
    "TerritoryState",
    "TerritoryUpdate",
    "Verdict",
    "World",
    "dump_frame",
    "parse_frame",
    "response_types",
]
