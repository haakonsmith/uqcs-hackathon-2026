"""The two lines under the board: what phase it is, and what to press.

Split out of `render` because it is the only part of the screen that reads the
round rather than the terrain, and it changes every second while the rest of a
frame changes hardly at all.
"""

from __future__ import annotations

from client.palette import PANEL_BG, PANEL_FG
from client.screen import Screen
from protocol.rounds import BattleReport, DroppedOrder, Phase, PlayerRound, RoundState, Verdict

# The short version, for the bar under the board. Both input methods get a
# mention: the mouse is faster, the keyboard is the one that always works on
# a terminal with no mouse reporting. The full list lives behind [?].
KEYS: dict[Phase, str] = {
    "submitting": "[s]ubmit  [v] last panel  [f]inished",
    "commanding": "[v] last panel  click to place  [m] march from here  [1-9] how many  [p] all  [c]lear  [f]inished",
}

# Every binding, grouped, for the [?] panel. Phase-specific first, because
# that is what somebody opening it mid-round is looking for.
PHASE_HELP: dict[Phase, list[tuple[str, str]]] = {
    "submitting": [
        ("[s]", "write a solution in your editor, submit when you are done"),
        ("[v]", "show the last panel again"),
        ("[f]", "finished - ends the phase once everyone is"),
    ],
    "commanding": [
        ("click", "reinforce your ground, or assault ground beside it"),
        ("[space]", "place on the picked territory"),
        ("[p]", "place every troop you have left"),
        ("[m]", "march from here, then click a neighbour; [m] again calls it off"),
        ("[1-9]", "how many a place or a march moves"),
        ("[n] [N]", "cycle your territories, then neighbours"),
        ("[c]", "clear everything you have planned"),
        ("[esc]", "forget the source you picked"),
        ("[f]", "finished with this phase"),
    ],
}

BOARD_HELP: list[tuple[str, str]] = [
    ("arrows", "scroll the board"),
    ("wheel", "scroll, shift+wheel for sideways"),
    ("+ -", "zoom in and out"),
    ("[o]", "faint or bold borders"),
    ("[l]", "show or hide the legend"),
    ("[tab]", "scoreboard"),
    ("[?]", "this panel"),
    ("[q]", "quit to the menu"),
]

# Short lines that each say one thing. The old version was a paragraph wrapped
# to the panel, which is the hardest shape to find an answer in.
RULES_HELP: list[str] = [
    "Each round: solve a problem, then command your troops.",
    "",
    "Solving earns troops - 3, plus one per 3 territories,",
    "plus up to 5 for how much of the problem you got, plus",
    "3 for being first to solve it.",
    "",
    "Place troops and order marches in the same phase. Both",
    "are secret until it ends, and shown to you as (+n) on",
    "a garrison until then.",
    "",
    "When the phase ends, troops land first - so what you",
    "placed can march the same turn. Where marches meet, the",
    "biggest stack takes the territory and loses as many",
    "troops as the second biggest. A tie wipes both out and",
    "leaves the ground unowned.",
]


def help_popup(round_state: RoundState | None, rows: int = 999) -> list[str]:
    """Everything that does something, with the current phase first.

    `rows` is the terminal height. The panel is built in sections and the
    least useful are dropped until it fits, because `draw_panel` truncates at
    the bottom - and the bottom is where "[any key] close" lives, so an
    overlong panel is one a player cannot work out how to dismiss.
    """
    phase = round_state.phase if round_state is not None else None
    footer = ["", "[any key] close"]

    head: list[str] = []
    if phase is not None:
        head += [f"{phase.upper()} - what you can do now", "", *_bindings(PHASE_HELP[phase]), ""]

    board = ["ANY TIME", "", *_bindings(BOARD_HELP)]
    rules = ["", "HOW A ROUND WORKS", "", *RULES_HELP]

    # Two rows of padding from the panel border, and never the whole screen.
    budget = max(8, rows - 2)
    for sections in ([head, board, rules], [head, board], [head]):
        lines = [line for section in sections for line in section]
        if len(lines) + len(footer) <= budget:
            return [*lines, *footer]

    # Still too tall: keep the phase bindings, which is what was asked for.
    return [*head[: budget - len(footer)], *footer]


def _bindings(rows: list[tuple[str, str]]) -> list[str]:
    """Key and meaning in two columns, the keys all the same width."""
    width = max(len(key) for key, _ in rows)
    return [f"  {key:<{width}}   {what}" for key, what in rows]


# Below this the clock is the pressing thing, so the bar starts naming who
# everyone is waiting for. Earlier in a phase it is just noise.
GRACE_HINT = 60.0


def clock(seconds: float) -> str:
    minutes, rest = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{rest:02d}"


def phase_line(round_state: RoundState | None, me: str, message: str) -> str:
    """One sentence saying what is happening and what this player should do.

    A sentence rather than a row of fields. The bar used to read

        round 2  COMMANDING  1:30  5 troops to place  waiting on 2

    which is six things to parse to answer the one question a player has,
    which is "what now". The phase name, the count and the clock are all still
    here; they are just arranged so reading them in order is a sentence.
    """
    if round_state is None:
        return " waiting for the server ..."

    mine = round_state.player(me)
    sentence = f"Round {round_state.number}: {_doing(round_state, mine)}"

    left = clock(round_state.seconds_left)
    if mine is not None and mine.done:
        sentence += f", you are done - {_blocking(round_state, me)}"
    elif (waiting := _blocking(round_state, me)) and round_state.seconds_left < GRACE_HINT:
        sentence += f" - {left} left, {waiting}"
    else:
        sentence += f" - {left} left"

    return f" {sentence}   {message}" if message else f" {sentence}"


def _doing(round_state: RoundState, mine: PlayerRound | None) -> str:
    """The part of the sentence that says what this phase is for."""
    if round_state.phase == "submitting":
        title = round_state.problem.title if round_state.problem is not None else "the problem"
        if mine is not None and mine.best is not None:
            if mine.best.perfect:
                return f"you solved {title}"
            return f"solve {title}, best so far {mine.best.passed}/{mine.best.total}"
        return f"solve {title}"

    if mine is not None and mine.troops:
        return f"place {mine.troops} troops and order marches"
    return "order marches"


def _blocking(round_state: RoundState, me: str) -> str:
    """Who the phase is waiting on, named while there are few enough to name."""
    others = [p.name for p in round_state.players if not p.done and p.player_id != me]
    if not others:
        return ""
    if len(others) == 1:
        return f"waiting on {others[0]}"
    if len(others) == 2:
        return f"waiting on {others[0]} and {others[1]}"
    return f"waiting on {len(others)} players"


def key_line(round_state: RoundState | None, selected: int | None) -> str:
    """The keys that do something right now, and any pending selection."""
    # Only what this phase answers, plus the way to everything else. The rest
    # used to be listed here and ran to 200 characters, which no terminal
    # showed in full and which the [?] panel now covers properly.
    common = "[?] help  [q]uit"
    if round_state is None:
        return f"  {common} "

    keys = KEYS.get(round_state.phase, "")
    if selected is not None:
        # Mid-order the phase keys are the wrong list: what matters is that an
        # order is half-made, where it starts, and the two ways out of it.
        return f"  marching from {selected} - click a neighbour  [esc] give up  [m] on {selected} calls it off   {common} "
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


def battles_popup(battles: list[BattleReport], dropped: list[DroppedOrder] | None = None) -> list[str]:
    """What the marching orders did, once they all landed at once.

    Orders that could not be carried out are listed under the battles rather
    than left out. They move nothing, so they reach the board delta as an
    absence: troops a player watched themselves commit that are simply not
    there next round, with no way to tell a lost order from a lost fight.
    """
    dropped = dropped or []
    if not battles and not dropped:
        return ["ORDERS CARRIED OUT", "", "nobody met anybody", "", "[any key] back to the board"]

    lines = ["BATTLES", ""] if battles else []
    for battle in battles:
        sides = "  vs  ".join(f"{s.name} {s.troops}" for s in battle.stacks)
        outcome = f"{battle.winner_name} holds it with {battle.survivors}" if battle.winner else "wiped out - unowned"
        lines += [f"territory {battle.territory}: {sides}", f"    {outcome}"]

    if dropped:
        lines += ["", "NEVER HAPPENED", ""]
        for order in dropped:
            lines += [f"{order.name}: {order.count} at territory {order.territory}", f"    {order.reason}"]
    return [*lines, "", "[any key] back to the board"]


def game_over_popup(name: str, winner: str | None) -> list[str]:
    """The last thing the board has to say."""
    verdict = f"{name} holds the whole board" if winner is not None else "every territory is neutral - nobody won"
    return ["GAME OVER", "", verdict, "", "[any key] back to the board"]


def scoreboard(round_state: RoundState | None, me: str) -> list[str]:
    """One line per player, for the panel `[tab]` opens."""
    if round_state is None:
        return ["no round yet"]

    rows = [f"ROUND {round_state.number} - {round_state.phase.upper()}", ""]
    rows.append(f"{'player':<16} {'best':<24} {'troops':>6}  {'phase':<7}")
    for player in round_state.players:
        name = f"{player.name} (you)" if player.player_id == me else player.name
        best = player.best.summary() if player.best is not None else "no submission"
        mark = "done" if player.done else "thinking"
        rows.append(f"{name[:16]:<16} {best[:24]:<24} {player.troops:>6}  {mark:<7}")
    return rows


def draw_panel(screen: Screen, lines: list[str]) -> None:
    """A boxed overlay in the middle of the screen, for popups and scores.

    Every line is left-aligned. Centring each line independently is what made
    a list of keys look ragged: each row found its own middle, so a column
    that lined up in the text did not line up on screen.
    """
    if not lines:
        return

    content = min(max((len(line) for line in lines), default=0), screen.width - 4)
    width = content + 4
    rows = ["", *lines, ""]
    top = max(0, (screen.height - len(rows)) // 2)
    left = max(0, (screen.width - width) // 2)

    for index, line in enumerate(rows):
        y = top + index
        if y >= screen.height:
            break
        screen.text(left, y, f"  {line.ljust(content)}  "[:width], fg=PANEL_FG, bg=PANEL_BG, bold=True)
