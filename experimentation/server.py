import asyncio
import random
import time

from websockets.asyncio.server import ServerConnection, serve

gameState = {}
player = {}
world = {}
territory = {}


def createPlayer(websocket: ServerConnection):
    playerId = str(websocket.id)  # uuid??

    return {
        "userID": playerId,  # idk what player values to add except userID as primary
        "connectionTime": time.time(),
        "lastSeen": time.time(),  # for client heartbeat
        "name": None,  # no name yet (idk if we will ask names)
        "status": "Unknown",
        "territory": None,
        "points": 0,
        "currentAction": {"mouseHover": False, "mousePos": (0, 0), "mouseTile": (0, 0)},
    }


def createTerritory(name, id):  # no arguments for now
    return {
        "name": name,
        "id": id,
        "colour": random.randint(1, 15),  # maybe int represents colour, like 1=blue
    }


async def game(websocket):
    player[websocket] = createPlayer(websocket)


# name = await websocket.recv();
# print(f"<<< {name}")

# greeting = f"Hello {name}!"

# await websocket.send(greeting)
# print(f">>> {greeting}")


async def main():
    server = await serve(game, "localhost", 8765)
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
