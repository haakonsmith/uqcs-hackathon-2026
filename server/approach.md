Just current ideas for server implementation via websockets.

Client inputs are sent to the server via websocket.send?
The server then receives those inputs via websocket.recv() and validates the inputs on the server to never trust the client.
Server is where the game mechanism lies behind, keeps track of any connected users, and sends rendering data to the client.
The client receives the rendering data (maybe not exactly rendering data more so the terrain values itself), where the client
will do the rendering on the client's end.
