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
from protocol.rounds import BattleReport, MoveOrder, Placement, RoundState, TerritoryUpdate, Verdict
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
class Judged:
    """The verdict on one submission, and the round it left behind."""

    verdict: Verdict
    round: RoundState
    kind: Literal["judged"] = "judged"


@dataclass(frozen=True)
class Planned:
    """This player's plan for the phase, as it stands after being added to.

    One answer for placing and for marching, because they spend the same
    troops: a march can send soldiers placed this phase, so a client told about
    one without the other would show a garrison it cannot account for.

    Sent only to the player who made it, and never broadcast - a plan the room
    can see is a plan nobody can be surprised by.
    """

    placements: list[Placement]
    orders: list[MoveOrder]
    # Troops earned this round and not yet promised to anywhere.
    remaining: int
    round: RoundState
    kind: Literal["planned"] = "planned"


@dataclass(frozen=True)
class Refused:
    """A move the rules do not allow. The board is unchanged."""

    reason: str
    kind: Literal["refused"] = "refused"


@dataclass(frozen=True)
class Acknowledged:
    """A request that changed nothing but the round state."""

    round: RoundState
    kind: Literal["acknowledged"] = "acknowledged"


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
class SubmitSolution(Request[Judged | Refused]):
    """Try a solution. Allowed as many times as the phase has seconds left."""

    code: str
    action: Literal["submit_solution"] = "submit_solution"


@dataclass(frozen=True)
class PlaceTroops(Request[Planned | Refused]):
    """Promise `count` of this round's troops to a territory already owned.

    Nothing reaches the board here. The troops join the plan and land when the
    phase ends, so until then they are a `(+n)` on one player's screen.
    """

    territory: int
    count: int = 1
    action: Literal["place_troops"] = "place_troops"


@dataclass(frozen=True)
class MoveTroops(Request[Planned | Refused]):
    """Order troops to a neighbouring territory, friendly or not.

    Queued rather than applied: everybody's orders resolve together when the
    phase ends, so two players can march into the same place at once.
    """

    source: int
    target: int
    count: int
    action: Literal["move_troops"] = "move_troops"


@dataclass(frozen=True)
class CancelPlan(Request[Planned | Refused]):
    """Tear up this player's plan for the phase, placements and orders alike.

    Placed troops go back into hand rather than onto the board: nothing has
    happened yet, so there is nothing to take back off it.
    """

    action: Literal["cancel_plan"] = "cancel_plan"


@dataclass(frozen=True)
class FinishPhase(Request[Acknowledged | Refused]):
    """Say this player is done, so the phase can end before its clock does."""

    action: Literal["finish_phase"] = "finish_phase"


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


@dataclass(frozen=True)
class RoundChanged:
    """A new round, a new phase, or somebody's progress within one."""

    round: RoundState
    kind: Literal["round_changed"] = "round_changed"


@dataclass(frozen=True)
class BoardChanged:
    """Territories that moved, as a delta rather than a fresh board."""

    updates: list[TerritoryUpdate]
    kind: Literal["board_changed"] = "board_changed"


@dataclass(frozen=True)
class GameOver:
    """One player holds the whole board. The round loop has stopped."""

    winner: str
    name: str
    kind: Literal["game_over"] = "game_over"


@dataclass(frozen=True)
class MovesResolved:
    """The phase ended and every order was carried out at once.

    Only contested territories get a report; a march into empty ground of your
    own is just a board delta.
    """

    battles: list[BattleReport]
    kind: Literal["moves_resolved"] = "moves_resolved"


# --------------------------------------------------------------------------
# Unions
# --------------------------------------------------------------------------

# Annotated rather than `type` aliases: pydantic needs the discriminator to
# switch on the tag instead of trying each member in turn, which is both faster
# and gives an error naming the tag rather than every failed candidate.
ClientRequest = Annotated[
    Join | SetReady | SubmitSolution | PlaceTroops | MoveTroops | CancelPlan | FinishPhase | Echo,
    Field(discriminator="action"),
]
ServerResponse = Annotated[
    Joined | JoinRejected | ReadySet | Judged | Planned | Acknowledged | Refused | Echoed,
    Field(discriminator="kind"),
]
ServerEvent = Annotated[
    LobbyChanged | GameStarted | RoundChanged | BoardChanged | MovesResolved | GameOver,
    Field(discriminator="kind"),
]

REQUEST_ADAPTER: TypeAdapter[ClientRequest] = TypeAdapter(ClientRequest)
RESPONSE_ADAPTER: TypeAdapter[ServerResponse] = TypeAdapter(ServerResponse)
EVENT_ADAPTER: TypeAdapter[ServerEvent] = TypeAdapter(ServerEvent)
