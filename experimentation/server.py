import asyncio
import random
import time
from dataclasses import dataclass

import player
import websockets
from player import player
from websockets.asyncio.server import serve

gameState = {}
players = {}
world = {}
territory = {}
playersTarget = 4
lobbyCount = 0


def createWorld():
    return {"numTerritories": 0}


class territory:
    def __init__(self, name, id):
        self.name = name
        self.id = id


def createTerritory(name, id):  # no arguments for now
    return {
        "name": name,
        "id": id,
        "colour": random.randint(1, 15),  # maybe int represents colour, like 1=blue (random for now)
    }


async def startMenu(websocket):
    content = await websocket.recv()
    if content == "joinGame":
        players[websocket] = player(websocket)
        if lobbyCount < playersTarget:
            await websocket.send(f"Logging in")
            await lobby(websocket)
        else:
            websocket.close(playersTarget, "Lobby is full, please reconnect")
    else:
        await websocket.send("invalid initation (temp)")


async def lobby(websocket):
    global lobbyCount
    players[websocket].setStatus("LOBBY")
    lobbyCount += 1
    print(len(players))
    for p in players.values():
        if p.getStatus() == "LOBBY":
            if lobbyCount == playersTarget:
                await game(websocket)
            await p.getWebSocket().send(f"LOBBY: {lobbyCount}/{playersTarget}")


async def game():
    for player in players.values():
        player.getWebSocket().send(f"Game starting")


async def clients(websocket):
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


async def main():
    server = await serve(clients, "localhost", 8765)
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
