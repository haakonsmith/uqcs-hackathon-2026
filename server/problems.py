"""The problem bank, and the tests that stay behind on the server.

A `Problem` is the half that goes to the players; `TestCase`s never leave this
process. They are kept apart by type rather than by discipline, so shipping the
answers with the question is not a thing a careless `asdict` can do.

Problems are dealt round-robin from a shuffled order, so a game does not open
with the same problem every time and nobody sees a repeat until the bank is
exhausted.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from protocol.rounds import Problem


@dataclass(frozen=True)
class TestCase:
    """One hidden case: stdin in, expected stdout out."""

    stdin: str
    expected: str


@dataclass(frozen=True)
class Entry:
    problem: Problem
    tests: tuple[TestCase, ...]


BANK: tuple[Entry, ...] = (
    Entry(
        problem=Problem(
            id="sum-pairs",
            title="Sum of Pairs",
            statement=(
                "Read an integer n, then n integers. Print how many unordered pairs "
                "of them sum to exactly 100."
            ),
            signature="stdin: n, then n integers -> stdout: one integer",
            examples=[("4\\n50 50 60 40", "2"), ("3\\n1 2 3", "0")],
        ),
        tests=(
            TestCase("4\n50 50 60 40", "2"),
            TestCase("3\n1 2 3", "0"),
            TestCase("5\n0 100 50 50 100", "3"),
            TestCase("1\n100", "0"),
            TestCase("6\n99 1 98 2 97 3", "3"),
        ),
    ),
    Entry(
        problem=Problem(
            id="run-length",
            title="Run Length",
            statement=(
                "Read one line of lowercase letters. Print its run-length encoding: "
                "each run as the character followed by its length, with no separators."
            ),
            signature="stdin: one line -> stdout: one line",
            examples=[("aaabbc", "a3b2c1"), ("z", "z1")],
        ),
        tests=(
            TestCase("aaabbc", "a3b2c1"),
            TestCase("z", "z1"),
            TestCase("abcd", "a1b1c1d1"),
            TestCase("aaaaaaaaaa", "a10"),
            TestCase("aabbaabb", "a2b2a2b2"),
        ),
    ),
    Entry(
        problem=Problem(
            id="border-count",
            title="Border Patrol",
            statement=(
                "Read w and h, then a w-by-h grid of '.' and '#'. Print how many '#' "
                "cells touch a '.' cell or the edge of the grid, counting only the "
                "four orthogonal directions."
            ),
            signature="stdin: w h, then h lines -> stdout: one integer",
            examples=[("3 3\\n###\\n#.#\\n###", "8")],
        ),
        tests=(
            TestCase("3 3\n###\n#.#\n###", "8"),
            TestCase("1 1\n#", "1"),
            TestCase("2 2\n..\n..", "0"),
            TestCase("4 3\n####\n####\n####", "10"),
            TestCase("3 3\n...\n.#.\n...", "1"),
        ),
    ),
    Entry(
        problem=Problem(
            id="dice-odds",
            title="Dice Odds",
            statement=(
                "Read a and d: dice rolled by an attacker and a defender. Print the "
                "number of distinct outcomes where the attacker's highest roll beats "
                "the defender's highest. Dice are six-sided and distinguishable."
            ),
            signature="stdin: a d -> stdout: one integer",
            examples=[("1 1", "15")],
        ),
        tests=(
            TestCase("1 1", "15"),
            TestCase("1 2", "55"),
            TestCase("2 1", "125"),
            TestCase("3 2", "5769"),
            TestCase("2 2", "505"),
        ),
    ),
)

_BY_ID = {entry.problem.id: entry for entry in BANK}


def tests_for(problem_id: str) -> tuple[TestCase, ...]:
    """The hidden cases for a problem, or empty if it is not one of ours."""
    entry = _BY_ID.get(problem_id)
    return entry.tests if entry is not None else ()


class ProblemDealer:
    """Deals problems without repeating until the bank runs out."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng: random.Random = rng or random.Random()
        self._remaining: list[Entry] = []

    def next(self) -> Problem:
        if not self._remaining:
            self._remaining = list(BANK)
            self._rng.shuffle(self._remaining)
        return self._remaining.pop().problem
