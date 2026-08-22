"""Concrete messages on the wire.

Three categories, each a discriminated union:

- `ClientRequest` - things a client asks for, each declaring its response type
- `ServerResponse` - the answers, correlated back by frame id
- `ServerEvent` - things the server pushes without being asked

Each request subclasses `Request[...]` with the responses it may receive, so
the response type of a call follows from the request that made it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from protocol.core import Request
from protocol.lobby import Lobby
from protocol.terrain import World

# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Joined:
    """Accepted into the waiting room, with the id the player is known by.

    No board here: it does not exist yet. The server generates one only once
    everybody has readied up, and pushes it with `GameStarted`.
    """

    player_id: str
    lobby: Lobby
    kind: Literal["joined"] = "joined"


@dataclass(frozen=True)
class ReadySet:
    """A ready flag was accepted, with the roster it produced."""

    lobby: Lobby
    kind: Literal["ready_set"] = "ready_set"


@dataclass(frozen=True)
class JoinRejected:
    reason: str
    kind: Literal["join_rejected"] = "join_rejected"


@dataclass(frozen=True)
class Echoed:
    text: str
    kind: Literal["echoed"] = "echoed"


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Join(Request[Joined | JoinRejected]):
    room: str
    name: str = "Player"
    action: Literal["join"] = "join"


@dataclass(frozen=True)
class SetReady(Request[ReadySet]):
    """Raise or lower this player's hand in the waiting room."""

    ready: bool = True
    action: Literal["set_ready"] = "set_ready"


@dataclass(frozen=True)
class Echo(Request[Echoed]):
    text: str = ""
    action: Literal["echo"] = "echo"


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LobbyChanged:
    """Somebody joined, left, or changed their mind about being ready.

    Carries the whole roster rather than naming who moved: see `protocol.lobby`
    for why the state is resent instead of patched.
    """

    lobby: Lobby
    kind: Literal["lobby_changed"] = "lobby_changed"


@dataclass(frozen=True)
class GameStarted:
    """Everyone readied up, so here is the board they will play on.

    A `World` and not a `terrain.WorldMap`, because this has to survive JSON.
    Call `world.to_map()` for the renderable board.
    """

    world: World
    kind: Literal["game_started"] = "game_started"


# --------------------------------------------------------------------------
# Unions
# --------------------------------------------------------------------------

# Annotated rather than `type` aliases: pydantic needs the discriminator to
# switch on the tag instead of trying each member in turn, which is both faster
# and gives an error naming the tag rather than every failed candidate.
ClientRequest = Annotated[Join | SetReady | Echo, Field(discriminator="action")]
ServerResponse = Annotated[Joined | JoinRejected | ReadySet | Echoed, Field(discriminator="kind")]
ServerEvent = Annotated[LobbyChanged | GameStarted, Field(discriminator="kind")]

REQUEST_ADAPTER: TypeAdapter[ClientRequest] = TypeAdapter(ClientRequest)
RESPONSE_ADAPTER: TypeAdapter[ServerResponse] = TypeAdapter(ServerResponse)
EVENT_ADAPTER: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)
