"""The two lines under the board: what phase it is, and what to press.

Split out of `render` because it is the only part of the screen that reads the
round rather than the terrain, and it changes every second while the rest of a
frame changes hardly at all.
"""

from __future__ import annotations

from client.screen import Color, Screen
from client.viewport import Viewport
from protocol.rounds import BattleReport, Phase, RoundState, Verdict
from protocol.terrain import WorldMap

# White on the same blue the menu panels use, so an overlay reads as one
# thing wherever it appears.
PANEL_FG: Color = (255, 255, 255)
PANEL_BG: Color = (28, 62, 122)

# The short version, for the bar under the board. Both input methods get a
# mention: the mouse is faster, the keyboard is the one that always works on
# a terminal with no mouse reporting. The full list lives behind [?].
KEYS: dict[Phase, str] = {
    "submitting": "[s]ubmit  [v] last result  [f]inished",
    "commanding": "click to place  [m] march from here  [1-9] how many  [p] all  [c]lear  [f]inished",
}

# Every binding, grouped, for the [?] panel. Phase-specific first, because
# that is what somebody opening it mid-round is looking for.
PHASE_HELP: dict[Phase, list[tuple[str, str]]] = {
    "submitting": [
        ("[s]", "write a solution in $EDITOR, submit on quit"),
        ("[v]", "show the last result again"),
        ("[f]", "finished - ends the phase once everyone is"),
    ],
    "commanding": [
        ("click", "place troops on your own territory"),
        ("[space]", "place on the picked territory"),
        ("[p]", "place every troop you have left"),
        ("[m]", "march from here, then click a neighbour"),
        ("[1-9]", "how many a place or a march moves"),
        ("[n] [N]", "cycle your territories, then neighbours"),
        ("[c]", "clear everything you have planned"),
        ("[esc]", "forget the source you picked"),
        ("[f]", "finished with this phase"),
    ],
}

BOARD_HELP: list[tuple[str, str]] = [
    ("arrows", "scroll the board"),
    ("wheel", "scroll, shift+wheel sideways"),
    ("+ -", "zoom in and out"),
    ("[o]", "ownership overlay"),
    ("[l]", "legend"),
    ("[tab]", "scoreboard"),
    ("[q]", "quit to the menu"),
]

RULES_HELP: list[str] = [
    "A round is submit, then command.",
    "Troops earned = 3 + territories/3 + up to 5 for your",
    "solution, +3 for solving first.",
    "Place them on your own ground and march out of it in",
    "the one phase. Both are secret, shown to you as (+n)",
    "beside a garrison, and both happen when the phase",
    "ends: troops land first, so what you place can march",
    "the same turn. The biggest stack in a territory takes",
    "it, losing as many troops as the next biggest. A tie",
    "wipes both out and leaves it unowned.",
]


def help_popup(round_state: RoundState | None, rows: int = 999) -> list[str]:
    """Everything that does something, with the current phase first.

    `rows` is the terminal height. The panel is built in sections and the
    least useful are dropped until it fits, because `draw_panel` truncates
    at the bottom - and the bottom is where "[any key] close" lives, so an
    overlong panel is one a player cannot work out how to dismiss.
    """
    phase = round_state.phase if round_state is not None else None
    footer = ["", "[any key] close"]

    head: list[str] = ["HELP", ""]
    if phase is not None:
        head.append(f"{phase.upper()} - what you can do now")
        head += [f"  {key:<9} {what}" for key, what in PHASE_HELP[phase]]
        head.append("")

    board = ["ANY TIME", *(f"  {key:<9} {what}" for key, what in BOARD_HELP)]
    rules = ["", "HOW IT WORKS", *(f"  {line}" for line in RULES_HELP)]

    # Two rows of padding from the panel border, and never the whole screen.
    budget = max(8, rows - 2)
    for sections in ([head, board, rules], [head, board], [head]):
        lines = [line for section in sections for line in section]
        if len(lines) + len(footer) <= budget:
            return [*lines, *footer]

    # Still too tall: keep the phase bindings, which is what was asked for.
    return [*head[: budget - len(footer)], *footer]


def clock(seconds: float) -> str:
    minutes, rest = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{rest:02d}"


def phase_line(round_state: RoundState | None, me: str, message: str) -> str:
    """Round, phase, countdown, then whatever last happened to this player."""
    if round_state is None:
        return " waiting for the server ..."

    mine = round_state.player(me)
    parts = [f"round {round_state.number}", round_state.phase.upper(), clock(round_state.seconds_left)]

    if round_state.phase == "submitting" and round_state.problem is not None:
        parts.append(round_state.problem.title)
        if mine is not None and mine.best is not None:
            parts.append(f"best {mine.best.passed}/{mine.best.total}")
    elif round_state.phase == "commanding" and mine is not None and mine.troops:
        parts.append(f"{mine.troops} troops to place")

    waiting = round_state.waiting_on()
    if waiting:
        parts.append(f"waiting on {waiting}")

    line = "  ".join(parts)
    return f" {line}   {message}" if message else f" {line}"


def key_line(round_state: RoundState | None, selected: int | None) -> str:
    """The keys that do something right now, and any pending selection."""
    common = "arrows/wheel scroll  +/- zoom  [o] borders  [l]egend  [tab] scores  [q]uit"
    if round_state is None:
        return f"  {common} "

    keys = KEYS.get(round_state.phase, "")
    if selected is not None:
        keys = f"from {selected} ->  {keys}"
    return f"  {keys}   {common} "


# Drawn instead of colour, so the result reads the same on a terminal that
# has none and in a screenshot pasted into a chat.
CASE_MARKS: dict[str, str] = {
    "passed": "ok  ",
    "wrong": "WRONG",
    "timeout": "SLOW",
    "crashed": "CRASH",
    "flooded": "FLOOD",
}


def verdict_popup(verdict: Verdict, round_state: RoundState | None, me: str) -> list[str]:
    """The submission result, case by case, as something to be dismissed.

    Every case gets a line whether it passed or not: "3 of 5" says nothing
    about which three, and a solution that passes the small cases and times
    out on the big one is a different problem from one that is simply wrong.
    """
    passed = verdict.passed == verdict.total and verdict.total > 0
    lines = [
        "ACCEPTED" if passed else "NOT YET",
        "",
        f"{verdict.passed} of {verdict.total} tests passed",
        "",
    ]

    for case in verdict.cases:
        mark = CASE_MARKS.get(case.status, "?")
        timing = f"{case.milliseconds:>5}ms" if case.milliseconds else " " * 7
        detail = f"  {case.detail}" if case.detail and not case.ok else ""
        lines.append(f" {case.index}. {mark:<6}{timing}{detail}"[:64])

    if not verdict.cases and verdict.error:
        lines.append(verdict.error)

    mine = round_state.player(me) if round_state is not None else None
    lines.append("")
    if mine is not None:
        best = f"best so far {mine.best.passed}/{mine.best.total}" if mine.best else ""
        lines.append(f"attempt {mine.submissions}   {best}".strip())
    lines.append("everyone else now has a minute" if passed else "press [s] to edit and try again")
    return [*lines, "", "[any key] close   [v] show this again"]


def battles_popup(battles: list[BattleReport]) -> list[str]:
    """What the marching orders did, once they all landed at once."""
    if not battles:
        return ["ORDERS CARRIED OUT", "", "nobody met anybody", "", "[any key] back to the board"]

    lines = ["BATTLES", ""]
    for battle in battles:
        sides = "  vs  ".join(f"{s.name} {s.troops}" for s in battle.stacks)
        outcome = f"{battle.winner_name} holds it with {battle.survivors}" if battle.winner else "wiped out - unowned"
        lines += [f"territory {battle.territory}: {sides}", f"    {outcome}"]
    return [*lines, "", "[any key] back to the board"]


def where(world: WorldMap, view: Viewport) -> str:
    """The slice of board on screen, so scrolling has a read-out."""
    return f"[{view.x},{view.y}] {view.span_x}x{view.span_y} of {world.width}x{world.height} @{view.zoom}x"


def scoreboard(round_state: RoundState | None, me: str) -> list[str]:
    """One line per player, for the panel `[tab]` opens."""
    if round_state is None:
        return ["no round yet"]

    rows = [f"=== ROUND {round_state.number} - {round_state.phase.upper()} ===", ""]
    for player in round_state.players:
        name = f"{player.name} (you)" if player.player_id == me else player.name
        best = player.best.summary() if player.best is not None else "no submission"
        mark = "done" if player.done else "..."
        rows.append(f"{name[:16]:<16} {best:<22} {player.troops:>2} troops  {mark}")
    return rows


def draw_panel(screen: Screen, lines: list[str]) -> None:
    """A boxed overlay in the middle of the screen, for popups and scores."""
    if not lines:
        return

    width = min(max(len(line) for line in lines) + 4, screen.width)
    rows = ["", *lines, ""]
    top = max(0, (screen.height - len(rows)) // 2)
    left = max(0, (screen.width - width) // 2)

    for index, text in enumerate(rows):
        y = top + index
        if y >= screen.height:
            break
        screen.text(left, y, text.center(width)[:width], fg=PANEL_FG, bg=PANEL_BG, bold=True)
