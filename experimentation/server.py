
import asyncio
import time
import random

from websockets.asyncio.server import serve
from dataclasses import dataclass

gameState = {};
player = {};
world = {};
territory = {};

def createWorld():
    return {
        "numTerritories": 0
    }


@dataclass
class currentAction:
    mouseHover: bool = False
    mousePos: tuple = (0, 0)
    mouseTile: tuple = (0, 0)
    mouseClick: bool = False #temp i guess


class player:
    def __init__(self, userID, name, status, territory):
        self.userID = userID;
        self.name = name;
        self.status = status;
        self.territory = territory;
        self.points = 0;

        self.connectionTime = time.time(),
        self.lastSeen = self.connectionTime,
        self.currentAction = currentAction()

class territory:
    def __init__(self, name, id):
        self.name = name;
        self.id = id;

def createPlayer(websocket):
    playerId = str(websocket.id); #uuid??
    #player = player(playerId, name, status, territory)
    """
    return {
        "userID": playerId, #idk what player values to add except userID as primary
        "connectionTime": time.time(),
        "lastSeen": time.time(), #for client heartbeat
        "name": None, #no name yet (idk if we will ask names)
        "status": "Unknown",

        "territory": None,
        "points": 0,
        "currentAction": {
            "mouseHover": False,
            "mousePos": (0,0),
            "mouseTile": (0,0)
        }
    }
    """


def createTerritory(name, id): #no arguments for now 
    return {
        "name": name,
        "id": id,
        "colour": random.randint(1, 15), #maybe int represents colour, like 1=blue (random for now)
    }

async def initiate(websocket):
    player[websocket] = createPlayer(websocket);
    world[websocket] = createWorld();

async def game(websocket):
    player[websocket] = createPlayer(websocket);

    #name = await websocket.recv();
    #print(f"<<< {name}")

    #greeting = f"Hello {name}!"

    #await websocket.send(greeting)
    #print(f">>> {greeting}")

async def main():
    server = await serve(game, "localhost", 8765)
    await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())