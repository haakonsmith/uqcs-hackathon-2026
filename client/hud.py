"""The three lines under the board: the phase, the pointer, and the keys.

Split out of `render` because it is the only part of the screen that reads the
round rather than the terrain, and it changes every second while the rest of a
frame changes hardly at all.
"""

from __future__ import annotations

import math

from client.palette import PANEL_BG, PANEL_FG
from client.screen import Screen
from protocol import terrain
from protocol.lobby import MAX_NAME_LENGTH
from protocol.rounds import GRACE_SECONDS, BattleReport, DroppedOrder, Phase, PlayerRound, RoundState, Verdict

# Both input methods get a mention: the mouse is faster, the keyboard is the one
# that always works on a terminal with no mouse reporting.
#
# Ordered most useful first - a row too narrow to hold them all keeps the front
# and gives up the tail. [?] always has the full list.
KEYS: dict[Phase, tuple[str, ...]] = {
    "submitting": ("[s]ubmit", "[f]inished", "[v] last panel"),
    "commanding": ("click to place", "[1-9] how many", "[f]inished", "[v] last panel", "[c]lear", "[p] all"),
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
        ("[1-9]", "how many troops a placement puts down"),
        ("[n] [N]", "cycle your territories, then neighbours"),
        ("[esc]", "let go of the picked territory"),
        ("[c]", "clear everything you have planned"),
        ("[f]", "finished with this phase"),
    ],
}

BOARD_HELP: list[tuple[str, str]] = [
    ("arrows", "scroll the board"),
    ("wheel", "scroll, shift+wheel for sideways"),
    ("+ -", "zoom in and out"),
    ("[o]", "faint or bold borders"),
    ("[d]", "relief shading on or off"),
    ("[l]", "show or hide the legend"),
    ("[tab]", "scoreboard"),
    ("[?]", "this panel"),
    ("[q]", "quit to the menu"),
]

# Short lines that each say one thing: a paragraph wrapped to the panel is the
# hardest shape to find an answer in.
RULES_HELP: list[str] = [
    "Each round: solve a problem, then command your troops.",
    "",
    "Solving earns troops - 3, plus one per 3 territories,",
    "plus up to 5 for how much of the problem you got, plus",
    "3 for being first to solve it.",
    "",
    "Spend them on your own ground or on ground touching it:",
    "the first reinforces, the second assaults. Placements",
    "are secret until the phase ends, and shown to you as",
    "(+n) on a garrison until then.",
    "",
    "Then everything lands at once. Where two players meet,",
    "the biggest stack takes the territory and loses as many",
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
        return f"place {mine.troops} troops"
    return "waiting on the others"


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


def key_line(round_state: RoundState | None, width: int = 0) -> str:
    """The keys that do something right now, as many as `width` will hold.

    Trimmed rather than truncated. The row is written with `Screen.row`, which
    cuts at the terminal width, so a line that does not fit loses its tail
    mid-word - and the tail is where [?] lives, the one hint that leads to all
    the others. Dropping whole hints from the least useful end keeps what
    survives readable and keeps [?] on screen at any width.

    `width` of 0 means no limit, for callers that only want the full line.
    """
    common = "[?] help  [q]uit"
    hints = list(KEYS.get(round_state.phase, ())) if round_state is not None else []

    def render(kept: list[str]) -> str:
        return f"  {'  '.join(kept)}   {common} " if kept else f"  {common} "

    line = render(hints)
    while hints and width and len(line) > width:
        _ = hints.pop()
        line = render(hints)
    return line


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
    grace = f"everyone else now has {GRACE_SECONDS:.0f} seconds"
    lines.append(grace if passed else "press [s] to edit and try again")
    return [*lines, "", "[any key] close   [v] show this again"]


# Where on the map a territory is, said the way somebody looking at the board
# would say it. A territory id is the only handle the protocol has, and no help
# at all to a player who has never seen one.
COMPASS: tuple[tuple[str, str, str], ...] = (
    ("north-west", "north", "north-east"),
    ("west", "middle", "east"),
    ("south-west", "south", "south-east"),
)


def whereabouts(world: terrain.WorldMap, territory_id: int) -> str:
    """A rough compass position, for naming a territory out loud."""
    if not 0 <= territory_id < len(world.territories):
        return f"territory {territory_id}"
    territory = world.territories[territory_id]
    if not territory.cells:
        return f"territory {territory_id}"

    x, y = territory.centroid
    column = min(2, int(x * 3 / max(world.width, 1)))
    row = min(2, int(y * 3 / max(world.height, 1)))
    return f"the {COMPASS[row][column]}"


def _outcome(battle: BattleReport, me: str) -> str:
    """One line saying who ended up with the ground, and who lost it.

    Taking ground and holding it are different results. Naming the side that
    lost is the half a player feels: "Mason holds it" is a fact about the
    board, where "Mason took it from you" is one about their game.
    """
    if battle.winner is None:
        lost = "yours" if battle.defender == me else f"{battle.defender_name}'s"
        whose = f" - it was {lost}" if battle.defender is not None else ""
        return f"nobody holds it{whose}, every side wiped out"

    taker = "you" if battle.winner == me else battle.winner_name
    left = f"{battle.survivors} left standing"
    if battle.held:
        return f"{taker} held it, {left}"
    if battle.defender is None:
        return f"{taker} took empty ground, {left}"
    loser = "you" if battle.defender == me else battle.defender_name
    return f"{taker} took it from {loser}, {left}"


def mark_note(battle: BattleReport, me: str) -> str:
    """The shortest true thing about a battle, for the hover slot.

    The popup's sentence is too long for a line it has to share with the key
    hints, and the count of survivors is on the territory anyway - the garrison
    beside it is that number. What is left is the part the board cannot show:
    which way the ground went, and who it went from.
    """
    if battle.winner is None:
        whose = "yours" if battle.defender == me else f"{battle.defender_name}'s"
        return f"wiped out - was {whose}" if battle.defender is not None else "wiped out"
    taker = "you" if battle.winner == me else battle.winner_name
    if battle.held:
        return f"{taker} held it"
    if battle.defender is None:
        return f"{taker} took empty ground"
    loser = "you" if battle.defender == me else battle.defender_name
    return f"{taker} took it from {loser}"


def dropped_note(order: DroppedOrder, me: str) -> str:
    """The same, for troops that never made it onto the board."""
    whose = "yours" if order.player == me else f"{order.name}'s"
    return f"{order.count} of {whose} never landed"


def battles_popup(
    battles: list[BattleReport],
    dropped: list[DroppedOrder] | None = None,
    world: terrain.WorldMap | None = None,
    me: str = "",
) -> list[str]:
    """What the phase did, once every placement landed at once.

    Written from the reader's side. The same report goes to everybody, but the
    thing each of them wants to know first is what happened to *them*, so their
    own fights are listed first and their own stack is called "you". A list of
    third-person names in territory-id order is the same information and much
    harder to read your own game out of.

    Placements that could not be carried out are listed underneath rather than
    left out. They put nothing on the board, so they reach the delta as an
    absence: troops a player watched themselves commit that are simply not
    there next round, with no way to tell a lost placement from a lost fight.
    """
    dropped = dropped or []
    if not battles and not dropped:
        return ["NOTHING MET", "", "every placement landed unopposed", "", "[any key] back to the board"]

    def place(territory_id: int) -> str:
        where = whereabouts(world, territory_id) if world is not None else f"territory {territory_id}"
        # The id stays in brackets: nobody navigates by it, but it is what the
        # logs and the server say.
        return f"{where}  ({territory_id})"

    def mine(battle: BattleReport) -> bool:
        return any(stack.owner == me for stack in battle.stacks)

    lines: list[str] = []
    if battles:
        lines += ["BATTLES", ""]
        # Yours first, then the rest in the order they were reported.
        for battle in sorted(battles, key=lambda b: not mine(b)):
            sides = "  vs  ".join(f"{'you' if s.owner == me else s.name} {s.troops}" for s in battle.stacks)
            outcome = _outcome(battle, me)
            lines += [f"  {place(battle.territory)}", f"    {sides}", f"    {outcome}", ""]
        lines.pop()

    if dropped:
        lines += ["", "NEVER LANDED", ""] if battles else ["NEVER LANDED", ""]
        for order in dropped:
            whose = "yours" if order.player == me else f"{order.name}'s"
            lines += [f"  {order.count} of {whose} at {place(order.territory)}", f"    {order.reason}", ""]
        lines.pop()

    return [*lines, "", "the board marks these until you next command", "[any key] back to the board"]


# A reveal lands on the server's clock, not the player's, so a key already on
# its way down would close it before anybody had read a word. Long enough to
# swallow that key and no longer; [v] brings it back.
REVEAL_HOLD_SECONDS = 0.4

# The winner's panel ends the game, so there is nothing to get back to.
GAME_OVER_HOLD_SECONDS = 12.0


def reveal_hold(battles: list[BattleReport], dropped: list[DroppedOrder] | None = None) -> float:
    """Seconds `battles_popup` should stay up before it can be dismissed."""
    if not battles and not dropped:
        # Nothing to read, so nothing to protect from a stray keystroke.
        return 0.0
    return REVEAL_HOLD_SECONDS


# Below this the hold is over before a notice about it could be read, and the
# panel would flash a countdown nobody had a chance to act on.
HOLD_NOTICE_FLOOR = 1.0


def hold_notice(seconds: float) -> str | None:
    """Replaces a popup's dismiss hint while the hint would be a lie."""
    if seconds < HOLD_NOTICE_FLOOR:
        return None
    return f"[any key] in {math.ceil(seconds)}s"


def game_over_popup(name: str, winner: str | None) -> list[str]:
    """The last thing the board has to say."""
    verdict = f"{name} holds the whole board" if winner is not None else "every territory is neutral - nobody won"
    return ["GAME OVER", "", verdict, "", "[any key] back to the board"]


# Wide enough for the longest name a player may take plus the marker on their
# own row. Derived, because a narrower column truncates a legal name and the
# first thing it eats is the "(you)" saying which row is yours.
YOU_MARKER = " (you)"
NAME_COLUMN = MAX_NAME_LENGTH + len(YOU_MARKER)


def scoreboard(round_state: RoundState | None, me: str) -> list[str]:
    """One line per player, for the panel `[tab]` opens."""
    if round_state is None:
        return ["no round yet"]

    rows = [f"ROUND {round_state.number} - {round_state.phase.upper()}", ""]
    rows.append(f"{'player':<{NAME_COLUMN}} {'best':<24} {'troops':>6}  {'phase':<7}")
    for player in round_state.players:
        name = f"{player.name}{YOU_MARKER}" if player.player_id == me else player.name
        best = player.best.summary() if player.best is not None else "no submission"
        mark = "done" if player.done else "thinking"
        rows.append(f"{name[:NAME_COLUMN]:<{NAME_COLUMN}} {best[:24]:<24} {player.troops:>6}  {mark:<7}")
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
