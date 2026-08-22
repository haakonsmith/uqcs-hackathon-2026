"""A grid of cells to compose a frame into, and the diff that puts it on screen.

Everything that draws - terrain, garrison counts, the legend, the status bars,
popups - writes into one `Screen` instead of emitting escape sequences of its
own. At the end of a frame the screen is compared with the one before it and
only the cells that differ are written.

That is worth doing for two reasons beyond the byte count. The first is that
correctness stops depending on bookkeeping: with per-component redrawing, a
panel laid over the board leaves the board's idea of what is on screen wrong,
and every new overlay is another chance to forget to invalidate something. A
composed frame cannot disagree with itself. The second is that the diff no
longer cares what changed - a countdown digit, a highlight ring or a whole new
popup all cost exactly the cells they touch.

Colours are held as RGB tuples rather than escape strings so that comparing
two frames is comparing tuples, which is cheap, rather than comparing
formatted output, which is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import blessed

Color = tuple[int, int, int]

# One cell of the composed frame: the character, its colours, and whether it
# is bold. `None` for either colour means the terminal default. A tuple rather
# than a class because a frame holds thousands of these and they are compared
# one against another every frame.
type Cell = tuple[str, Color | None, Color | None, bool]

BLANK: Cell = (" ", None, None, False)


@dataclass
class Screen:
    """What the terminal should be showing, one cell per column."""

    width: int
    height: int
    cells: list[list[Cell]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cells:
            self.clear()

    def clear(self) -> None:
        self.cells = [[BLANK] * self.width for _ in range(self.height)]

    def matches(self, width: int, height: int) -> bool:
        return self.width == width and self.height == height

    def set(self, x: int, y: int, cell: Cell) -> None:
        """One cell, ignored when it falls outside the screen.

        Clipping here rather than at every call site: panels are positioned by
        arithmetic that can put them partly off a small terminal, and every
        caller checking its own bounds is how one of them ends up not doing it.
        """
        if 0 <= y < self.height and 0 <= x < self.width:
            self.cells[y][x] = cell

    def text(
        self,
        x: int,
        y: int,
        value: str,
        fg: Color | None = None,
        bg: Color | None = None,
        bold: bool = False,
    ) -> None:
        """A run of characters starting at `x`, clipped to the screen."""
        for offset, char in enumerate(value):
            self.set(x + offset, y, (char, fg, bg, bold))

    def fill(self, x: int, y: int, width: int, height: int, bg: Color, char: str = " ") -> None:
        """A solid block, for panel backgrounds and board cells."""
        for row in range(y, y + height):
            for column in range(x, x + width):
                self.set(column, row, (char, None, bg, False))

    def row(self, y: int, value: str, fg: Color | None = None, bg: Color | None = None) -> None:
        """A whole row, padded out so it erases whatever it replaces."""
        self.text(0, y, value.ljust(self.width)[: self.width], fg, bg)

    def cell_at(self, x: int, y: int) -> Cell:
        if 0 <= y < self.height and 0 <= x < self.width:
            return self.cells[y][x]
        return BLANK


def render(term: blessed.Terminal, previous: Screen | None, current: Screen) -> str:
    """The escape sequences that turn `previous` into `current`.

    Cells are emitted in runs: one cursor move and one colour change carry as
    many neighbouring cells as share them. A run stops at the first cell that
    already matches, because leaving it alone is cheaper than rewriting it.
    """
    if previous is None or not previous.matches(current.width, current.height):
        previous = None

    parts: list[str] = [term.home] if previous is None else []
    fg: Color | None = None
    bg: Color | None = None
    bold = False
    positioned = False

    for y in range(current.height):
        old_row = previous.cells[y] if previous is not None else None
        new_row = current.cells[y]
        x = 0
        while x < current.width:
            if old_row is not None and new_row[x] == old_row[x]:
                x += 1
                positioned = False
                continue

            if not positioned:
                parts.append(term.move_xy(x, y))
                positioned = True

            char, cell_fg, cell_bg, cell_bold = new_row[x]
            # Only when they actually change: a run of one colour needs one
            # escape, and `term.normal` is what resets a colour back to none.
            if (cell_fg, cell_bg, cell_bold) != (fg, bg, bold):
                parts.append(term.normal)
                if cell_bg is not None:
                    parts.append(term.on_color_rgb(*cell_bg))
                if cell_fg is not None:
                    parts.append(term.color_rgb(*cell_fg))
                if cell_bold:
                    parts.append(term.bold)
                fg, bg, bold = cell_fg, cell_bg, cell_bold
            parts.append(char)
            x += 1

        # A row ends wherever it ends; the next one needs its own move.
        positioned = False

    if parts:
        parts.append(term.normal)
    return "".join(parts)
