"""Everything the screen is drawn from, and the ways it changes.

Input and (later) the network both mutate this one object and set `dirty`, so
a server event repaints exactly the way a keypress does. While these lived as
locals in the render loop, only a keypress could cause a redraw.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

import blessed
from blessed.keyboard import Keystroke

from client import terrain
from client.input import SCROLL_KEYS, wheel_delta
from client.render import Highlight, render, status
from client.viewport import Viewport
from protocol import ServerEvent

logger = logging.getLogger("client")


@dataclass
class App:
    term: blessed.Terminal
    world: terrain.WorldMap
    view: Viewport = field(default_factory=Viewport)
    overlay: bool = False
    cursor: tuple[int, int] | None = None
    hover: str = "move the mouse over the map"
    highlights: dict[int, Highlight] = field(default_factory=dict)
    dirty: bool = True
    running: bool = True
    _size: tuple[int, int] = (0, 0)

    def fit(self) -> None:
        """Size the viewport to the terminal and refresh what follows from it."""
        self._size = (self.term.width, self.term.height)
        self.view.fit(self.term.width, self.term.height, self.world)
        self.refresh_hover()
        self.dirty = True

    def check_resize(self) -> None:
        """A resize changes how much board fits, so refit before drawing."""
        if (self.term.width, self.term.height) != self._size:
            self.fit()

    def refresh_hover(self) -> None:
        """Recompute what the cursor points at.

        Called after scrolling and zooming too, not just on mouse motion: the
        board moves under a stationary mouse, so what it points at changes.
        """
        self.highlights = {}
        if self.cursor is None:
            self.hover = "move the mouse over the map"
            return

        cell = self.view.cell_at(self.world, *self.cursor)
        if cell is None:
            self.hover = "off the board"
            return

        where = f"({cell.x},{cell.y}) {cell.terrain.label} height {cell.height:.2f}"
        if cell.territory is None:
            self.hover = f"{where} - unclaimed"
            return

        owner = self.world.territories[cell.territory]
        detail = f"territory {owner.id}, continent {owner.continent}, {len(owner.neighbours)} neighbours"
        self.hover = f"{where} - {detail}"
        self.highlights[owner.id] = True

    def handle_key(self, key: Keystroke) -> None:
        moved = False

        if key.lower() == "q":
            self.running = False
            return

        if key == "o":
            self.overlay = not self.overlay
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
            term.home + render(term, self.world, view, self.overlay, self.highlights),
            end="",
        )
        print(
            term.move_xy(0, view.drawn_rows) + term.ljust(status(self.world, view, self.hover)[: term.width]),
            end="",
        )
        # Zooming out shrinks the map below the window, so wipe whatever the
        # previous frame left under the status bar. Skip it when the bar is
        # already on the last row: moving past the bottom clamps back onto the
        # bar and the erase would take the bar with it.
        if view.drawn_rows + 1 < term.height:
            print(term.move_xy(0, view.drawn_rows + 1) + term.clear_eos, end="")
        _ = sys.stdout.flush()
        self.dirty = False

    def apply_event(self, event: ServerEvent) -> None:
        """Fold a server push into the board. Repaints the same way a key does."""
        logger.debug("unhandled server event %r", event)
        _ = event
