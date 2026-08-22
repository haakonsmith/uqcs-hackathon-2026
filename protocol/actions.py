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
from protocol.world import World

# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Joined:
    """Accepted into a room, with the id the player is known by.

    The board rides along on the acceptance rather than arriving as a later
    push: a client has nothing to draw until it has one, so a separate event
    would only open a window where it is joined but blank.

    A `World` and not a `terrain.WorldMap`, because this has to survive JSON.
    Call `world.to_map()` for the renderable board.
    """

    player_id: str
    world: World
    kind: Literal["joined"] = "joined"


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
    action: Literal["join"] = "join"


@dataclass(frozen=True)
class Echo(Request[Echoed]):
    text: str = ""
    action: Literal["echo"] = "echo"


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerJoined:
    player_id: str
    kind: Literal["player_joined"] = "player_joined"


@dataclass(frozen=True)
class PlayerLeft:
    player_id: str
    kind: Literal["player_left"] = "player_left"


# --------------------------------------------------------------------------
# Unions
# --------------------------------------------------------------------------

# Annotated rather than `type` aliases: pydantic needs the discriminator to
# switch on the tag instead of trying each member in turn, which is both faster
# and gives an error naming the tag rather than every failed candidate.
ClientRequest = Annotated[Join | Echo, Field(discriminator="action")]
ServerResponse = Annotated[Joined | JoinRejected | Echoed, Field(discriminator="kind")]
ServerEvent = Annotated[PlayerJoined | PlayerLeft, Field(discriminator="kind")]

REQUEST_ADAPTER: TypeAdapter[ClientRequest] = TypeAdapter(ClientRequest)
RESPONSE_ADAPTER: TypeAdapter[ServerResponse] = TypeAdapter(ServerResponse)
EVENT_ADAPTER: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)
