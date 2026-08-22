import asyncio
import json
import logging
import random

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

import server.player

gameState = {}
players = {}
world = {}
territory = {}
playersTarget = 4
lobbyCount = 0


def createWorld():
    return {"numTerritories": 0}


def createTerritory(name: str, id: str):  # no arguments for now
    return {
        "name": name,
        "id": id,
        "colour": random.randint(1, 15),  # maybe int represents colour, like 1=blue (random for now)
    }


async def startMenu(websocket: websockets.ServerConnection):
    content = await websocket.recv()
    if content == "joinGame":
        players[websocket] = player(websocket)
        if lobbyCount < playersTarget:
            await websocket.send("Logging in")
            await lobby(websocket)
        else:
            await websocket.close(playersTarget, "Lobby is full, please reconnect")
    else:
        await websocket.send("invalid initation (temp)")


async def lobby(websocket: websockets.ServerConnection):
    global lobbyCount
    players[websocket].setStatus("LOBBY")
    lobbyCount += 1
    print(len(players))
    for p in players.values():
        if p.getStatus() == "LOBBY":
            if lobbyCount == playersTarget:
                await game()
            await p.getWebSocket().send(f"LOBBY: {lobbyCount}/{playersTarget}")


async def game():
    for player in players.values():
        player.getWebSocket().send("Game starting")


async def clients(websocket: websockets.ServerConnection):
    try:
        await startMenu(websocket)
        await websocket.wait_closed()

    except websockets.exceptions.ConnectionClosed:
        print("disconnected")

    finally:
        if websocket in players:
            global lobbyCount
            if players[websocket].getStatus() == "LOBBY":
                lobbyCount -= 1
            del players[websocket]


class Server:
    def __init__(self) -> None:
        self.connections: set[websockets.ServerConnection] = set()

    async def register_connection(self, websocket: websockets.ServerConnection):
        logging.info("Connection received")
        self.connections.add(websocket)

        async for message in websocket:
            if isinstance(message, str):
                event = json.loads(message)

                print(f"got event {event}")

    async def handler(self, websocket: websockets.ServerConnection):
        try:
            await self.register_connection(websocket)
        except ConnectionClosedError:
            logging.warning("Connection abruptly closed!")
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
            logging.info("Server started")
            await asyncio.Future()


async def main():
    server = Server()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
