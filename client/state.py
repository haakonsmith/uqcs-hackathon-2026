"""Everything the screen is drawn from, and the ways it changes.

Input and (later) the network both mutate this one object and set `dirty`, so
a server event repaints exactly the way a keypress does. While these lived as
locals in the render loop, only a keypress could cause a redraw.
"""

from __future__ import annotations

import logging
import sys
import time
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
from client.sound import beep
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
    MovesResolved,
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


# Phase, hover and keys, a row each. Sharing a row cuts one of them off below
# about 120 columns: the key line alone fills most of an 80-column row.
HUD_ROWS = 3


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
    # Purely a CPU guard: composing costs about 3 ms and the diff decides what
    # reaches the terminal, so setting this when nothing changed wastes work
    # but cannot put anything wrong on screen.
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
    scoreboard: bool = False
    # A result the player has to acknowledge. Any key dismisses it, and
    # while it is up nothing else reads the keyboard - a verdict that
    # scrolls past in a status bar is a verdict nobody saw.
    popup: list[str] | None = None
    # When a held popup may be dismissed, on the monotonic clock. Panels that
    # arrive unasked cannot be closed by a key already on its way down.
    popup_hold_until: float = 0.0
    # The last panel shown, so `[v]` can put it back after a dismissal.
    last_popup: list[str] | None = None
    # Troops the next placement will promise, set with the number keys.
    move_count: int = 1
    # The last verdict, kept so [v] can bring it back. Dismissing a popup
    # should not be the only chance to read which cases failed.
    last_verdict: Verdict | None = None
    # The plan for this phase, as the server last confirmed it: troops promised
    # to each territory. Kept rather than counted locally because a placement
    # can be refused. Drawn as the `(+n)` beside a garrison, and emptied when
    # the phase carries it out.
    placements: dict[int, int] = field(default_factory=dict)
    # What the last resolution did, by territory, lit on the board until the
    # next commanding phase opens. The note travels with the mark so a lit
    # territory can say what happened to it.
    battle_notes: dict[int, str] = field(default_factory=dict)

    def tick(self) -> None:
        """A second passed: the phase bar's countdown, and a held panel's."""
        if self.round is not None:
            self.round = replace(self.round, seconds_left=max(0.0, self.round.seconds_left - 1.0))
            self.dirty = True
        if self.popup is not None and self.popup_hold_until > time.monotonic():
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
            return f"picked {self._territory_text(self.selected)}"
        return fallback or "[n] to pick a territory, or use the mouse"

    def _territory_text(self, territory_id: int) -> str:
        """One territory, said the way a player thinks about it.

        Who holds it, how strongly, what this phase's plan adds, and what the
        last one did to it. Deliberately nothing about the tile under the
        cursor: terrain and height decide how the board is generated and
        nothing about how it is played, so a cell's label and elevation were
        three quarters of this line and no part of any decision made from it.
        """
        territory = self.world.territories[territory_id]
        side = self.factions.side_of.get(territory_id)
        held = self.factions.label(side) if side is not None else "nobody"
        soldiers = f"{territory.soldiers} soldiers{self._placed_suffix(territory_id)}"
        return f"territory {territory_id} - {held}, {soldiers}{self._mark_suffix(territory_id)}"

    def _mark_suffix(self, territory_id: int) -> str:
        """` - you took it from Guus`, for ground the last phase fought over."""
        note = self.battle_notes.get(territory_id)
        return f" - {note}" if note else ""

    def _placed_suffix(self, territory_id: int) -> str:
        """`, +3 placed` for ground with troops promised to it, else nothing."""
        placed = self.placements.get(territory_id, 0)
        return f", +{placed} placed" if placed else ""

    def refresh_hover(self) -> None:
        """Recompute what the cursor points at.

        Called after scrolling and zooming too, not just on mouse motion: the
        board moves under a stationary mouse, so what it points at changes.
        """
        self.highlights = {}
        # Under the pick rather than over it: the pick is a thing the player is
        # doing now, and a mark is a thing that already happened.
        for territory_id in self.battle_notes:
            self.highlights[territory_id] = (200, 110, 45)
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

        if cell.territory is None:
            # Open water, or ground no territory was cut from. Nothing can be
            # done with it, so there is nothing to say about it.
            self.hover = self._selection_text("nothing here")
            self.dirty = True
            return

        self.hover = self._territory_text(cell.territory)
        self.highlights[cell.territory] = True
        self.dirty = True

    async def handle_key(self, key: Keystroke) -> None:
        """Act on a keystroke. Async, because most of them are requests."""
        moved = False

        if self.popup is not None:
            # No mouse input closes a panel, clicks included: motion arrives
            # as a keystroke, and a hand resting on the desk produces a stream
            # of it.
            if (key.name or "").startswith("MOUSE_"):
                return
            if time.monotonic() < self.popup_hold_until:
                # Swallowed rather than queued: the key was aimed at whatever
                # was on screen before this appeared.
                return
            # The panel was drawn over the terrain, so the terrain under it
            # has to be drawn again to erase it.
            self.popup = None
            self.popup_hold_until = 0.0
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
            # The one way back to no pick at all: cycling with [n] only ever
            # moves the pick somewhere else.
            if self.selected is not None:
                self.selected = None
                self._say("nothing picked")
            self.scoreboard = False
            self.refresh_hover()
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
            # Troops go down a handful at a time, so the bar offers [1-9].
            if key.isdigit() and key != "0":
                self.move_count = int(key)
                self._say(f"next placement puts down {self.move_count}")
                return True
            if key in (" ", "p"):
                await self._place(self._target_territory(), None if key == "p" else self.move_count)
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

    async def _place(self, territory: int | None, count: int | None) -> None:
        """Promise troops to a territory. `count` of None means all in hand.

        Told which territory rather than working it out, so a caller that
        already knows - a click does - does not have to route the answer
        through the keyboard pick to get it here.

        Nothing lands on the board either: the answer comes back as a `(+n)` on
        the territory, and the troops arrive when the phase ends - joining the
        garrison on your own ground, or assaulting it next door.
        """
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
            self._say("no troops left to place - [f] when you are finished")
            return
        await self._request(PlaceTroops(territory=territory, count=wanted))

    async def _click(self) -> None:
        """Left click: put troops on the territory under the cursor."""
        if self.round is None or self.conn is None:
            return

        under = self._hovered_territory()
        if under is None:
            self._say("nothing there")
            return

        # Deliberately not `self.selected = under`. A click says where to put
        # troops once; the keyboard pick is a standing choice, drawn white
        # until something clears it.
        if self.round.phase != "commanding":
            self.selected = under
            self.dirty = True
        else:
            await self._place(under, self.move_count)

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
        """What `n` cycles through: everywhere troops may go, and what just happened.

        Your own ground first, then the ground touching it, because reinforcing
        is the commoner move and assaulting a neighbour is the other one.

        Then anywhere the last phase fought over. A territory that was taken
        off you outright borders nothing of yours any more, so without this the
        one place a player most wants to look at is the one place the keyboard
        cannot reach - and reading the mark meant owning a mouse.
        """
        mine = self._my_uuid()
        held = sorted(t.id for t in self.world.territories if t.owner == mine)
        touching = {n for t in held for n in self.world.territories[t].neighbours}
        reachable = [*held, *sorted(touching.difference(held))]
        seen = set(reachable)
        return [*reachable, *sorted(t for t in self.battle_notes if t not in seen)]

    def _in_reach(self, territory_id: int) -> bool:
        """Whether troops may be placed here: yours, or bordering yours.

        Literally the rule the server checks against - the same method, not a
        copy of it - so a placement this allows is one the server accepts.
        """
        return self.world.within_reach(self._my_uuid(), territory_id)

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
            # Not fatal: the socket is fine and a judge with every slot busy
            # answers late rather than never. Quitting here would cost a player
            # their board over a busy moment they had no part in causing.
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
            case Planned(placements=placements, remaining=remaining, round=round_state):
                self.round = round_state
                self.placements = {p.territory: p.count for p in placements}
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
        is usually how many troops are left, not what one placement moved.
        """
        parts: list[str] = []
        placed = sum(self.placements.values())
        if placed:
            parts.append(f"{placed} placed")
        parts.append(f"{remaining} in hand" if remaining else "nothing left in hand")
        return "  ".join(parts)

    def _say(self, message: str) -> None:
        self.message = message
        self.dirty = True

    def _show_popup(self, lines: list[str], hold: float = 0.0) -> None:
        """Put a panel up, and remember it so `[v]` can bring it back.

        Panels wait rather than expire. A result that vanishes on a timer is a
        result somebody looked away from and lost, and the ones that appear
        unasked - a battle report the moment a phase ends - are exactly the
        ones worth reading.
        """
        self.popup = lines
        self.popup_hold_until = time.monotonic() + hold if hold else 0.0
        self.last_popup = lines
        self.dirty = True

    def _panel(self) -> list[str]:
        """The popup as it should be drawn right now.

        A panel's last line is its dismiss hint, and while pressing a key would
        do nothing the hint says when it will start working instead. Leaving
        "[any key] back to the board" up while keys do nothing is the version
        of this that reads as the game having frozen.
        """
        if not self.popup:
            return []
        notice = hud.hold_notice(self.popup_hold_until - time.monotonic())
        if notice is None:
            return self.popup
        return [*self.popup[:-1], notice]

    def _handle_mouse(self, key: Keystroke) -> bool:
        # Keystroke reports mouse position as mouse_xy, not .x / .y, and gives
        # (-1, -1) for anything that isn't a mouse event.
        mx, my = key.mouse_xy
        if (mx, my) != (-1, -1):
            self.cursor = (mx, my)

        delta = wheel_delta(key.name or "")
        if delta is not None:
            zoom = self.view.zoom
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
        draw_garrisons(frame, self.world, view, self.factions, self.placements)
        if self.legend:
            draw_legend(frame, view, self.factions)

        bars = [
            hud.phase_line(self.round, self.me, self.message),
            f" {self.hover}",
            hud.key_line(self.round, frame.width),
        ]
        for offset, text in enumerate(bars):
            frame.row(view.drawn_rows + offset, text)

        if self.popup is not None:
            hud.draw_panel(frame, self._panel())
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
                    self._forget_plan()
                    # The phase turns on the server's clock, and a player who
                    # has dropped into their editor to write a solution cannot
                    # see it happen. This is the only thing that reaches them
                    # there, so it belongs on the change itself rather than on
                    # any one screen.
                    if self.round is not None:
                        beep()
                    if round_state.phase == "commanding":
                        # Marks survive the submitting phase so a player
                        # looking up from their editor can still see what
                        # happened, and clear before there is planning to do.
                        self.battle_notes = {}
                self.round = round_state
                self.refresh_hover()
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
                self.battle_notes = {b.territory: hud.mark_note(b, self.me) for b in battles}
                for order in dropped:
                    # A territory can be both fought over and short of troops
                    # meant for it. The battle is the bigger news.
                    self.battle_notes.setdefault(order.territory, hud.dropped_note(order, self.me))
                self.refresh_hover()
                self._show_popup(
                    hud.battles_popup(battles, dropped, self.world, self.me),
                    hold=hud.reveal_hold(battles, dropped),
                )
                self._say(f"{len(battles)} battles" if battles else "troops landed")
            case GameOver(name=name, winner=winner):
                self._show_popup(hud.game_over_popup(name, winner), hold=hud.GAME_OVER_HOLD_SECONDS)
                ending = f"{name} has taken the whole board" if winner is not None else "nobody is left holding ground"
                self._say(f"{ending} - game over")
            case _:
                logger.debug("unhandled server event %r", event)
