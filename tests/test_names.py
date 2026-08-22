"""What a player may call themselves, and who decides.

A name is the one piece of player-supplied text that gets drawn into everybody
else's fixed-width panels. The rule lives in `protocol.lobby` so both ends
apply the same one; these are the cases that say what it is.
"""

from __future__ import annotations

import argparse

import pytest

from client.hud import NAME_COLUMN, YOU_MARKER, scoreboard
from client.menu import checked_username
from protocol import Join, Joined, JoinRejected
from protocol.lobby import MAX_NAME_LENGTH, clean_name
from protocol.rounds import PlayerRound, RoundState
from server.server import Server

# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Haakon", "Haakon"),
        ("  padded  ", "padded"),
        ("spaced    out", "spaced out"),
        pytest.param("line\nbreak", "line break", id="a-newline-is-a-word-break-not-a-deletion"),
        pytest.param("tab\there", "tab here", id="so-is-a-tab"),
        pytest.param("a" * 40, "a" * MAX_NAME_LENGTH, id="over-long-is-cut-to-the-limit"),
        pytest.param("\x1b[31mred", "[31mred", id="an-escape-sequence-becomes-literal-text"),
        pytest.param("nul\x00byte", "nulbyte", id="unprintables-inside-a-word-are-dropped"),
        ("", ""),
        ("   ", ""),
        ("\x00\x1b", ""),
    ],
)
def test_clean_name(raw: str, expected: str) -> None:
    assert clean_name(raw) == expected


def test_a_cut_never_leaves_a_trailing_space() -> None:
    """Otherwise the name renders with a gap and the column looks misaligned."""
    cleaned = clean_name("a" * (MAX_NAME_LENGTH - 1) + " tail")
    assert cleaned == "a" * (MAX_NAME_LENGTH - 1)
    assert cleaned == cleaned.rstrip()


def test_cleaning_is_idempotent() -> None:
    for raw in ("Haakon", "  spaced   out  ", "a" * 40, "line\nbreak"):
        once = clean_name(raw)
        assert clean_name(once) == once


def test_no_clean_name_is_longer_than_the_limit() -> None:
    for raw in ("a" * 500, "\t".join("word" for _ in range(50)), "  " + "x" * 99):
        assert len(clean_name(raw)) <= MAX_NAME_LENGTH


# --------------------------------------------------------------------------
# The server has the last word
# --------------------------------------------------------------------------


class FakeSocket:
    """Enough of a websocket for `handle_request` to answer over."""

    id = "fake"

    async def send(self, message: str) -> None:
        pass


async def _join(server: Server, name: str) -> Joined | JoinRejected:
    return await server.handle_request(FakeSocket(), Join(room="lobby", name=name))  # pyright: ignore[reportArgumentType]


async def test_a_long_name_is_shortened_rather_than_refused() -> None:
    response = await _join(Server(), "a" * 40)
    assert isinstance(response, Joined)
    (player,) = response.lobby.players
    assert player.name == "a" * MAX_NAME_LENGTH


async def test_a_name_with_nothing_printable_is_turned_away() -> None:
    server = Server()
    response = await _join(server, "   \x00  ")
    assert isinstance(response, JoinRejected)
    assert "printable" in response.reason
    assert not server.players, "a refused join must not leave a player behind"


async def test_a_control_character_does_not_reach_the_roster() -> None:
    """A name is drawn straight into other players' panels; an escape sequence
    in one is an instruction to their terminal rather than text on it."""
    response = await _join(Server(), "evil\x1b[2Jname")
    assert isinstance(response, Joined)
    (player,) = response.lobby.players
    assert "\x1b" not in player.name
    assert player.name == "evil[2Jname"


# --------------------------------------------------------------------------
# The column the limit was chosen for
# --------------------------------------------------------------------------


def test_the_longest_legal_name_fits_beside_the_you_marker() -> None:
    me = "11111111-1111-1111-1111-111111111111"
    longest = "a" * MAX_NAME_LENGTH
    state = RoundState(
        number=1,
        phase="commanding",
        seconds_left=5.0,
        players=[PlayerRound(player_id=me, name=longest, troops=3)],
    )
    row = scoreboard(state, me)[-1]
    assert row.startswith(longest + YOU_MARKER), "the marker saying which row is yours must survive"
    assert NAME_COLUMN >= MAX_NAME_LENGTH + len(YOU_MARKER)


# --------------------------------------------------------------------------
# The command line refuses early rather than surprising the player later
# --------------------------------------------------------------------------


def test_the_name_flag_rejects_what_the_server_would() -> None:
    assert checked_username("  Haakon  ") == "Haakon"
    with pytest.raises(argparse.ArgumentTypeError, match="printable character"):
        _ = checked_username("   ")
    with pytest.raises(argparse.ArgumentTypeError, match=f"at most {MAX_NAME_LENGTH}"):
        _ = checked_username("a" * 40)
