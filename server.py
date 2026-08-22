import asyncio

from server.server import Server


async def main():
    server = Server()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
