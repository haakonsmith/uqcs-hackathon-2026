
import asyncio
import time

from websockets.asyncio.server import serve

players = {};

async def game(websocket):
    playerId = websocket.id; #uuid??

    players[websocket] = {
        "userID": playerId, #idk what player values to add except userID as primary
        "connectionTime": time.time(),
        "lastSeen": time.time(), #for client heartbeat
        "name": None, #no name yet (idk if we will ask names)
        "status": "Unknown",

        "territory": None,
        "points": 0
    }

    
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