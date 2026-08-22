import asyncio
import logging
import time
from dataclasses import dataclass
from typing import assert_never
from uuid import UUID, uuid4

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from protocol import (
    ClientRequest,
    Echo,
    Echoed,
    EvtFrame,
    GameStarted,
    Join,
    Joined,
    JoinRejected,
    LobbyChanged,
    ReadySet,
    ReqFrame,
    ResFrame,
    ServerEvent,
    ServerResponse,
    SetReady,
    dump_frame,
    parse_frame,
)
from protocol.lobby import Lobby, LobbyPlayer
from server.player import Player
from server.world import World, create_world

logger = logging.getLogger("Server")


# Loopback by default: a dev server that binds every interface the moment it
# is run is a surprise. Pass --host 0.0.0.0 to let the network in.
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8888

# The game will not start below this many players, however eager they are.
MIN_PLAYERS = 2


def _short(value: object) -> str:
    """First block of a UUID. Enough to follow one player through a log."""
    return str(value)[:8]


def _label(player: Player) -> str:
    return f"{player.name}[{_short(player.id)}]"


@dataclass
class Server:
    connections: set[websockets.ServerConnection]
    players: dict[UUID, Player]
    sessions: dict[websockets.ServerConnection, UUID]
    map: World | None
    player_count: int

    def __init__(self) -> None:
        self.connections: set[websockets.ServerConnection] = set()
        self.players: dict[UUID, Player] = {}
        # Which player a socket is logged in as, so a drop can be announced.
        self.sessions: dict[websockets.ServerConnection, UUID] = {}

        # No board until the lobby says go. Generating one up front would deal
        # territories to players who never turned up, and take the count of
        # them from a constant rather than from who is actually in the room.
        self.map: World | None = None
        self.player_count = 4

    def lobby(self) -> Lobby:
        """The roster as the clients see it. Insertion order is join order."""
        return Lobby(
            players=[LobbyPlayer(id=str(p.id), name=p.name, ready=p.ready) for p in self.players.values()],
            minimum=MIN_PLAYERS,
            capacity=self.player_count,
        )

    def roster(self) -> str:
        """The room in one line, for the log. `*` marks a player who is ready."""
        if not self.players:
            return "lobby empty"
        names = " ".join(f"{'*' if p.ready else ' '}{p.name}" for p in self.players.values())
        return f"lobby {len(self.players)}/{self.player_count}:{names}"

    async def handle_request(self, ws: websockets.ServerConnection, request: ClientRequest) -> ServerResponse:
        """Answer one request. Returning the response keeps correlation out of
        the game logic - the caller tags it with the id it came in on."""
        match request:
            case Join(name=name):
                if self.map is not None:
                    logger.info(f"turning away {name!r} from {_short(ws.id)}: a game is already running")
                    return JoinRejected(reason="a game is already in progress")

                player_id = uuid4()
                player = Player(player_id, name=name)
                self.players[player_id] = player
                self.sessions[ws] = player_id

                lobby = self.lobby()
                logger.info(f"{_label(player)} joined - {self.roster()}")
                # To everyone else, so the joiner is not told twice - it has
                # the same roster on its `Joined`.
                await self.broadcast(LobbyChanged(lobby=lobby), exclude=ws)
                return Joined(player_id=str(player_id), lobby=lobby)

            case SetReady(ready=ready):
                player_id = self.sessions.get(ws)
                player = self.players.get(player_id) if player_id is not None else None
                if player is None:
                    logger.warning(f"{_short(ws.id)} set ready without joining first")
                    # Readying up before joining. Nothing to record, but the
                    # roster is still a truthful answer.
                    return ReadySet(lobby=self.lobby())

                player.ready = ready
                lobby = self.lobby()
                logger.info(
                    f"{_label(player)} is {'ready' if ready else 'not ready'}"
                    f" - {lobby.ready_count}/{len(lobby.players)} ready, {lobby.waiting_for()}"
                )
                await self.broadcast(LobbyChanged(lobby=lobby), exclude=ws)
                # The board is pushed by `_answer` once this response is out,
                # so a client is never handed a game mid-request.
                return ReadySet(lobby=lobby)

            case Echo(text=text):
                logger.debug(f"echo from {_short(ws.id)}: {text!r}")
                return Echoed(text=text)

            case _:
                assert_never(request)

    async def start_if_ready(self) -> None:
        """Generate the board and push it, once everybody has readied up.

        Deals to exactly the players in the room, so a three-player game is a
        three-way split rather than a four-way one with an empty share.
        """
        if self.map is not None:
            return
        if not self.lobby().can_start:
            logger.debug(f"not starting yet - {self.lobby().waiting_for()}")
            return

        ids = list(self.players)
        logger.info(f"all {len(ids)} players ready - generating the board")
        started = time.perf_counter()
        self.map = create_world(players=ids, player_count=len(ids))
        elapsed = (time.perf_counter() - started) * 1000
        logger.info(
            f"board ready in {elapsed:.0f} ms: {self.map.width}x{self.map.height},"
            f" {len(self.map.territories)} territories across {len(self.map.owners)} players"
        )
        await self.broadcast(GameStarted(world=self.map))

    async def broadcast(self, event: ServerEvent, exclude: websockets.ServerConnection | None = None) -> None:
        """Push an event to everyone, dropping those that disconnect mid-send."""
        raw = dump_frame(EvtFrame(body=event))
        targets = [ws for ws in self.connections if ws is not exclude]
        results = await asyncio.gather(*(ws.send(raw) for ws in targets), return_exceptions=True)

        logger.debug(f"{event.kind} -> {len(targets)} client(s), {len(raw)} bytes")
        # gather() with return_exceptions swallows these, and a push that
        # silently never lands is the worst kind of bug to chase.
        for ws, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(f"could not push {event.kind} to {_short(ws.id)}: {result!r}")

    async def register_connection(self, websocket: websockets.ServerConnection) -> None:
        who = _short(websocket.id)

        if len(self.connections) >= self.player_count:
            logger.warning(f"refusing {who}: {len(self.connections)} connections already, lobby is full")
            await websocket.close(code=1008, reason="lobby full")
            return

        self.connections.add(websocket)
        logger.info(f"{who} connected ({len(self.connections)}/{self.player_count} sockets)")

        async for message in websocket:
            try:
                frame = parse_frame(message)
            except ValueError as error:
                logger.error(f"unparseable frame from {who}: {error}")
                logger.debug(f"raw frame was {message!r}")
                continue

            match frame:
                case ReqFrame(id=request_id, body=body):
                    await self._answer(websocket, request_id, body)
                case _:
                    logger.warning(f"{who} sent a {frame.t} frame to the server")

    async def _answer(self, ws: websockets.ServerConnection, request_id: str, request: ClientRequest) -> None:
        who = _short(ws.id)
        logger.debug(f"{who} -> {request.action} {request_id[:8]}")
        try:
            response = await self.handle_request(ws, request)
        except Exception:
            logger.exception(f"{who}: handling {type(request).__name__} failed")
            return

        raw = dump_frame(ResFrame(id=request_id, body=response))
        logger.debug(f"{who} <- {response.kind} {request_id[:8]}, {len(raw)} bytes")
        await ws.send(raw)
        # Anything that follows from the request goes out after its answer, so
        # a client is never pushed a game it has not been told it joined.
        await self.start_if_ready()

    async def handler(self, websocket: websockets.ServerConnection) -> None:
        who = _short(websocket.id)
        try:
            await self.register_connection(websocket)
        except ConnectionClosedError:
            logger.warning(f"{who} dropped without closing cleanly")
        finally:
            self.connections.discard(websocket)
            player_id = self.sessions.pop(websocket, None)
            if player_id is None:
                logger.info(f"{who} disconnected before joining")
            else:
                gone = self.players.pop(player_id, None)
                name = _label(gone) if gone is not None else _short(player_id)
                logger.info(f"{name} left - {self.roster()}")
                if self.map is not None:
                    logger.warning(f"{name} left a game in progress; their territories are now unplayed")
                await self.broadcast(LobbyChanged(lobby=self.lobby()))

    async def run(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        """Serve until cancelled. `host` is the interface to bind, not a peer."""
        async with serve(
            self.handler,
            host,
            port,
            ping_interval=None,
            max_size=64 * 1024 * 1024,
        ):
            logger.info(f"listening on ws://{host}:{port} - {MIN_PLAYERS} to {self.player_count} players")
            if host in ("0.0.0.0", "::"):
                logger.warning(f"bound to {host}: reachable from the network, with no authentication")
            await asyncio.Future()
