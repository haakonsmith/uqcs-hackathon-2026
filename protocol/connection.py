"""Client side of the protocol: correlated requests plus an event stream."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Protocol, cast

from protocol.actions import ClientRequest, ServerEvent, ServerResponse
from protocol.core import ProtocolError, Request, response_types
from protocol.frames import EvtFrame, ReqFrame, ResFrame, dump_frame, parse_frame

logger = logging.getLogger("Connection")


class Socket(Protocol):
    """The slice of a websocket connection this needs.

    Structural, so it fits both websockets' client and server connection types
    without this module having to pick one.
    """

    async def send(self, message: str) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


class Connection:
    """Wraps a websocket, matching responses to the requests that asked for them."""

    def __init__(self, ws: Socket) -> None:
        self._ws = ws
        self._pending: dict[str, asyncio.Future[ServerResponse]] = {}
        self.events: asyncio.Queue[ServerEvent] = asyncio.Queue()

    async def send[R](self, request: Request[R], timeout: float = 10.0) -> R:
        """Send a request and wait for its answer.

        The return type follows from the request: `send(Join(...))` is typed
        `Joined | JoinRejected` with nothing to annotate at the call site.
        """
        # `Request[R]` is broader than the union the frame accepts: R carries
        # the response type, not the fact of membership. Serialising checks it
        # for real - the adapter rejects anything outside ClientRequest.
        body_out = cast(ClientRequest, request)

        request_id = str(uuid.uuid4())
        future: asyncio.Future[ServerResponse] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._ws.send(dump_frame(ReqFrame(id=request_id, body=body_out)))
            body = await asyncio.wait_for(future, timeout)
        finally:
            # Pop before validating, so a ProtocolError doesn't leak the entry.
            _ = self._pending.pop(request_id, None)

        # The future is typed over every response, so this narrows it to the
        # kinds this particular request declared. Nothing else checks that the
        # peer answered with one of them, which is what makes the cast honest.
        expected = response_types(type(request))
        if not isinstance(body, expected):
            names = "/".join(t.__name__ for t in expected)
            raise ProtocolError(f"{type(request).__name__} expects {names}, got {type(body).__name__}")
        return cast(R, body)

    async def run(self) -> None:
        """Read frames until the socket closes, dispatching each to its waiter."""
        try:
            async for raw in self._ws:
                self._handle(raw)
        finally:
            # Without this every in-flight request sits until its own timeout,
            # on a socket already known to be dead.
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("connection closed"))
            self._pending.clear()

    def _handle(self, raw: str | bytes) -> None:
        match parse_frame(raw):
            case ResFrame(id=request_id, body=body):
                future = self._pending.get(request_id)
                if future is None:
                    # Late arrival after a timeout, or an id we never sent.
                    logger.warning("response for unknown request %s", request_id)
                elif not future.done():
                    future.set_result(body)
            case EvtFrame(body=body):
                self.events.put_nowait(body)
            case ReqFrame(id=request_id):
                # A client receiving a request means the peer is confused;
                # staying silent would make that unfindable.
                logger.warning("ignoring request frame %s sent to a client", request_id)
