import asyncio
import logging
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
    Join,
    Joined,
    PlayerJoined,
    PlayerLeft,
    ReqFrame,
    ResFrame,
    ServerEvent,
    ServerResponse,
    dump_frame,
    parse_frame,
)
from server.player import Player

logger = logging.getLogger("Server")


class Server:
    def __init__(self) -> None:
        self.connections: set[websockets.ServerConnection] = set()
        self.game: None = None  # put your game state here!
        self.players: dict[UUID, Player] = {}
        # Which player a socket is logged in as, so a drop can be announced.
        self.sessions: dict[websockets.ServerConnection, UUID] = {}

    async def handle_request(self, ws: websockets.ServerConnection, request: ClientRequest) -> ServerResponse:
        """Answer one request. Returning the response keeps correlation out of
        the game logic - the caller tags it with the id it came in on."""
        match request:
            case Join(room=room):
                player_id = uuid4()
                self.players[player_id] = Player(player_id)
                self.sessions[ws] = player_id
                await self.broadcast(PlayerJoined(player_id=str(player_id), room=room), exclude=ws)
                return Joined(player_id=str(player_id), room=room)
            case Echo(text=text):
                return Echoed(text=text)
            case _:
                # Fails to type check the moment a request joins the union
                # without a case here, rather than silently going unanswered.
                assert_never(request)

    async def broadcast(self, event: ServerEvent, exclude: websockets.ServerConnection | None = None) -> None:
        """Push an event to everyone, dropping those that disconnect mid-send."""
        raw = dump_frame(EvtFrame(body=event))
        _ = await asyncio.gather(
            *(ws.send(raw) for ws in self.connections if ws is not exclude),
            return_exceptions=True,
        )

    async def register_connection(self, websocket: websockets.ServerConnection) -> None:
        logger.info(f"Connection received from {websocket.id}")
        self.connections.add(websocket)

        async for message in websocket:
            try:
                frame = parse_frame(message)
            except ValueError as error:
                # Malformed or unknown message. Nothing to reply on, since the
                # id lives inside the frame we just failed to read.
                logger.error(f"Could not parse frame from {websocket.id}: {error}")
                continue

            match frame:
                case ReqFrame(id=request_id, body=body):
                    await self._answer(websocket, request_id, body)
                case _:
                    # Only clients send requests; anything else means the peer
                    # is confused, and silence would make that unfindable.
                    logger.warning(f"{websocket.id} sent a {frame.t} frame to the server")

    async def _answer(self, ws: websockets.ServerConnection, request_id: str, request: ClientRequest) -> None:
        try:
            response = await self.handle_request(ws, request)
        except Exception:
            # One bad request must not take down the whole connection loop.
            logger.exception(f"Handling {type(request).__name__} failed")
            return
        await ws.send(dump_frame(ResFrame(id=request_id, body=response)))

    async def handler(self, websocket: websockets.ServerConnection) -> None:
        try:
            await self.register_connection(websocket)
        except ConnectionClosedError:
            logger.warning("Connection abruptly closed!")
        finally:
            self.connections.discard(websocket)
            player_id = self.sessions.pop(websocket, None)
            if player_id is not None:
                self.players.pop(player_id, None)
                await self.broadcast(PlayerLeft(player_id=str(player_id)))

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
