"""The start menu: title art over a world-map backdrop, and three buttons.

Drawn from the same queue the board reads, never from its own `inkey()`: two
readers on one terminal race for keystrokes, and the loser silently drops
them. Every screen here - menu, settings, username prompt - is an
`await events.get()` and a `match`, exactly like `play()`.

The backdrop and the title change only on resize, so the escape sequences for
them are built once and cached; selecting a different button repaints the
buttons only.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, assert_never

import blessed

from client.input import AppEvent, KeyPress, Received, Resized

DEFAULT_ADDRESS = "ws://localhost:8888"
DEFAULT_USERNAME = "Player"

# --- Solid Block Title Art ("TERMINATION") ---
TITLE_ART = [
    " ██████ ███████ ██████  ██      ██ ██ ███    ██  █████  ████████ ██   ██████  ██    ██",
    "   ██   ██      ██   ██ ████  ████ ██ ████   ██ ██   ██    ██    ██  ██    ██ ███   ██",
    "   ██   █████   ██████  ██ ████ ██ ██ ██ ██  ██ ███████    ██    ██  ██    ██ ██ ██ ██",
    "   ██   ██      ██   ██ ██  ██  ██ ██ ██  ██ ██ ██   ██    ██    ██  ██    ██ ██  ████",
    "   ██   ███████ ██   ██ ██      ██ ██ ██   ████ ██   ██    ██    ██   ██████  ██   ███",
]

# Decoration only - the board the game is played on comes from the server.
WORLD_MAP = [
    "           ################      ##`             #                ",
    "         ######## #########      ##               #               ",
    "          ######    #######                   ######    # #       ",
    "        ##########  ########             ##################       ",
    " ##################  ####         ##############################  ",
    " ##################   #    #    ###############################   ",
    "  ##################             ########################         ",
    "        ############        #############################  #      ",
    "        #############         ##########################          ",
    "         ########            ############################         ",
    "          ######            #########################             ",
    " #          ###  ##        ##############  ### ####               ",
    "                #####       ###########                           ",
    "                ########         ######          # #   #          ",
    "                 #######         #######              ###         ",
    "                 ######          #### ##           ########       ",
    "                 ####             ##                #######       ",
    "                  #                                      #        ",
    "                 ##                                               ",
    "                                                                  ",
]


@dataclass(frozen=True)
class MenuItem:
    """One button. `action` is what `_activate` dispatches on, `label` is only
    ever drawn - renaming a button must not be able to change what it does."""

    action: Literal["join", "settings", "exit"]
    label: str
    where: Literal["center_top", "center_bottom", "top_right"]


# Order is arrow-key order; `where` is the corner of the screen each sits in.
MENU_ITEMS = [
    MenuItem("join", "JOIN GAME", "center_top"),
    MenuItem("settings", "SETTINGS", "top_right"),
    MenuItem("exit", "EXIT", "center_bottom"),
]

TITLE_TOP = 1
MAP_OFFSET_Y = 2

# Panels are drawn at a fixed minimum width so that typing into one does not
# make the box breathe in and out a column at a time.
PANEL_WIDTH = 48


@dataclass(frozen=True)
class JoinGame:
    """Connect to `address` and play as `username`."""

    address: str
    username: str


@dataclass(frozen=True)
class Quit:
    """The player asked to leave."""


type MenuChoice = JoinGame | Quit


@dataclass
class MenuState:
    """What the menu remembers between visits.

    The menu is one screen the app returns to over and over, so the address
    typed into settings has to outlive a single call to `run_menu`.
    """

    selected: int = 0
    address: str = DEFAULT_ADDRESS
    username: str = DEFAULT_USERNAME


state = MenuState()


def button_geometry(index: int, width: int, height: int) -> tuple[int, int, int]:
    """Top-left corner and drawn width of a button, in screen cells."""
    item = MENU_ITEMS[index]
    length = len(item.label) + 4
    if item.where == "top_right":
        return max(0, width - length - 4), 7, length
    if item.where == "center_top":
        return max(0, (width - length) // 2), height // 2 + 2, length
    return max(0, (width - length) // 2), height // 2 + 4, length


def _backdrop(term: blessed.Terminal) -> str:
    """Ocean, landmasses and title, sized to the terminal."""
    return _render_backdrop(term, term.width, term.height)


@lru_cache(maxsize=1)
def _render_backdrop(term: blessed.Terminal, width: int, height: int) -> str:
    """Draw the backdrop for one terminal size.

    Cached because this is a per-cell colour call across the whole screen -
    tens of milliseconds and tens of kilobytes - and it is redrawn under every
    panel, including once per keystroke while a prompt is open. The size is in
    the key rather than read off `term`, so a resize misses and redraws.

    One entry is enough: only the current size is ever asked for, and a resize
    should evict the old one rather than hold a screen's worth of string.
    """
    # Annotated, because `term.home` is a blessed string subclass and an
    # inferred list of it rejects the plain strings appended below.
    output: list[str] = [term.home]

    map_height = len(WORLD_MAP)
    map_width = len(WORLD_MAP[0]) * 2
    start_y = max(0, (height - map_height) // 2) + MAP_OFFSET_Y
    start_x = max(0, (width - map_width) // 2)
    rows = {start_y + index: row for index, row in enumerate(WORLD_MAP)}

    for y in range(height):
        row = rows.get(y, "")
        cells: list[str] = []
        # Two columns per map cell, because terminal cells are about twice as
        # tall as they are wide and a 1:1 map comes out sheared.
        for x in range(0, width - 1, 2):
            column = (x - start_x) // 2
            land = x >= start_x and (x - start_x) % 2 == 0 and column < len(row) and row[column] != " "
            cells.append(term.green_on_blue("██") if land else term.blue_on_blue("██"))
        output.append(term.move_xy(0, y) + "".join(cells))

    for index, line in enumerate(TITLE_ART):
        y = TITLE_TOP + index
        if y >= height:
            break
        left = max(0, (width - len(line)) // 2)
        for offset, char in enumerate(line):
            x = left + offset
            if 0 <= x < width:
                # The gaps inside the letters are painted solid blue rather
                # than left transparent, so the map never shows through them.
                output.append(term.move_xy(x, y) + (term.red_on_blue(char) if char != " " else term.blue_on_blue(" ")))

    return "".join(output)


def _buttons(term: blessed.Terminal) -> str:
    """The buttons and the footer - everything that changes without a resize."""
    width, height = term.width, term.height
    output: list[str] = []

    for index, item in enumerate(MENU_ITEMS):
        x, y, _length = button_geometry(index, width, height)
        if y >= height:
            continue
        if index == state.selected:
            output.append(term.move_xy(x, y) + term.bold_black_on_white(f"[>{item.label}<]"))
        else:
            output.append(term.move_xy(x, y) + term.bold_white_on_blue(f"[ {item.label} ]"))

    footer = f" {state.username} @ {state.address}   arrows/mouse select  [enter] choose  [q] quit "
    output.append(term.move_xy(0, height - 1) + term.bold_white_on_blue(footer[:width].ljust(width)))
    return "".join(output)


def _paint(term: blessed.Terminal, backdrop: bool) -> None:
    print((_backdrop(term) + _buttons(term)) if backdrop else _buttons(term), end="")
    _ = sys.stdout.flush()


def _panel(term: blessed.Terminal, lines: Sequence[str]) -> str:
    """A centred box of text, laid over whatever is already on screen."""
    width = max(PANEL_WIDTH, max((len(line) for line in lines), default=0) + 4)
    width = min(width, term.width)
    # A blank row top and bottom, so the box reads as a box and not as text
    # dropped on the map.
    rows = ["", *lines, ""]
    top = max(0, (term.height - len(rows)) // 2)
    left = max(0, (term.width - width) // 2)

    output: list[str] = []
    for index, text in enumerate(rows):
        y = top + index
        if y >= term.height:
            break
        output.append(term.move_xy(left, y) + term.bold_white_on_blue(text.center(width)[:width]))
    return "".join(output)


def notice(term: blessed.Terminal, *lines: str) -> None:
    """Show a panel over the menu. Used for progress that passes on its own.

    The menu is repainted underneath rather than the screen cleared, so
    connecting looks like a step in the menu instead of the app vanishing.
    """
    print(_backdrop(term) + _buttons(term) + _panel(term, lines), end="")
    _ = sys.stdout.flush()


async def alert(term: blessed.Terminal, events: asyncio.Queue[AppEvent], *lines: str) -> None:
    """Show a panel and wait for the player to dismiss it.

    Errors used to sit on screen for a fixed two seconds, which is too long to
    watch and too short to read a socket error in. This waits instead.
    """
    body = (*lines, "", "[any key] back to menu")
    notice(term, *body)

    while True:
        match await events.get():
            case Resized():
                notice(term, *body)
            case Received():
                continue
            case KeyPress(key=key):
                name = key.name or ""
                # Mouse motion is a keystroke here, and dismissing on it would
                # blink the panel away before it has been read.
                if not name.startswith("MOUSE_") or name == "MOUSE_LEFT":
                    return


def _draw_prompt(term: blessed.Terminal, title: str, buffer: str, hint: str) -> None:
    # The cursor is hidden app-wide, so the caret is drawn as text.
    notice(term, title, "", f"> {buffer}_", "", hint)


async def prompt(
    term: blessed.Terminal,
    events: asyncio.Queue[AppEvent],
    title: str,
    initial: str = "",
    hint: str = "[enter] confirm  [esc] cancel",
) -> str | None:
    """Read a line of text, or None if the player pressed escape.

    Reads the shared queue rather than the terminal, so keystrokes typed here
    cannot be swallowed by the pump thread that is already reading it.
    """
    buffer = initial
    _draw_prompt(term, title, buffer, hint)

    while True:
        match await events.get():
            case Resized():
                _draw_prompt(term, title, buffer, hint)
            case Received():
                continue
            case KeyPress(key=key):
                name = key.name or ""
                if name == "KEY_ESCAPE":
                    return None
                if name in ("KEY_ENTER", "KEY_RETURN"):
                    return buffer.strip()
                if name == "KEY_BACKSPACE":
                    buffer = buffer[:-1]
                elif key.is_sequence or not key.isprintable():
                    continue
                else:
                    buffer += key
                _draw_prompt(term, title, buffer, hint)


async def _activate(term: blessed.Terminal, events: asyncio.Queue[AppEvent]) -> MenuChoice | None:
    """Run the selected button. None means stay on the menu."""
    match MENU_ITEMS[state.selected].action:
        case "join":
            username = await prompt(term, events, "=== ENTER YOUR USERNAME ===", state.username)
            if username is None:
                return None
            state.username = username or DEFAULT_USERNAME
            return JoinGame(address=state.address, username=state.username)
        case "settings":
            address = await prompt(term, events, "=== SERVER ADDRESS ===", state.address)
            if address:
                state.address = address
            return None
        case "exit":
            return Quit()
        case unknown:
            # Exhaustive over the Literal, so a new action that nobody wired up
            # is a crash here rather than a button that silently quits.
            assert_never(unknown)


def _hit(width: int, height: int, x: int, y: int) -> int | None:
    """The button under a screen position, if any."""
    for index in range(len(MENU_ITEMS)):
        bx, by, length = button_geometry(index, width, height)
        if by == y and bx <= x < bx + length:
            return index
    return None


async def run_menu(term: blessed.Terminal, events: asyncio.Queue[AppEvent]) -> MenuChoice:
    """Show the menu until the player picks something."""
    _paint(term, backdrop=True)

    while True:
        match await events.get():
            case Resized():
                _paint(term, backdrop=True)
            case Received():
                # Nothing is connected while the menu is up; drop late pushes
                # from a game that has already ended rather than queueing them.
                continue
            case KeyPress(key=key):
                name = key.name or ""
                moved = False

                if name == "KEY_UP":
                    state.selected = (state.selected - 1) % len(MENU_ITEMS)
                    moved = True
                elif name == "KEY_DOWN":
                    state.selected = (state.selected + 1) % len(MENU_ITEMS)
                    moved = True
                elif name in ("KEY_ENTER", "KEY_RETURN"):
                    choice = await _activate(term, events)
                    if choice is not None:
                        return choice
                    _paint(term, backdrop=True)
                elif key.lower() == "q":
                    return Quit()
                elif name.startswith("MOUSE_"):
                    x, y = key.mouse_xy
                    if (x, y) == (-1, -1):
                        continue
                    index = _hit(term.width, term.height, x, y)
                    if index is None:
                        continue
                    # Motion highlights, a click on the highlighted button
                    # runs it - so a click on a cold button only selects it.
                    if index != state.selected:
                        state.selected = index
                        moved = True
                    elif name == "MOUSE_LEFT":
                        choice = await _activate(term, events)
                        if choice is not None:
                            return choice
                        _paint(term, backdrop=True)

                if moved:
                    _paint(term, backdrop=False)
