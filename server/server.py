import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from server.player import Player

logger = logging.getLogger("Server")


Actions = Literal["echo"]


@dataclass
class ServerAction[T]:
    action: Actions
    payload: T


class Server:
    def __init__(self) -> None:
        self.connections: set[websockets.ServerConnection] = set()
        self.game: None = None  # put your game state here!
        self.players: dict[UUID, Player] = {}

    async def register_connection(self, websocket: websockets.ServerConnection):
        logger.info(
            f"Connection received from {websocket.id}",
        )
        self.connections.add(websocket)

        async for message in websocket:
            try:
                if isinstance(message, str):
                    event = ServerAction(**json.loads(message))

                    match event.action:
                        case "echo":
                            await websocket.send(json.dumps(ServerAction(action="echo", payload=None)))

                    print(f"got event {event}")
            except Exception as e:
                logger.error(f"Failed to handle message {e}")

    async def handler(self, websocket: websockets.ServerConnection):
        try:
            await self.register_connection(websocket)
        except ConnectionClosedError:
            logger.warning("Connection abruptly closed!")
        finally:
            self.connections.remove(websocket)

    async def run(self):

        async with serve(
            self.handler,
            "localhost",
            8888,
            ping_interval=None,
            max_size=64 * 1024 * 1024,
        ):
            logger.info("Server started")
            await asyncio.Future()
