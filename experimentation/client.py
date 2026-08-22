from websockets.sync.client import connect


def listen_to_server():
    uri = "ws://localhost:8765"
    with connect(uri) as websocket:
        inp = input("what u want to do ")
        websocket.send(inp)
        while True:
            for message in websocket:
                print(f"<<< {message}")


if __name__ == "__main__":
    listen_to_server()
