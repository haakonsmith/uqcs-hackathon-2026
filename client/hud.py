"""The two lines under the board: what phase it is, and what to press.

Split out of `render` because it is the only part of the screen that reads the
round rather than the terrain, and it changes every second while the rest of a
frame changes hardly at all.
"""

from __future__ import annotations

import blessed

from client.viewport import Viewport
from protocol.rounds import BattleReport, Phase, RoundState, Verdict
from protocol.terrain import WorldMap

# What each phase lets you do, in the order you would reach for it.
KEYS: dict[Phase, str] = {
    "submitting": "[s]ubmit  [f]inished",
    "allocating": "[n]ext territory  [space] place 1  [p] place all  [f]inished",
    "moving": "[n]ext  [m] source then target  [1-9] count  [c]ancel orders  [f]inished",
}


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
    elif round_state.phase == "allocating" and mine is not None:
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


def verdict_popup(verdict: Verdict, round_state: RoundState | None, me: str) -> list[str]:
    """The submission result, as something that has to be dismissed."""
    headline = "ACCEPTED" if verdict.perfect else "NOT YET"
    lines = [headline, "", f"{verdict.passed} of {verdict.total} tests passed"]
    if verdict.error:
        lines.append(verdict.error)

    mine = round_state.player(me) if round_state is not None else None
    if mine is not None:
        lines.append(f"attempt {mine.submissions}")
    if verdict.perfect:
        lines += ["", "everyone else now has a minute"]
    else:
        lines += ["", "press [s] to try again"]
    return [*lines, "", "[any key] back to the board"]


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


def render_panel(term: blessed.Terminal, lines: list[str]) -> str:
    """A boxed overlay in the middle of the board, for the scoreboard."""
    width = max(len(line) for line in lines) + 4
    width = min(width, term.width)
    rows = ["", *lines, ""]
    top = max(0, (term.height - len(rows)) // 2)
    left = max(0, (term.width - width) // 2)

    out: list[str] = []
    for index, text in enumerate(rows):
        y = top + index
        if y >= term.height:
            break
        out.append(term.move_xy(left, y) + term.bold_white_on_blue(text.center(width)[:width]))
    return "".join(out)
