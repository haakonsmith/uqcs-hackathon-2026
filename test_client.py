import asyncio
import json

from websockets.asyncio.client import connect


async def test_client():
    uri = "ws://localhost:8888"
    async with connect(uri) as websocket:
        payload = {"action": "echo", "payload": None}
        print(f"Sennding {payload}")
        await websocket.send(json.dumps(payload))

        response = await websocket.recv()
        print(f"Received from server: {response}")


if __name__ == "__main__":
    asyncio.run(test_client())
