import asyncio

from server.server import Server

gameState = {}
players = {}
world: World | None = None
territory = {}
playersTarget = 4
lobbyCount = 0


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
            await p.getWebSocket().send(f"LOBBY: {lobbyCount}/{playersTarget}")
    if lobbyCount == playersTarget:
        await game()


async def game():
    global world
    world = create_world()
    payload = world.to_json()
    for p in players.values():
        p.setStatus("GAME")
        await p.getWebSocket().send("Game starting")
        await p.getWebSocket().send(payload)


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
