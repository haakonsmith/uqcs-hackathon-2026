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
            statement=("Read an integer n, then n integers. Print how many unordered pairs of them sum to exactly 100."),
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
                "Read one line of lowercase letters. Print its run-length encoding: each run as the character followed by its length, with no separators."
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
            id="EZ 001",
            title="Echo Echo",
            statement="Read a single word from input. Print it back three times separated by spaces.",
            signature="stdin: one string -> stdout: three space-separated strings",
            examples=[("Hello", "Hello Hello Hello")],
        ),
        tests=(
            TestCase("Hello", "Hello Hello Hello"),
            TestCase("Python", "Python Python Python"),
            TestCase("A", "A A A"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 002",
            title="Next Integer",
            statement="Read a single integer N. Print the number that comes immediately after it (N + 1).",
            signature="stdin: one integer -> stdout: one integer",
            examples=[("5", "6")],
        ),
        tests=(
            TestCase("5", "6"),
            TestCase("-1", "0"),
            TestCase("99", "100"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 003",
            title="Secret Password Check",
            statement="Read a string. If it matches exactly 'let-me-in', print 'ACCESS GRANTED'. Otherwise, print 'ACCESS DENIED'.",
            signature="stdin: one string -> stdout: 'ACCESS GRANTED' or 'ACCESS DENIED'",
            examples=[("let-me-in", "ACCESS GRANTED")],
        ),
        tests=(
            TestCase("let-me-in", "ACCESS GRANTED"),
            TestCase("password123", "ACCESS DENIED"),
            TestCase("LET-ME-IN", "ACCESS DENIED"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 004",
            title="Full Name Joiner",
            statement="Read a first name and a last name separated by a space. Print them back separated by a comma and a space in the format 'LastName, FirstName'.",
            signature="stdin: two space-separated strings -> stdout: one combined string",
            examples=[("John Smith", "Smith, John")],
        ),
        tests=(
            TestCase("John Smith", "Smith, John"),
            TestCase("Alice Green", "Green, Alice"),
            TestCase("A B", "B, A"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 005",
            title="Double Trouble",
            statement="Read a single integer N. Print its value multiplied by 2.",
            signature="stdin: one integer -> stdout: one integer",
            examples=[("10", "20")],
        ),
        tests=(
            TestCase("10", "20"),
            TestCase("0", "0"),
            TestCase("-5", "-10"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 006",
            title="Length Checker",
            statement="Read a single word. Print the total number of characters in that word.",
            signature="stdin: one string -> stdout: one integer",
            examples=[("apple", "5")],
        ),
        tests=(
            TestCase("apple", "5"),
            TestCase("a", "1"),
            TestCase("programming", "11"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 007",
            title="Is It Positive?",
            statement="Read a single integer N. Print 'YES' if N is strictly greater than 0, otherwise print 'NO'.",
            signature="stdin: one integer -> stdout: 'YES' or 'NO'",
            examples=[("5", "YES")],
        ),
        tests=(
            TestCase("5", "YES"),
            TestCase("-3", "NO"),
            TestCase("0", "NO"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 008",
            title="Scream Converter",
            statement="Read a lowercase word. Convert all of its letters to uppercase and add an exclamation mark at the end.",
            signature="stdin: one string -> stdout: one string",
            examples=[("hello", "HELLO!")],
        ),
        tests=(
            TestCase("hello", "HELLO!"),
            TestCase("run", "RUN!"),
            TestCase("a", "A!"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 009",
            title="Two Sum Baseline",
            statement="Read two space-separated integers, A and B. Print their sum (A + B).",
            signature="stdin: two space-separated integers -> stdout: one integer",
            examples=[("5 7", "12")],
        ),
        tests=(
            TestCase("5 7", "12"),
            TestCase("0 0", "0"),
            TestCase("-3 8", "5"),
            TestCase("-10 -20", "-30"),
        ),
    ),
    Entry(
            problem=Problem(
                id="claude",
                title="Whipping Claude",
                statement=(
                    "Given an array of 10 chars claude has to pick out the # from the rest of the chars"
                    "and return the number of # it found. Any error in count will result in claude being"
                    "savagely whipped."
                ),
                signature="stdin: array -> stdout: one integer",
                examples=[(['#','#','#','d','#','#','#','f','#','#'], "8")],
            ),
            tests=(
                TestCase(['#','#','#','#','#','#','#','#','#','#'], "10"),
                TestCase(['#','3','s','f','b','h','e','d','g','h'], "1"),
                TestCase(['#','f','#','d','#','#','g','#','#','#'], "7"),
                TestCase(['#','#','2','#','4','5','#','8','#','9'], "5"),
                TestCase(['e','e','e','e','e','e','e','e','e',' '], "0"),
            )
        )
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
