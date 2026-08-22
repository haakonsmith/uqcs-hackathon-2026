"""Manual smoke test against a running server on 8888.

Uses the same `protocol` package the server does, so the frame shape cannot
drift out of step with what the server accepts.
"""

import asyncio
import logging

from websockets.asyncio.client import connect

from protocol import Connection, Echo, Join

URI = "ws://localhost:8888"


async def test_client() -> None:
    async with connect(URI) as websocket:
        conn = Connection(websocket)
        reader = asyncio.create_task(conn.run())

        # The response type follows from the request: joined is
        # Joined | JoinRejected, echoed is Echoed. Connection wraps each in a
        # ReqFrame, waits for the ResFrame carrying the same id, and checks the
        # answer is one of the kinds the request declared.
        joined = await conn.send(Join(room="lobby"))
        print(f"join -> {joined!r}")

        echoed = await conn.send(Echo(text="hello"))
        print(f"echo -> {echoed!r}")

        # Anything the server pushed at us while we were talking to it.
        while not conn.events.empty():
            print(f"event -> {conn.events.get_nowait()!r}")

        reader.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_client())
