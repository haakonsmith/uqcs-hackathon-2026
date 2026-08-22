"""Everything the screen is drawn from, and the ways it changes.

Input and (later) the network both mutate this one object and set `dirty`, so
a server event repaints exactly the way a keypress does. While these lived as
locals in the render loop, only a keypress could cause a redraw.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field, replace
from uuid import UUID

import blessed
from blessed.keyboard import Keystroke

from client import hud
from client import screen as screen_module
from client.editor import Draft, NoEditor, edit
from client.input import SCROLL_KEYS, KeyPump, wheel_delta
from client.palette import Factions
from client.render import Highlight, draw_board, draw_garrisons, draw_legend, draw_sea_routes
from client.screen import Screen
from client.viewport import Viewport
from protocol import (
    Acknowledged,
    BoardChanged,
    CancelPlan,
    ClientRequest,
    Connection,
    FinishPhase,
    GameOver,
    Judged,
    MoveOrder,
    MovesResolved,
    MoveTroops,
    PlaceTroops,
    Planned,
    ProtocolError,
    Refused,
    RoundChanged,
    RoundState,
    ServerEvent,
    ServerResponse,
    SubmitSolution,
    Verdict,
    terrain,
    troops_to_send,
)

logger = logging.getLogger("client")


def _starter(round_state: RoundState | None) -> str:
    """What a fresh draft opens on: the problem, so it is there while typing."""
    if round_state is None or round_state.problem is None:
        return ""

    problem = round_state.problem
    lines = [f"# {problem.title}", "#", *(f"# {line}" for line in _wrap(problem.statement)), f"# {problem.signature}"]
    for given, wanted in problem.examples:
        lines.append(f"#   {given!r} -> {wanted!r}")
    lines += [
        "#",
        "# Read stdin, write stdout. Your code runs on the server against",
        "# hidden test cases, in a sandbox with no network and a few seconds",
        "# of CPU. Only whether each case matched comes back, never what was",
        "# expected.",
        "",
    ]
    return "\n".join(lines)


def _wrap(text: str, width: int = 68) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


# Status bars under the board: the round, then the hover text and keys.
HUD_ROWS = 2


@dataclass
class App:
    term: blessed.Terminal
    world: terrain.WorldMap
    factions: Factions
    view: Viewport = field(default_factory=Viewport)
    bold_borders: bool = False
    legend: bool = True
    cursor: tuple[int, int] | None = None
    hover: str = "move the mouse over the map"
    highlights: dict[int, Highlight] = field(default_factory=dict)
    # Something changed and the frame should be composed again. Purely a CPU
    # guard now: composing costs about 3 ms and the diff decides what actually
    # reaches the terminal, so setting this when nothing changed wastes work
    # but cannot put anything wrong on screen. That is the opposite of the
    # per-component invalidation it replaced, where being wrong meant a stale
    # board under a closed popup.
    dirty: bool = True
    # The frame currently on the terminal, to diff the next one against.
    _shown: Screen | None = None
    running: bool = True

    # Everything below is the round; without a connection the board is just a
    # map to look at, which is what `viewer.py` wants.
    conn: Connection | None = None
    pump: KeyPump | None = None
    me: str = ""
    round: RoundState | None = None
    # Scratch file the player's solution is written in, kept for the whole
    # session so a second attempt starts from the first.
    draft: Draft | None = None
    # Last verdict, refusal or attack result, shown on the phase line.
    message: str = ""
    # Territory picked with the keyboard. Takes precedence over the mouse,
    # so the game is playable on a terminal with no mouse reporting.
    selected: int | None = None
    # Source territory chosen for a march, waiting for a destination.
    move_from: int | None = None
    # Something worth saying that the next plan summary would otherwise
    # overwrite - a march clamped to fewer troops than were asked for, say.
    # Held rather than said, because the summary lands after the round trip.
    note: str | None = None
    scoreboard: bool = False
    # A result the player has to acknowledge. Any key dismisses it, and
    # while it is up nothing else reads the keyboard - a verdict that
    # scrolls past in a status bar is a verdict nobody saw.
    popup: list[str] | None = None
    # When a held popup may be dismissed, on the monotonic clock. Panels that
    # arrive unasked - the reveal, the winner - are held for a few seconds so
    # they cannot be closed by a key already on its way down.
    # The last panel shown, so `[v]` can put it back after a dismissal.
    last_popup: list[str] | None = None
    # Troops the next place or march will move, set with the number keys.
    move_count: int = 1
    # The last verdict, kept so [v] can bring it back. Dismissing a popup
    # should not be the only chance to read which cases failed.
    last_verdict: Verdict | None = None
    # The plan for this phase, as the server last confirmed it: troops promised
    # to each territory, and the marches ordered out of them. Kept rather than
    # counted locally because a move can be refused, and because both have to
    # be known to say what a territory still has to send. Drawn as the `(+n)`
    # beside a garrison, and emptied when the phase carries it out.
    placements: dict[int, int] = field(default_factory=dict)
    orders: list[MoveOrder] = field(default_factory=list)

    def tick(self) -> None:
        """A second passed: only the countdown on the phase bar changed."""
        if self.round is not None:
            self.round = replace(self.round, seconds_left=max(0.0, self.round.seconds_left - 1.0))
            self.dirty = True

    def invalidate(self) -> None:
        """Forget what is on screen, so the next frame is written in full.

        For when something other than `draw` has written to the terminal - an
        editor taking it over, say. The diff is against what this believes is
        displayed, and once a stranger has drawn over that, every cell whose
        stale model happens to match is skipped and the leftovers stay put.
        A resize needs no such call: the frame sizes disagree, which `render`
        already treats as nothing in common.
        """
        self._shown = None
        self.dirty = True

    def fit(self) -> None:
        """Size the viewport to the terminal and refresh what follows from it."""
        self.view.fit(self.term.width, self.term.height, self.world, reserved=HUD_ROWS)
        self.refresh_hover()
        self.dirty = True

    def _selection_text(self, fallback: str = "") -> str:
        """What the hover slot says when the mouse is not over the board."""
        if self.selected is not None:
            held = self.world.territories[self.selected]
            return f"picked {self.selected} ({held.soldiers} soldiers{self._placed_suffix(self.selected)})"
        return fallback or "[n] to pick a territory, or use the mouse"

    def _outbound(self) -> dict[int, int]:
        """Troops ordered out of each territory this phase, summed per source."""
        totals: dict[int, int] = {}
        for order in self.orders:
            totals[order.source] = totals.get(order.source, 0) + order.count
        return totals

    def _placed_suffix(self, territory_id: int) -> str:
        """What this phase's plan has promised here: troops in, troops out."""
        parts: list[str] = []
        placed = self.placements.get(territory_id, 0)
        if placed:
            parts.append(f"+{placed} placed")
        marching = self._outbound().get(territory_id, 0)
        if marching:
            parts.append(f"-{marching} marching out")
        return f", {', '.join(parts)}" if parts else ""

    def refresh_hover(self) -> None:
        """Recompute what the cursor points at.

        Called after scrolling and zooming too, not just on mouse motion: the
        board moves under a stationary mouse, so what it points at changes.
        """
        self.highlights = {}
        if self.move_from is not None:
            # Every neighbour lit faintly, the source lit strongly. Which
            # territories border which is not something a player can read off
            # a terrain map, and guessing it by clicking costs a click each
            # time to be told it does not border.
            for neighbour in self.world.territories[self.move_from].neighbours:
                self.highlights[neighbour] = (120, 90, 60)
            self.highlights[self.move_from] = (235, 90, 70)
        if self.selected is not None:
            self.highlights[self.selected] = (245, 245, 245)

        if self.cursor is None:
            self.hover = self._selection_text()
            self.dirty = True
            return

        cell = self.view.cell_at(self.world, *self.cursor)
        if cell is None:
            self.hover = self._selection_text("off the board")
            self.dirty = True
            return

        where = f"({cell.x},{cell.y}) {cell.terrain.label} height {cell.height:.2f}"
        if cell.territory is None:
            self.hover = f"{where} - unclaimed"
            self.dirty = True
            return

        owner = self.world.territories[cell.territory]
        side = self.factions.side_of.get(owner.id)
        held = self.factions.label(side) if side is not None else "nobody"
        soldiers = f"{owner.soldiers} soldiers{self._placed_suffix(owner.id)}"
        # Who holds it and how strongly. The neighbour count was here too and
        # answered a question nobody asks while deciding a move.
        self.hover = f"{where} - {held}, {soldiers}{self._order_preview(owner.id)}"
        if self.move_from is None:
            self.highlights[owner.id] = True
        elif owner.id in self.world.territories[self.move_from].neighbours:
            # Brighter than the rest of the frontier rather than the ownership
            # highlight, which would paint over the one thing being said here:
            # that this is somewhere the march may go.
            self.highlights[owner.id] = (215, 160, 90)
        self.dirty = True

    def _order_preview(self, territory_id: int) -> str:
        """What clicking here would order, for a player part-way through one.

        The count a march sends is `[1-9]` clamped to what the source has left,
        which is two numbers the player is otherwise expected to hold in their
        head and multiply out before committing.
        """
        if self.move_from is None or territory_id == self.move_from:
            return ""
        source = self.world.territories[self.move_from]
        if territory_id not in source.neighbours:
            return f" - does not border {source.id}"
        count = min(self.move_count, self._sendable(source.id))
        if count < 1:
            return f" - {source.id} has nothing left to send"
        verb = "reinforce with" if self.world.territories[territory_id].owner == self._my_uuid() else "assault with"
        return f" - {verb} {count} from {source.id}"

    async def handle_key(self, key: Keystroke) -> None:
        """Act on a keystroke. Async, because most of them are requests."""
        moved = False

        if self.popup is not None:
            # No mouse input closes a panel, clicks included. Motion arrives
            # here as a keystroke and a hand resting on the desk produces a
            # stream of it, and a click aimed at the board underneath is not a
            # decision to dismiss what is covering it. A panel goes away when
            # a player presses a key, and stays until then.
            if (key.name or "").startswith("MOUSE_"):
                return
            # The panel was drawn over the terrain, so the terrain under it
            # has to be drawn again to erase it.
            self.popup = None
            self.dirty = True
            return

        if key.lower() == "q":
            self.running = False
            return

        if key.name == "KEY_TAB" or key == "\t":
            self.scoreboard = not self.scoreboard
            self.dirty = True
            return
        if key.name == "KEY_ESCAPE":
            self.move_from = None
            self.scoreboard = False
            self.dirty = True
            return
        if key in ("n", "N"):
            self._cycle(1 if key == "n" else -1)
            return
        if key == "v":
            self._show_verdict()
            return
        if key in ("?", "/"):
            # `/` too: `?` is shift+/ and getting the shift wrong should still
            # open the help rather than do nothing.
            self._show_popup(hud.help_popup(self.round, self.term.height))
            return
        if await self._handle_phase_key(key):
            return

        if key == "o":
            self.bold_borders = not self.bold_borders
            self.dirty = True
        elif key == "l":
            self.legend = not self.legend
            self.dirty = True
        elif key in "+=":
            moved = self.view.set_zoom(self.view.zoom - 1, self.world)
        elif key in "-_":
            moved = self.view.set_zoom(self.view.zoom + 1, self.world)
        elif key.name in SCROLL_KEYS:
            # Step by one screen cell, so scrolling feels the same at any zoom.
            dx, dy = SCROLL_KEYS[key.name]
            moved = self.view.scroll(dx * self.view.zoom, dy * self.view.zoom, self.world)
        elif key.name and key.name.startswith("MOUSE_"):
            moved = self._handle_mouse(key)
            # Motion and the wheel are handled above; a press is a move in the
            # game and has to go to the server, which the sync path cannot do.
            if key.name == "MOUSE_LEFT":
                await self._click()

        if moved:
            self.refresh_hover()
            self.dirty = True

    async def _handle_phase_key(self, key: Keystroke) -> bool:
        """Handle the keys that mean something this phase. True if consumed."""
        if self.conn is None or self.round is None:
            return False

        phase = self.round.phase
        if key == "f":
            await self._request(FinishPhase())
            return True

        if phase == "submitting" and key == "s":
            await self._submit()
            return True
        if phase == "commanding":
            # One count for both halves of the phase: placing and marching move
            # troops a handful at a time and the bar offers [1-9] for either.
            if key.isdigit() and key != "0":
                self.move_count = int(key)
                self._say(f"next place or march moves {self.move_count}")
                return True
            if key in (" ", "p"):
                await self._place(None if key == "p" else self.move_count)
                return True
            if key == "m":
                await self._order()
                return True
            if key == "c":
                await self._request(CancelPlan())
                return True
        return False

    def _show_verdict(self) -> None:
        """Put the last panel back, for a player who closed it too quickly.

        Whatever was last shown, not only a verdict: a battle report arrives
        unasked the instant a phase ends, which makes it the one most likely
        to be dismissed by a keystroke meant for something else.

        Verdicts are rebuilt rather than replayed, so the attempt count and
        best-so-far line are current rather than whatever they said when the
        panel first appeared.
        """
        if self.last_verdict is not None:
            self._show_popup(hud.verdict_popup(self.last_verdict, self.round, self.me))
            return
        if self.last_popup is not None:
            self._show_popup(self.last_popup)
            return
        self._say("nothing to show again yet")

    async def _submit(self) -> None:
        """Open the player's editor, then submit whatever they saved."""
        if self.pump is None:
            self._say("no editor available in this session")
            return
        if self.draft is None:
            self.draft = Draft(initial=_starter(self.round))

        try:
            code = await edit(self.term, self.pump, self.draft)
        except NoEditor as error:
            self._say(str(error))
            return
        except OSError as error:
            self._say(f"could not start your editor: {error}")
            return
        finally:
            # The editor owned the terminal and drew whatever it liked, so
            # what this believes is on screen is now fiction. It also may have
            # been resized while the board was not being drawn.
            self.invalidate()
            self.fit()

        if code is None:
            self._say("nothing submitted - the draft was never saved")
            return
        if not code.strip():
            self._say("nothing submitted - the draft was empty")
            return

        self._say(f"submitting {len(code)} bytes ...")
        await self._request(SubmitSolution(code=code))

    async def _place(self, count: int | None) -> None:
        """Promise troops to a territory. `count` of None means all in hand.

        Nothing lands on the board here either: the answer comes back as a
        `(+n)` on the territory, and the troops arrive when the phase ends -
        joining the garrison on your own ground, or assaulting it next door.
        """
        territory = self._target_territory()
        if territory is None:
            self._say("click a territory, or pick one with [n]")
            return
        if not self._in_reach(territory):
            self._say("place on your own ground, or on ground touching it")
            return

        mine = self.round.player(self.me) if self.round else None
        held = mine.troops if mine is not None else 0
        wanted = held if count is None else min(count, held)
        if wanted < 1:
            self._say("no troops left to place - [m] marches what is already there")
            return
        await self._request(PlaceTroops(territory=territory, count=wanted))

    async def _order(self) -> None:
        """Pick a source, then a neighbour, and queue a march between them.

        Every refusal here keeps the source picked where it can. Half an order
        is a state the player built with two deliberate actions, and throwing
        it away over one bad click means building it again from the start.
        """
        territory = self._target_territory()
        if territory is None:
            self._say("click a territory, or pick one with [n]")
            return

        if self.move_from is None:
            if self.world.territories[territory].owner != self._my_uuid():
                self._say("orders start from one of your own territories")
                return
            spare = self._sendable(territory)
            if spare < 1:
                self._say(f"territory {territory} has nothing left to send")
                return
            self.move_from = territory
            self.selected = None
            self._say(f"from {territory}: {spare} to send - click a neighbour, [n] to cycle them, [esc] to give up")
            return

        # Picking the source a second time is changing your mind about it.
        # Without this it is an order from a territory to itself, refused for
        # not bordering itself, which reads as the click having missed.
        if territory == self.move_from:
            self.move_from = None
            self._say("march called off - nothing was ordered")
            return

        source = self.world.territories[self.move_from]
        if territory not in source.neighbours:
            self._say(f"{territory} does not border {source.id} - pick a neighbour, or [esc] to give up")
            return

        spare = self._sendable(source.id)
        if spare < 1:
            self._say(f"territory {source.id} has nothing left to send")
            self.move_from = None
            return

        count = min(self.move_count, spare)
        if count < self.move_count:
            # Quietly sending fewer troops than were asked for is the kind of
            # thing a player finds out about when the phase resolves.
            self.note = f"{source.id} had only {spare} left - marching {count}, not {self.move_count}"

        response = await self._request(MoveTroops(source=source.id, target=territory, count=count))
        if isinstance(response, Refused):
            # The source is still fine - whatever the server disagreed with was
            # about this destination - so leave it picked and let them retry.
            self.note = None
            return
        self.move_from = None

    def _sendable(self, territory_id: int) -> int:
        """Troops a territory can still be ordered to march out.

        Literally the sum the server checks the order against - the same
        function, not a copy of it - so a march this clamps to is a march the
        server accepts.
        """
        return troops_to_send(
            garrison=self.world.territories[territory_id].soldiers,
            placed=self.placements.get(territory_id, 0),
            orders=self.orders,
            source=territory_id,
        )

    async def _click(self) -> None:
        """Left click: reinforce here, or finish a march that has a source.

        One phase does both, so a click has to mean one of them. It reinforces,
        which is the commoner move and the one a click meant before the phases
        were merged; a march is started with [m] and only then does the next
        click name its destination. Two clicks that mean different things
        depending on a source picked three keys ago is worse than one key.
        """
        if self.round is None or self.conn is None:
            return

        under = self._hovered_territory()
        if under is None:
            self._say("nothing there")
            return

        # `_place` and `_order` read the keyboard pick first, so pointing them
        # at what was clicked is a matter of setting it.
        self.selected = under
        if self.round.phase != "commanding":
            self.dirty = True
        elif self.move_from is not None:
            await self._order()
        else:
            await self._place(self.move_count)

    def _hovered_territory(self) -> int | None:
        """The territory under the mouse, ignoring any keyboard pick."""
        if self.cursor is None:
            return None
        cell = self.view.cell_at(self.world, *self.cursor)
        return cell.territory if cell is not None else None

    def _target_territory(self) -> int | None:
        """What a phase key acts on: the keyboard pick, else what is hovered."""
        if self.selected is not None:
            return self.selected
        if self.cursor is None:
            return None
        cell = self.view.cell_at(self.world, *self.cursor)
        return cell.territory if cell is not None else None

    def _selectable(self) -> list[int]:
        """What `n` cycles through, which depends on what you are doing.

        Mid-order that is every neighbour of the source, since every one of
        them is a legal destination: marching onto your own ground is how a
        frontier gets reinforced, and leaving those out of the cycle hid half
        the moves available. Ground held by somebody else comes first, because
        attacking is the commoner reason to be part-way through an order.

        Otherwise it is everywhere troops may go - your own ground, then the
        ground touching it - with your own first, because a march can only
        start there.
        """
        mine = self._my_uuid()
        if self.round is not None and self.round.phase == "commanding" and self.move_from is not None:
            source = self.world.territories[self.move_from]
            theirs = sorted(n for n in source.neighbours if self.world.territories[n].owner != mine)
            ours = sorted(n for n in source.neighbours if self.world.territories[n].owner == mine)
            return [*theirs, *ours]

        held = sorted(t.id for t in self.world.territories if t.owner == mine)
        touching = {n for t in held for n in self.world.territories[t].neighbours}
        return [*held, *sorted(touching.difference(held))]

    def _in_reach(self, territory_id: int) -> bool:
        """Whether troops may be placed here: yours, or bordering yours.

        The server decides, but it is the same rule either way and a refusal
        that arrives after a round trip reads as the click having missed.
        """
        mine = self._my_uuid()
        if mine is None:
            return False
        target = self.world.territories[territory_id]
        return target.owner == mine or any(self.world.territories[n].owner == mine for n in target.neighbours)

    def _cycle(self, step: int) -> None:
        """Step through the selectable territories, scrolling each into view."""
        options = self._selectable()
        if not options:
            self._say("nothing to select")
            return

        if self.selected in options:
            index = (options.index(self.selected) + step) % len(options)
        else:
            index = 0
        self.selected = options[index]
        self._centre_on(self.selected)
        held = self.world.territories[self.selected]
        # Whose it is, because the list now runs past your own ground into
        # what borders it, and placing on those two means different things.
        whose = " - theirs" if self.round is not None and held.owner != self._my_uuid() else ""
        self._say(f"territory {self.selected} ({held.soldiers} soldiers{whose}) - {index + 1}/{len(options)}")

    def _centre_on(self, territory_id: int) -> None:
        """Put a territory in the middle of the screen, so a pick is visible."""
        centre_x, centre_y = self.world.territories[territory_id].centroid
        target_x = int(centre_x) - self.view.span_x // 2
        target_y = int(centre_y) - self.view.span_y // 2
        _ = self.view.scroll(target_x - self.view.x, target_y - self.view.y, self.world)
        self.refresh_hover()
        self.dirty = True

    def _forget_plan(self) -> None:
        """Drop the pending plan, once it has been carried out or overtaken."""
        self.placements = {}
        self.orders = []

    def _my_uuid(self) -> UUID | None:
        try:
            return UUID(self.me)
        except ValueError:
            return None

    async def _request(self, request: ClientRequest) -> ServerResponse | None:
        """Send, and turn whatever comes back into a line on the phase bar.

        Returns the response so a caller can tell a refusal from an acceptance.
        Most do not care - the bar already says what happened - but an order
        half-made needs to know whether to keep the source it picked.
        """
        if self.conn is None:
            return None
        try:
            response = await self.conn.send(request)
        except TimeoutError:
            # Not fatal. The socket is fine and the server is most likely still
            # working - a judge with every slot busy answers late rather than
            # never. Quitting the game over it, which is what treating this
            # like a dropped connection did, loses a player their board over a
            # busy moment they had no part in causing.
            self._say("no answer yet - the server is busy. Nothing was lost; try again")
            return None
        except (ConnectionError, ProtocolError) as error:
            self._say(f"lost the server: {error}")
            self.running = False
            return None

        match response:
            case Judged(verdict=verdict, round=round_state):
                self.round = round_state
                self.last_verdict = verdict
                self._say(verdict.summary())
                self._show_popup(hud.verdict_popup(verdict, round_state, self.me))
            case Planned(placements=placements, orders=orders, remaining=remaining, round=round_state):
                self.round = round_state
                self.placements = {p.territory: p.count for p in placements}
                self.orders = list(orders)
                self._say(self._plan_summary(remaining))
            case Acknowledged(round=round_state):
                self.round = round_state
                self._say("waiting for the others")
            case Refused(reason=reason):
                self._say(reason)
            case _:
                self._say(f"unexpected {type(response).__name__}")
        return response

    def _plan_summary(self, remaining: int) -> str:
        """The whole plan in one line, for the phase bar.

        Every move in this phase says the same thing about all of it, rather
        than each reporting what it did: the interesting number after placing
        is usually how many troops are left, and after ordering a march it is
        usually how much is now committed to it.
        """
        parts: list[str] = []
        placed = sum(self.placements.values())
        if placed:
            parts.append(f"{placed} placed")
        if self.orders:
            marching = sum(order.count for order in self.orders)
            parts.append(f"{len(self.orders)} order{'s' if len(self.orders) > 1 else ''}, {marching} marching")
        parts.append(f"{remaining} in hand" if remaining else "nothing left in hand")
        summary = "  ".join(parts)
        if self.note is not None:
            summary, self.note = f"{self.note}  -  {summary}", None
        return summary

    def _say(self, message: str) -> None:
        self.message = message
        self.dirty = True

    def _show_popup(self, lines: list[str]) -> None:
        """Put a panel up, and remember it so `[v]` can bring it back.

        Panels wait rather than expire. A result that vanishes on a timer is a
        result somebody looked away from and lost, and the ones that appear
        unasked - a battle report the moment a phase ends - are exactly the
        ones worth reading.
        """
        self.popup = lines
        self.last_popup = lines
        self.dirty = True

    def _handle_mouse(self, key: Keystroke) -> bool:
        # Keystroke reports mouse position as mouse_xy, not .x / .y, and gives
        # (-1, -1) for anything that isn't a mouse event.
        mx, my = key.mouse_xy
        if (mx, my) != (-1, -1):
            self.cursor = (mx, my)

        delta = wheel_delta(key.name or "")
        if delta is not None:
            zoom = self.view.zoom
            # Scrolling moves the terrain, so the caller's `moved` handling
            # the view, which the caller turns into a redraw.
            return self.view.scroll(delta[0] * zoom, delta[1] * zoom, self.world)

        if (mx, my) != (-1, -1):
            self.refresh_hover()
            self.dirty = True
        return False

    def draw(self) -> None:
        """Compose the whole frame, then write only what differs from the last.

        Everything goes into one buffer - terrain, garrison counts, legend,
        both bars, any popup - and the diff decides what reaches the terminal.
        Nothing has to declare what it invalidated, because a composed frame
        cannot disagree with itself: a popup that covered the board is simply
        absent from the next frame, and those cells come back on their own.
        """
        term, view = self.term, self.view
        frame = Screen(term.width, term.height)

        draw_board(frame, self.world, view, self.bold_borders, self.highlights, self.factions)
        # Over the water, under everything else: a lane is part of the map.
        draw_sea_routes(frame, self.world, view, self.factions)
        # Garrisons before the legend: the legend is a solid box and should
        # cover a count that lands under it, rather than have digits printed
        # across its rows.
        draw_garrisons(frame, self.world, view, self.factions, self.placements, self._outbound())
        if self.legend:
            draw_legend(frame, view, self.factions)

        # Two bars: what the round is doing, and what the mouse is pointing at
        # plus the keys that work right now.
        bars = [
            hud.phase_line(self.round, self.me, self.message),
            f" {self.hover}" + hud.key_line(self.round, self.move_from),
        ]
        for offset, text in enumerate(bars):
            frame.row(view.drawn_rows + offset, text)

        if self.popup is not None:
            hud.draw_panel(frame, self.popup)
        elif self.scoreboard:
            hud.draw_panel(frame, hud.scoreboard(self.round, self.me))

        _ = sys.stdout.write(screen_module.render(term, self._shown, frame))
        _ = sys.stdout.flush()
        self._shown = frame
        self.dirty = False

    def apply_event(self, event: ServerEvent) -> None:
        """Fold a server push into the board. Repaints the same way a key does."""
        match event:
            case RoundChanged(round=round_state):
                if self.round is None or round_state.number != self.round.number:
                    # A new round is a new problem: last round's verdict is no
                    # longer "the last one" and [v] must not resurrect it.
                    self.last_verdict = None
                    # And the draft still holds the last problem's statement
                    # and the answer to it, so [s] would open the wrong
                    # instructions over a solution to something else.
                    if self.draft is not None:
                        self.draft.reset(_starter(round_state))
                if self.round is None or round_state.phase != self.round.phase:
                    # A new phase clears whatever the last one was saying, so
                    # a stale verdict cannot sit over the new instructions.
                    self.message = ""
                    self.move_from = None
                    self._forget_plan()
                self.round = round_state
                self.dirty = True
            case BoardChanged(updates=updates):
                for update in updates:
                    territory = self.world.territories[update.id]
                    territory.owner = UUID(update.owner) if update.owner else None
                    territory.soldiers = update.soldiers
                # Ground that changed hands has to change colour with it, or
                # the board goes on drawing a territory as its old holder's.
                self.factions = self.factions.updated(self.world)
                # The only delta the server sends is the one a phase resolving
                # produces, and it already counts in everything that was
                # promised. Keeping the `(+n)` would show those troops twice.
                self._forget_plan()
                self.refresh_hover()
                self.dirty = True
            case MovesResolved(battles=battles, dropped=dropped):
                # Newer than any verdict from this round, so [v] should
                # bring this back rather than the submission before it.
                self.last_verdict = None
                self._show_popup(hud.battles_popup(battles, dropped))
                self._say(f"{len(battles)} battles" if battles else "orders carried out")
            case GameOver(name=name, winner=winner):
                self._show_popup(hud.game_over_popup(name, winner))
                ending = f"{name} has taken the whole board" if winner is not None else "nobody is left holding ground"
                self._say(f"{ending} - game over")
            case _:
                logger.debug("unhandled server event %r", event)
