
import asyncio
import time
import random

from websockets.asyncio.server import serve
from dataclasses import dataclass
import websockets
import player

gameState = {};
players = {};
world = {};
territory = {};

def createWorld():
    return {
        "numTerritories": 0
    }


class territory:
    def __init__(self, name, id):
        self.name = name;
        self.id = id;

def createTerritory(name, id): #no arguments for now 
    return {
        "name": name,
        "id": id,
        "colour": random.randint(1, 15), #maybe int represents colour, like 1=blue (random for now)
    }


async def startMenu(websocket):
    content = await websocket.recv();
    if (content == "joinGame"):
        players[websocket] = player(websocket);
        await websocket.send(f"Logging in");
    else:
        await websocket.send("invalid initation (temp)");

async def clients(websocket):
    #players[websocket] = player(websocket);

    try:
        await startMenu(websocket);

    except: websockets.exceptions.ConnectionClosed;

    finally:
        if websocket in players:
            del players[websocket];

async def main():
    server = await serve(clients, "localhost", 8765)
    await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())