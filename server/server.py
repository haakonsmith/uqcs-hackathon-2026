import asyncio
import logging
import time
from dataclasses import dataclass
from typing import assert_never
from uuid import UUID, uuid4

import websockets
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

from protocol import (
    Acknowledged,
    BoardChanged,
    CancelPlan,
    ClientRequest,
    Echo,
    Echoed,
    EvtFrame,
    FinishPhase,
    GameOver,
    GameStarted,
    Join,
    Joined,
    JoinRejected,
    Judged,
    LobbyChanged,
    MovesResolved,
    MoveTroops,
    PlaceTroops,
    Planned,
    ReadySet,
    Refused,
    ReqFrame,
    ResFrame,
    RoundChanged,
    ServerEvent,
    ServerResponse,
    SetReady,
    SubmitSolution,
    dump_frame,
    parse_frame,
)
from protocol.lobby import Lobby, LobbyPlayer
from protocol.terrain import World, WorldMap
from server import combat, sandbox
from server.judge import Judge, PlaceholderJudge, SubprocessJudge
from server.player import Player
from server.rounds import Round
from server.world import generate

logger = logging.getLogger("Server")


# Loopback by default: a dev server that binds every interface the moment it
# is run is a surprise. Pass --host 0.0.0.0 to let the network in.
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8888

# The game will not start below this many players, however eager they are.
MIN_PLAYERS = 2

# How often the round clock checks whether the phase is over. Fine enough
# that a countdown looks right, coarse enough to cost nothing.
CLOCK_TICK = 0.5


def _short(value: object) -> str:
    """First block of a UUID. Enough to follow one player through a log."""
    return str(value)[:8]


def _label(player: Player) -> str:
    return f"{player.name}[{_short(player.id)}]"


@dataclass
class Server:
    connections: set[websockets.ServerConnection]
    players: dict[UUID, Player]
    sessions: dict[websockets.ServerConnection, UUID]
    board: WorldMap | None
    round: Round | None
    player_count: int

    def __init__(self, judge: Judge | None = None) -> None:
        self.connections: set[websockets.ServerConnection] = set()
        self.players: dict[UUID, Player] = {}
        # Which player a socket is logged in as, so a drop can be announced.
        self.sessions: dict[websockets.ServerConnection, UUID] = {}

        # No board until the lobby says go. Generating one up front would deal
        # territories to players who never turned up, and take the count of
        # them from a constant rather than from who is actually in the room.
        self.board: WorldMap | None = None
        self.round: Round | None = None
        self.clock: asyncio.Task[None] | None = None
        # Injected so a test can judge instantly, and so the sandboxed runner
        # can replace the placeholder without this file changing.
        self.judge: Judge = judge or PlaceholderJudge()
        self.player_count = 4

    @property
    def in_progress(self) -> bool:
        return self.round is not None

    def lobby(self) -> Lobby:
        """The roster as the clients see it. Insertion order is join order."""
        return Lobby(
            players=[LobbyPlayer(id=str(p.id), name=p.name, ready=p.ready) for p in self.players.values()],
            minimum=MIN_PLAYERS,
            capacity=self.player_count,
        )

    def roster(self) -> str:
        """The room in one line, for the log. `*` marks a player who is ready."""
        if not self.players:
            return "lobby empty"
        names = " ".join(f"{'*' if p.ready else ' '}{p.name}" for p in self.players.values())
        return f"lobby {len(self.players)}/{self.player_count}:{names}"

    async def handle_request(self, ws: websockets.ServerConnection, request: ClientRequest) -> ServerResponse:
        """Answer one request. Returning the response keeps correlation out of
        the game logic - the caller tags it with the id it came in on."""
        match request:
            case Join(name=name):
                if self.in_progress:
                    logger.info(f"turning away {name!r} from {_short(ws.id)}: a game is already running")
                    return JoinRejected(reason="a game is already in progress")

                player_id = uuid4()
                player = Player(player_id, name=name)
                self.players[player_id] = player
                self.sessions[ws] = player_id

                lobby = self.lobby()
                logger.info(f"{_label(player)} joined - {self.roster()}")
                # To everyone else, so the joiner is not told twice - it has
                # the same roster on its `Joined`.
                await self.broadcast(LobbyChanged(lobby=lobby), exclude=ws)
                return Joined(player_id=str(player_id), lobby=lobby)

            case SetReady(ready=ready):
                player_id = self.sessions.get(ws)
                player = self.players.get(player_id) if player_id is not None else None
                if player is None:
                    logger.warning(f"{_short(ws.id)} set ready without joining first")
                    # Readying up before joining. Nothing to record, but the
                    # roster is still a truthful answer.
                    return ReadySet(lobby=self.lobby())

                player.ready = ready
                lobby = self.lobby()
                logger.info(
                    f"{_label(player)} is {'ready' if ready else 'not ready'}"
                    f" - {lobby.ready_count}/{len(lobby.players)} ready, {lobby.waiting_for()}"
                )
                await self.broadcast(LobbyChanged(lobby=lobby), exclude=ws)
                # The board is pushed by `_answer` once this response is out,
                # so a client is never handed a game mid-request.
                return ReadySet(lobby=lobby)

            case SubmitSolution(code=code):
                player = self._player(ws)
                if player is None or self.round is None:
                    return Refused(reason="no game in progress")
                try:
                    verdict = await self.round.submit(player.id, code)
                except combat.IllegalMove as error:
                    return Refused(reason=str(error))

                logger.info(f"{_label(player)} submitted #{self.round.progress[player.id].submissions}: {verdict.summary()}")
                await self.broadcast(RoundChanged(round=self.round.state()))
                return Judged(verdict=verdict, round=self.round.state())

            case PlaceTroops(territory=territory, count=count):
                player = self._player(ws)
                if player is None or self.round is None:
                    return Refused(reason="no game in progress")
                try:
                    remaining = self.round.place(player.id, territory, count)
                except combat.IllegalMove as error:
                    return Refused(reason=str(error))

                logger.info(f"{_label(player)} promised {count} to territory {territory}, {remaining} left")
                # Nothing goes out to the room, not even the round state: a
                # placement is part of a plan until the phase ends, and the
                # troop count in that state would say how much of one is left.
                return self._planned(self.round, player.id)

            case MoveTroops(source=source, target=target, count=count):
                player = self._player(ws)
                if player is None or self.round is None:
                    return Refused(reason="no game in progress")
                try:
                    orders = self.round.order(player.id, source, target, count)
                except combat.IllegalMove as error:
                    return Refused(reason=str(error))

                logger.info(f"{_label(player)} ordered {count} from {source} to {target} ({len(orders)} orders)")
                # Deliberately not broadcast either: orders stay secret until
                # they land, or marching into somebody is never a surprise.
                return self._planned(self.round, player.id)

            case CancelPlan():
                player = self._player(ws)
                if player is None or self.round is None:
                    return Refused(reason="no game in progress")
                self.round.cancel_plan(player.id)
                logger.info(f"{_label(player)} tore up their plan")
                return self._planned(self.round, player.id)

            case FinishPhase():
                player = self._player(ws)
                if player is None or self.round is None:
                    return Refused(reason="no game in progress")
                self.round.finish(player.id)
                state = self.round.state()
                logger.info(f"{_label(player)} finished {state.phase} - waiting on {state.waiting_on()}")
                await self.broadcast(RoundChanged(round=state), exclude=ws)
                return Acknowledged(round=state)

            case Echo(text=text):
                logger.debug(f"echo from {_short(ws.id)}: {text!r}")
                return Echoed(text=text)

            case _:
                assert_never(request)

    async def start_if_ready(self) -> None:
        """Generate the board and push it, once everybody has readied up.

        Deals to exactly the players in the room, so a three-player game is a
        three-way split rather than a four-way one with an empty share.
        """
        if self.in_progress:
            return
        if not self.lobby().can_start:
            logger.debug(f"not starting yet - {self.lobby().waiting_for()}")
            return

        ids = list(self.players)
        logger.info(f"all {len(ids)} players ready - generating the board")
        started = time.perf_counter()
        self.board = generate(width=80, height=40, players=ids, player_count=len(ids))
        elapsed = (time.perf_counter() - started) * 1000
        logger.info(
            f"board ready in {elapsed:.0f} ms: {self.board.width}x{self.board.height},"
            f" {len(self.board.territories)} territories across {len(self.board.owners)} players"
        )

        self.round = Round(board=self.board, judge=self.judge)
        self.round.start({p.id: p.name for p in self.players.values()})

        # The board goes out once, in full; everything after it is a delta.
        await self.broadcast(GameStarted(world=World.from_map(self.board)))
        await self.broadcast(RoundChanged(round=self.round.state()))
        self.clock = asyncio.create_task(self._run_clock())

    async def _run_clock(self) -> None:
        """Move the round on when its phase runs out, or everyone is done.

        A poll rather than a timer per phase: a phase can also end early, and
        one loop that asks "is it over yet" handles both without a timer to
        cancel and reschedule on every move.
        """
        try:
            while self.round is not None:
                await asyncio.sleep(CLOCK_TICK)
                if self.round is not None and self.round.should_advance():
                    await self._advance()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the round clock stopped")

    async def _advance(self) -> None:
        """Close the phase that just ended and announce the next one."""
        if self.round is None:
            return

        winner_id = self.round.winner()
        if winner_id is not None:
            await self._finish_game(winner_id)
            return

        before = self.round.phase
        resolution = self.round.advance()
        if resolution is not None:
            # Board first, then the story: a client that draws the report
            # before the delta would describe a board it is not showing.
            await self.broadcast(BoardChanged(updates=self.round.updates(resolution.touched)))
            await self.broadcast(MovesResolved(battles=resolution.battles, dropped=resolution.dropped))

        state = self.round.state()
        logger.info(f"round {state.number}: {before} -> {state.phase} ({state.seconds_left:.0f}s)")
        await self.broadcast(RoundChanged(round=state))

    async def _finish_game(self, winner_id: UUID) -> None:
        winner = self.players.get(winner_id)
        name = winner.name if winner is not None else _short(winner_id)
        logger.info(f"{name} holds the whole board - game over")
        self.round = None
        await self.broadcast(GameOver(winner=str(winner_id), name=name))

    def _planned(self, round: Round, player_id: UUID) -> Planned:
        """One player's whole plan for the phase, as the answer to a move.

        Every move in a commanding phase answers with all of it rather than
        with what it changed, because placing and marching draw on the same
        troops: an answer naming only one of them leaves the client to work out
        the other, and the two would disagree the moment a move was refused.
        """
        return Planned(
            placements=round.placements_for(player_id),
            orders=round.orders_for(player_id),
            remaining=round.troops_left(player_id),
            round=round.state(),
        )

    def _player(self, ws: websockets.ServerConnection) -> Player | None:
        player_id = self.sessions.get(ws)
        return self.players.get(player_id) if player_id is not None else None

    async def broadcast(self, event: ServerEvent, exclude: websockets.ServerConnection | None = None) -> None:
        """Push an event to everyone, dropping those that disconnect mid-send."""
        raw = dump_frame(EvtFrame(body=event))
        targets = [ws for ws in self.connections if ws is not exclude]
        results = await asyncio.gather(*(ws.send(raw) for ws in targets), return_exceptions=True)

        logger.debug(f"{event.kind} -> {len(targets)} client(s), {len(raw)} bytes")
        # gather() with return_exceptions swallows these, and a push that
        # silently never lands is the worst kind of bug to chase.
        for ws, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(f"could not push {event.kind} to {_short(ws.id)}: {result!r}")

    async def register_connection(self, websocket: websockets.ServerConnection) -> None:
        who = _short(websocket.id)

        if len(self.connections) >= self.player_count:
            logger.warning(f"refusing {who}: {len(self.connections)} connections already, lobby is full")
            await websocket.close(code=1008, reason="lobby full")
            return

        self.connections.add(websocket)
        logger.info(f"{who} connected ({len(self.connections)}/{self.player_count} sockets)")

        async for message in websocket:
            try:
                frame = parse_frame(message)
            except ValueError as error:
                logger.error(f"unparseable frame from {who}: {error}")
                logger.debug(f"raw frame was {message!r}")
                continue

            match frame:
                case ReqFrame(id=request_id, body=body):
                    await self._answer(websocket, request_id, body)
                case _:
                    logger.warning(f"{who} sent a {frame.t} frame to the server")

    async def _answer(self, ws: websockets.ServerConnection, request_id: str, request: ClientRequest) -> None:
        who = _short(ws.id)
        logger.debug(f"{who} -> {request.action} {request_id[:8]}")
        try:
            response = await self.handle_request(ws, request)
        except Exception:
            logger.exception(f"{who}: handling {type(request).__name__} failed")
            return

        raw = dump_frame(ResFrame(id=request_id, body=response))
        logger.debug(f"{who} <- {response.kind} {request_id[:8]}, {len(raw)} bytes")
        await ws.send(raw)
        # Anything that follows from the request goes out after its answer, so
        # a client is never pushed a game it has not been told it joined.
        await self.start_if_ready()

    async def handler(self, websocket: websockets.ServerConnection) -> None:
        who = _short(websocket.id)
        try:
            await self.register_connection(websocket)
        except ConnectionClosedError:
            logger.warning(f"{who} dropped without closing cleanly")
        finally:
            self.connections.discard(websocket)
            player_id = self.sessions.pop(websocket, None)
            if player_id is None:
                logger.info(f"{who} disconnected before joining")
            else:
                gone = self.players.pop(player_id, None)
                name = _label(gone) if gone is not None else _short(player_id)
                logger.info(f"{name} left - {self.roster()}")
                if self.round is not None:
                    # Forget them in the round too, or every phase runs its
                    # full clock waiting on somebody who is not coming back.
                    self.round.drop(player_id)
                    logger.warning(f"{name} left a game in progress; their territories are now unplayed")
                await self.broadcast(LobbyChanged(lobby=self.lobby()))

    async def run(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        """Serve until cancelled. `host` is the interface to bind, not a peer."""
        async with serve(
            self.handler,
            host,
            port,
            ping_interval=None,
            max_size=64 * 1024 * 1024,
        ):
            logger.info(f"listening on ws://{host}:{port} - {MIN_PLAYERS} to {self.player_count} players")
            logger.info(f"judge: {type(self.judge).__name__}")
            if isinstance(self.judge, SubprocessJudge):
                # Said out loud at startup, because "sandboxed" means different
                # things on different hosts and the operator should know which.
                logger.info(sandbox.ISOLATION.summary())
                if not sandbox.ISOLATION.blocks_network:
                    logger.warning("no sandbox wrapper: submitted code can open sockets and write files")
                    logger.warning("install bubblewrap (dnf install bubblewrap) and restart to fix this")
                elif (failure := await sandbox.self_test()) is not None:
                    # Installed but not working is the worse of the two: it
                    # fails at the moment a player is waiting on a verdict,
                    # not here where somebody is watching.
                    logger.error(f"the {sandbox.ISOLATION.wrapper} sandbox is installed but did not run: {failure}")
                    logger.error("every submission will fail until this is fixed")
                else:
                    logger.info(f"{sandbox.ISOLATION.wrapper} self-test passed")
            if host in ("0.0.0.0", "::"):
                logger.warning(f"bound to {host}: reachable from the network, with no authentication")
            await asyncio.Future()
