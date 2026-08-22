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
from client.editor import Draft, NoEditor, edit
from client.input import SCROLL_KEYS, KeyPump, wheel_delta
from client.palette import Factions
from client.render import Highlight, legend_panel, render
from client.viewport import Viewport
from protocol import (
    Acknowledged,
    BoardChanged,
    CancelOrders,
    ClientRequest,
    Connection,
    FinishPhase,
    GameOver,
    Judged,
    MovesResolved,
    MoveTroops,
    Ordered,
    Placed,
    PlaceTroops,
    ProtocolError,
    Refused,
    RoundChanged,
    RoundState,
    ServerEvent,
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


# Status bars under the board: the round, then the hover text and keys.
HUD_ROWS = 2


@dataclass
class App:
    term: blessed.Terminal
    world: terrain.WorldMap
    factions: Factions
    view: Viewport = field(default_factory=Viewport)
    overlay: bool = False
    legend: bool = True
    cursor: tuple[int, int] | None = None
    hover: str = "move the mouse over the map"
    highlights: dict[int, Highlight] = field(default_factory=dict)
    dirty: bool = True
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
    scoreboard: bool = False
    # A result the player has to acknowledge. Any key dismisses it, and
    # while it is up nothing else reads the keyboard - a verdict that
    # scrolls past in a status bar is a verdict nobody saw.
    popup: list[str] | None = None
    # Troops the next [m] will send, set with the number keys.
    move_count: int = 1
    # The last verdict, kept so [v] can bring it back. Dismissing a popup
    # should not be the only chance to read which cases failed.
    last_verdict: Verdict | None = None

    def tick(self) -> None:
        """A second passed: only the countdown on the phase bar changed."""
        if self.round is not None:
            self.round = replace(self.round, seconds_left=max(0.0, self.round.seconds_left - 1.0))
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
            return f"picked {self.selected} ({held.soldiers} soldiers)"
        return fallback or "[n] to pick a territory, or use the mouse"

    def refresh_hover(self) -> None:
        """Recompute what the cursor points at.

        Called after scrolling and zooming too, not just on mouse motion: the
        board moves under a stationary mouse, so what it points at changes.
        """
        self.highlights = {}
        if self.move_from is not None:
            self.highlights[self.move_from] = (235, 90, 70)
        if self.selected is not None:
            self.highlights[self.selected] = (245, 245, 245)

        if self.cursor is None:
            self.hover = self._selection_text()
            return

        cell = self.view.cell_at(self.world, *self.cursor)
        if cell is None:
            self.hover = self._selection_text("off the board")
            return

        where = f"({cell.x},{cell.y}) {cell.terrain.label} height {cell.height:.2f}"
        if cell.territory is None:
            self.hover = f"{where} - unclaimed"
            return

        owner = self.world.territories[cell.territory]
        side = self.factions.side_of.get(owner.id)
        held = self.factions.label(side) if side is not None else "nobody"
        detail = f"territory {owner.id} held by {held}, {owner.soldiers} soldiers, {len(owner.neighbours)} neighbours"
        self.hover = f"{where} - {detail}"
        self.highlights[owner.id] = True

    async def handle_key(self, key: Keystroke) -> None:
        """Act on a keystroke. Async, because most of them are requests."""
        moved = False

        if self.popup is not None:
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
        if await self._handle_phase_key(key):
            return

        if key == "o":
            self.overlay = not self.overlay
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
        if phase == "allocating" and key in (" ", "p"):
            await self._place(all_of_them=key == "p")
            return True
        if phase == "moving":
            if key.isdigit() and key != "0":
                self.move_count = int(key)
                self._say(f"next order sends {self.move_count}")
                return True
            if key == "m":
                await self._order()
                return True
            if key == "c":
                await self._request(CancelOrders())
                return True
        return False

    def _show_verdict(self) -> None:
        """Bring the last result back, for a player who dismissed it too fast."""
        if self.last_verdict is None:
            self._say("no submission yet this round")
            return
        self.popup = hud.verdict_popup(self.last_verdict, self.round, self.me)
        self.dirty = True

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
            # The board was handed to the editor, so none of it is still on
            # screen and a partial repaint would draw over whatever is.
            self.fit()

        if not code.strip():
            self._say("nothing submitted - the draft was empty")
            return

        self._say(f"submitting {len(code)} bytes ...")
        await self._request(SubmitSolution(code=code))

    async def _place(self, all_of_them: bool) -> None:
        territory = self._target_territory()
        if territory is None:
            self._say("pick a territory with [n] or the mouse first")
            return
        mine = self.round.player(self.me) if self.round else None
        count = mine.troops if all_of_them and mine is not None else 1
        if count < 1:
            self._say("no troops left to place")
            return
        await self._request(PlaceTroops(territory=territory, count=count))

    async def _order(self) -> None:
        """Pick a source, then a neighbour, and queue a march between them."""
        territory = self._target_territory()
        if territory is None:
            self._say("pick a territory with [n] or the mouse first")
            return

        if self.move_from is None:
            if self.world.territories[territory].owner != self._my_uuid():
                self._say("orders start from one of your own territories")
                return
            self.move_from = territory
            self.selected = None
            self._say(f"from {territory} - [n] to pick a neighbour, [1-9] for how many, then [m]")
            return

        source = self.world.territories[self.move_from]
        count = min(self.move_count, source.soldiers)
        if count < 1:
            self._say(f"territory {source.id} has nothing left to send")
            self.move_from = None
            return
        await self._request(MoveTroops(source=source.id, target=territory, count=count))
        self.move_from = None

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

        Mid-order that is the neighbours worth marching on; otherwise it is
        your own ground, which is what both placing and picking a source want.
        """
        mine = self._my_uuid()
        if self.round is not None and self.round.phase == "moving" and self.move_from is not None:
            source = self.world.territories[self.move_from]
            return sorted(n for n in source.neighbours if self.world.territories[n].owner != mine)
        return sorted(t.id for t in self.world.territories if t.owner == mine)

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
        self._say(f"territory {self.selected} ({held.soldiers} soldiers) - {index + 1}/{len(options)}")

    def _centre_on(self, territory_id: int) -> None:
        """Put a territory in the middle of the screen, so a pick is visible."""
        centre_x, centre_y = self.world.territories[territory_id].centroid
        target_x = int(centre_x) - self.view.span_x // 2
        target_y = int(centre_y) - self.view.span_y // 2
        _ = self.view.scroll(target_x - self.view.x, target_y - self.view.y, self.world)
        self.refresh_hover()
        self.dirty = True

    def _my_uuid(self) -> UUID | None:
        try:
            return UUID(self.me)
        except ValueError:
            return None

    async def _request(self, request: ClientRequest) -> None:
        """Send, and turn whatever comes back into a line on the phase bar."""
        if self.conn is None:
            return
        try:
            response = await self.conn.send(request)
        except (ConnectionError, TimeoutError, ProtocolError) as error:
            self._say(f"lost the server: {error}")
            self.running = False
            return

        match response:
            case Judged(verdict=verdict, round=round_state):
                self.round = round_state
                self.last_verdict = verdict
                self._say(verdict.summary())
                self.popup = hud.verdict_popup(verdict, round_state, self.me)
            case Placed(round=round_state, remaining=remaining):
                self.round = round_state
                self._say(f"placed - {remaining} troops left")
            case Ordered(orders=orders, round=round_state):
                self.round = round_state
                total = sum(o.count for o in orders)
                self._say(f"{len(orders)} orders queued, {total} troops on the march")
            case Acknowledged(round=round_state):
                self.round = round_state
                self._say("waiting for the others")
            case Refused(reason=reason):
                self._say(reason)
            case _:
                self._say(f"unexpected {type(response).__name__}")

    def _say(self, message: str) -> None:
        self.message = message
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
            return self.view.scroll(delta[0] * zoom, delta[1] * zoom, self.world)

        if (mx, my) != (-1, -1):
            self.refresh_hover()
            self.dirty = True
        return False

    def draw(self) -> None:
        term, view = self.term, self.view
        print(
            term.home + render(term, self.world, view, self.overlay, self.highlights, self.factions),
            end="",
        )
        # After the board, because it is laid over it.
        if self.legend:
            print(legend_panel(term, view, self.factions), end="")

        # Two bars: what the round is doing, and what the mouse is pointing at
        # plus the keys that work right now.
        bars = [
            hud.phase_line(self.round, self.me, self.message),
            f" {hud.where(self.world, view)}  {self.hover}" + hud.key_line(self.round, self.move_from),
        ]
        for offset, text in enumerate(bars):
            row = view.drawn_rows + offset
            if row < term.height:
                print(term.move_xy(0, row) + term.ljust(text[: term.width]), end="")

        # Zooming out shrinks the map below the window, so wipe whatever the
        # previous frame left under the status bars. Skip it when they are
        # already on the last rows: moving past the bottom clamps back onto
        # them and the erase would take them with it.
        if view.drawn_rows + len(bars) < term.height:
            print(term.move_xy(0, view.drawn_rows + len(bars)) + term.clear_eos, end="")

        if self.popup is not None:
            print(hud.render_panel(term, self.popup), end="")
        elif self.scoreboard:
            print(hud.render_panel(term, hud.scoreboard(self.round, self.me)), end="")

        _ = sys.stdout.flush()
        self.dirty = False

    def apply_event(self, event: ServerEvent) -> None:
        """Fold a server push into the board. Repaints the same way a key does."""
        match event:
            case RoundChanged(round=round_state):
                if self.round is None or round_state.number != self.round.number:
                    # A new round is a new problem: last round's verdict is no
                    # longer "the last one" and [v] must not resurrect it.
                    self.last_verdict = None
                if self.round is None or round_state.phase != self.round.phase:
                    # A new phase clears whatever the last one was saying, so
                    # a stale verdict cannot sit over the new instructions.
                    self.message = ""
                    self.move_from = None
                self.round = round_state
                self.dirty = True
            case BoardChanged(updates=updates):
                for update in updates:
                    territory = self.world.territories[update.id]
                    territory.owner = UUID(update.owner) if update.owner else None
                    territory.soldiers = update.soldiers
                self.refresh_hover()
                self.dirty = True
            case MovesResolved(battles=battles):
                self.popup = hud.battles_popup(battles)
                self._say(f"{len(battles)} battles" if battles else "orders carried out")
            case GameOver(name=name):
                self.popup = ["GAME OVER", "", f"{name} holds the whole board", "", "[any key] back to the board"]
                self._say(f"{name} has taken the whole board - game over")
            case _:
                logger.debug("unhandled server event %r", event)
