"""Which slice of the board is on screen. Pure geometry - no terminal, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

from protocol import terrain

# Each map cell is drawn two columns wide, because terminal cells are roughly
# twice as tall as they are wide and a 1:1 mapping shears the map vertically.
CELL_WIDTH = 2


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


@dataclass
class Viewport:
    """Which slice of the board is on screen.

    `cols`/`rows` are screen cells; `zoom` is how many map cells each screen
    cell stands for. Zooming out subsamples - one map cell in every `zoom` is
    drawn and the rest are skipped, so the cost of a frame stays fixed no
    matter how much board is on screen.
    """

    x: int = 0
    y: int = 0
    cols: int = 1
    rows: int = 1
    zoom: int = 1
    # Screen cells actually drawn. Smaller than cols/rows when zoomed far
    # enough out that the board runs out before the screen does.
    drawn_cols: int = 1
    drawn_rows: int = 1

    @property
    def span_x(self) -> int:
        """Width of board covered, in map cells."""
        return self.cols * self.zoom

    @property
    def span_y(self) -> int:
        return self.rows * self.zoom

    def fit(self, width: int, height: int, world: terrain.WorldMap, reserved: int = 1) -> None:
        """Size to a terminal of `width` x `height`, less `reserved` status rows."""
        self.cols = max(1, width // CELL_WIDTH)
        self.rows = max(1, height - reserved)
        self.zoom = min(self.zoom, self.max_zoom(world))
        _ = self.scroll(0, 0, world)

    def max_zoom(self, world: terrain.WorldMap) -> int:
        """The zoom at which the whole board fits. Beyond it just adds margin."""
        return max(1, _ceil_div(world.width, self.cols), _ceil_div(world.height, self.rows))

    def scroll(self, dx: int, dy: int, world: terrain.WorldMap) -> bool:
        """Move by (dx, dy) map cells, clamped. True if the view actually moved."""
        before = (self.x, self.y)
        self.x = max(0, min(self.x + dx, world.width - self.span_x))
        self.y = max(0, min(self.y + dy, world.height - self.span_y))
        self.drawn_cols = min(self.cols, _ceil_div(world.width - self.x, self.zoom))
        self.drawn_rows = min(self.rows, _ceil_div(world.height - self.y, self.zoom))
        return (self.x, self.y) != before

    def set_zoom(self, zoom: int, world: terrain.WorldMap) -> bool:
        """Change zoom, keeping the centre of the view put. True if it changed."""
        zoom = max(1, min(zoom, self.max_zoom(world)))
        if zoom == self.zoom:
            return False

        centre_x = self.x + self.span_x // 2
        centre_y = self.y + self.span_y // 2
        self.zoom = zoom
        self.x = centre_x - self.span_x // 2
        self.y = centre_y - self.span_y // 2
        _ = self.scroll(0, 0, world)
        return True

    def to_map(self, screen_x: int, screen_y: int) -> tuple[int, int]:
        """Screen position to map cell. Columns are 2:1 to cells."""
        return (
            self.x + (screen_x // CELL_WIDTH) * self.zoom,
            self.y + screen_y * self.zoom,
        )

    def covers(self, screen_y: int) -> bool:
        """False below the map, so the status bar isn't mistaken for board."""
        return 0 <= screen_y < self.drawn_rows

    def cell_at(self, world: terrain.WorldMap, x: int, y: int) -> terrain.Cell | None:
        """The cell under a screen position, or None if it is off the board."""
        if not self.covers(y):
            return None

        map_x, map_y = self.to_map(x, y)
        if not (0 <= map_x < world.width and 0 <= map_y < world.height):
            return None

        return world.cell(map_x, map_y)
