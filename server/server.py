import asyncio
import logging
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


# The game will not start below this many players, however eager they are.
MIN_PLAYERS = 2


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

    async def handle_request(self, ws: websockets.ServerConnection, request: ClientRequest) -> ServerResponse:
        """Answer one request. Returning the response keeps correlation out of
        the game logic - the caller tags it with the id it came in on."""
        match request:
            case Join(name=name):
                if self.map is not None:
                    return JoinRejected(reason="a game is already in progress")

                player_id = uuid4()
                self.players[player_id] = Player(player_id, name=name)
                self.sessions[ws] = player_id

                # To everyone else, so the joiner is not told twice - it has
                # the same roster on its `Joined`.
                await self.broadcast(LobbyChanged(lobby=self.lobby()), exclude=ws)
                return Joined(player_id=str(player_id), lobby=self.lobby())

            case SetReady(ready=ready):
                player_id = self.sessions.get(ws)
                player = self.players.get(player_id) if player_id is not None else None
                if player is None:
                    # Readying up before joining. Nothing to record, but the
                    # roster is still a truthful answer.
                    return ReadySet(lobby=self.lobby())

                player.ready = ready
                lobby = self.lobby()
                await self.broadcast(LobbyChanged(lobby=lobby), exclude=ws)
                # The board is pushed by `_answer` once this response is out,
                # so a client is never handed a game mid-request.
                return ReadySet(lobby=lobby)

            case Echo(text=text):
                return Echoed(text=text)

            case _:
                assert_never(request)

    async def start_if_ready(self) -> None:
        """Generate the board and push it, once everybody has readied up.

        Deals to exactly the players in the room, so a three-player game is a
        three-way split rather than a four-way one with an empty share.
        """
        if self.map is not None or not self.lobby().can_start:
            return

        ids = list(self.players)
        logger.info(f"starting a game for {len(ids)} players")
        self.map = create_world(players=ids, player_count=len(ids))
        await self.broadcast(GameStarted(world=self.map))

    async def broadcast(self, event: ServerEvent, exclude: websockets.ServerConnection | None = None) -> None:
        """Push an event to everyone, dropping those that disconnect mid-send."""
        raw = dump_frame(EvtFrame(body=event))
        _ = await asyncio.gather(
            *(ws.send(raw) for ws in self.connections if ws is not exclude),
            return_exceptions=True,
        )

    async def register_connection(self, websocket: websockets.ServerConnection) -> None:
        logger.info(f"Connection received from {websocket.id}")

        if len(self.connections) >= self.player_count:
            await websocket.close(code=1008, reason="lobby full")
            return

        self.connections.add(websocket)

        async for message in websocket:
            try:
                frame = parse_frame(message)
            except ValueError as error:
                logger.error(f"Could not parse frame from {websocket.id}: {error}")
                continue

            match frame:
                case ReqFrame(id=request_id, body=body):
                    await self._answer(websocket, request_id, body)
                case _:
                    logger.warning(f"{websocket.id} sent a {frame.t} frame to the server")

    async def _answer(self, ws: websockets.ServerConnection, request_id: str, request: ClientRequest) -> None:
        try:
            response = await self.handle_request(ws, request)
        except Exception:
            logger.exception(f"Handling {type(request).__name__} failed")
            return
        await ws.send(dump_frame(ResFrame(id=request_id, body=response)))
        # Anything that follows from the request goes out after its answer, so
        # a client is never pushed a game it has not been told it joined.
        await self.start_if_ready()

    async def handler(self, websocket: websockets.ServerConnection) -> None:
        try:
            await self.register_connection(websocket)
        except ConnectionClosedError:
            logger.warning("Connection abruptly closed!")
        finally:
            self.connections.discard(websocket)
            player_id = self.sessions.pop(websocket, None)
            if player_id is not None:
                _ = self.players.pop(player_id, None)
                await self.broadcast(LobbyChanged(lobby=self.lobby()))

    async def run(self) -> None:
        async with serve(
            self.handler,
            "localhost",
            8888,
            ping_interval=None,
            max_size=64 * 1024 * 1024,
        ):
            logger.info("Server started")
            await asyncio.Future()


if __name__ == "__main__":
    server = Server()
    asyncio.run(server.run())
