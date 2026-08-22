import asyncio
import random

import websockets

from server import player
from server.server import Server

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
        players[websocket] = player.Player(websocket.id)
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


async def main():
    server = Server()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
