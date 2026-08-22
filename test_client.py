import asyncio

from websockets.asyncio.client import connect


async def test_client():
    uri = "ws://localhost:8888"
    async with connect(uri) as websocket:
        message = "Hello, WebSocket Server!"
        print(f"Sending to server: {message}")
        await websocket.send(message)

        response = await websocket.recv()
        print(f"Received from server: {response}")


if __name__ == "__main__":
    asyncio.run(test_client())
