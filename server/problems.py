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
            id="remove-duplicates",
            title="Remove Duplicates",
            statement=(
                "Read in a list of integers separated by spaces. Print the list with all duplicate values removed."
            ),
            signature="stdin: 'a c c d e' -> stdout: 'a c d e'",
            examples=[("5 5 8 5 5", "5 8"), ("1 2 3 4 4", "1 2 3 4")],
        ),
        tests=(
            TestCase("23 83 43 43 43", "23 83 43"),
            TestCase("1 2 3", "1 2 3"),
            TestCase("1 1 1 1", "1"),
            TestCase("5 5 8 5 5", "5 8"),
            TestCase("9 0 10 233", "9 0 10 233"),
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
            id="EZ 011",
            title="Vowel Vacuum",
            statement="Read a single lowercase word. Print a new string containing only the vowels (a, e, i, o, u) from the original word in the order they appeared. If there are no vowels, print an empty line.",
            signature="stdin: one string -> stdout: one string",
            examples=[("programming", "oai")],
        ),
        tests=(
            TestCase("programming", "oai"),
            TestCase("apple", "ae"),
            TestCase("rhythm", ""),
            TestCase("beautiful", "eauiu"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 012",
            title="Pizza Party Leftovers",
            statement="Read two space-separated integers: the total number of pizza slices S, and the number of people P. Everyone must get the exact same amount of whole slices. Print the number of slices left over.",
            signature="stdin: two space-separated integers -> stdout: one integer",
            examples=[("15 4", "3")],
        ),
        tests=(
            TestCase("15 4", "3"),
            TestCase("8 8", "0"),
            TestCase("5 10", "5"),
            TestCase("100 3", "1"),
        ),
    ),
    Entry(
        problem=Problem(
            id="MED 001",
            title="Adjacent Max Gap",
            statement="Read a space-separated list of integers. Find and print the maximum absolute difference between any two directly adjacent numbers in the list.",
            signature="stdin: space-separated integers -> stdout: one integer",
            examples=[("10 15 12 20", "8")],
        ),
        tests=(
            TestCase("10 15 12 20", "8"),
            TestCase("5 5 5 5", "0"),
            TestCase("-5 5 -5", "10"),
            TestCase("100 1 50", "99"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 013",
            title="Mirror Mirror",
            statement="Read a single lowercase word. Print 'TRUE' if it is a palindrome (reads exactly the same forwards and backwards) and 'FALSE' otherwise.",
            signature="stdin: one string -> stdout: 'TRUE' or 'FALSE'",
            examples=[("racecar", "TRUE")],
        ),
        tests=(
            TestCase("racecar", "TRUE"),
            TestCase("python", "FALSE"),
            TestCase("a", "TRUE"),
            TestCase("radar", "TRUE"),
            TestCase("hello", "FALSE"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 015",
            title="Word Counter",
            statement="Read a single line of text containing words separated by spaces. Print the total number of words in the line.",
            signature="stdin: one string -> stdout: one integer",
            examples=[("The quick brown fox", "4")],
        ),
        tests=(
            TestCase("The quick brown fox", "4"),
            TestCase("Hello", "1"),
            TestCase("A B C D E F G", "7"),
            TestCase("Coding is fun", "3"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 016",
            title="Swap Ends",
            statement="Read a single word. Swap its first and last characters and print the resulting word. If the word consists of only one character, print it unchanged.",
            signature="stdin: one string -> stdout: one string",
            examples=[("apple", "eppla")],
        ),
        tests=(
            TestCase("apple", "eppla"),
            TestCase("cat", "tac"),
            TestCase("python", "nythop"),
            TestCase("a", "a"),
            TestCase("it", "ti"),
        ),
    ),
    Entry(
        problem=Problem(
            id="MED 003",
            title="Second Largest",
            statement="Read a space-separated list of at least two unique integers. Find and print the second largest integer in the list.",
            signature="stdin: space-separated integers -> stdout: one integer",
            examples=[("10 5 8 20", "10")],
        ),
        tests=(
            TestCase("10 5 8 20", "10"),
            TestCase("1 2 3 4", "3"),
            TestCase("-5 -10 -1", "-5"),
            TestCase("100 99", "99"),
            TestCase("42 7 19 88 105", "88"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 017",
            title="Valid Triangle",
            statement="Read three space-separated integers representing the side lengths of a triangle. Print 'VALID' if they can form a valid triangle (the sum of any two sides must be strictly greater than the third), otherwise print 'INVALID'.",
            signature="stdin: three space-separated integers -> stdout: 'VALID' or 'INVALID'",
            examples=[("3 4 5", "VALID")],
        ),
        tests=(
            TestCase("3 4 5", "VALID"),
            TestCase("1 10 1", "INVALID"),
            TestCase("5 5 5", "VALID"),
            TestCase("2 2 4", "INVALID"),
        ),
    ),
    Entry(
        problem=Problem(
            id="MED 004",
            title="Anagram Check",
            statement="Read two space-separated words. Print 'YES' if they are exact anagrams of each other (contain the exact same letters in any order), otherwise print 'NO'.",
            signature="stdin: two space-separated strings -> stdout: 'YES' or 'NO'",
            examples=[("listen silent", "YES")],
        ),
        tests=(
            TestCase("listen silent", "YES"),
            TestCase("hello world", "NO"),
            TestCase("rat tar", "YES"),
            TestCase("apple pale", "NO"),
            TestCase("dusty study", "YES"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 018",
            title="Target Character Count",
            statement="Read a single character and a word separated by a space. Print the number of times that specific character appears in the word. This is case-sensitive.",
            signature="stdin: a character and a string separated by a space -> stdout: one integer",
            examples=[("a banana", "3")],
        ),
        tests=(
            TestCase("a banana", "3"),
            TestCase("z zebra", "1"),
            TestCase("x python", "0"),
            TestCase("l hello", "2"),
            TestCase("T Test", "1"),
        ),
    ),
    Entry(
        problem=Problem(
            id="MED 005",
            title="Missing Number",
            statement="Read an integer N on the first line. On the second line, read a space-separated list of N-1 unique integers ranging from 1 to N. Find and print the missing number.",
            signature="stdin: integer N, newline, N-1 space-separated integers -> stdout: one integer",
            examples=[("5\n1 2 4 5", "3")],
        ),
        tests=(
            TestCase("5\n1 2 4 5", "3"),
            TestCase("3\n3 1", "2"),
            TestCase("10\n1 2 3 4 5 6 7 8 9", "10"),
            TestCase("2\n2", "1"),
        ),
    ),
    Entry(
        problem=Problem(
            id="EZ 014",
            title="Sum of Odds",
            statement="Read a space-separated list of integers. Print the sum of all the odd integers in the list. If there are no odd integers, print 0.",
            signature="stdin: space-separated integers -> stdout: one integer",
            examples=[("1 2 3 4 5", "9")],
        ),
        tests=(
            TestCase("1 2 3 4 5", "9"),
            TestCase("2 4 6 8", "0"),
            TestCase("-1 -2 -3", "-4"),
            TestCase("10 11 12 13", "24"),
        ),
    ),
    Entry(
        problem=Problem(
            id="MED 002",
            title="Unique Char Counter",
            statement="Read a single continuous string. Print the number of completely unique characters in the string (characters that appear exactly once).",
            signature="stdin: one string -> stdout: one integer",
            examples=[("swiss", "2")],
        ),
        tests=(
            TestCase("swiss", "2"),
            TestCase("aabbcc", "0"),
            TestCase("abcdef", "6"),
            TestCase("engineering", "3"),
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
            id="EZ 005",
            title="K-Permutations",
            statement="Read an integer k on the first line. Read a space-separated list of numbers on the second line. Print a list of all possible k-length permutations of the given numbers, where each permutation is represented as a tuple.",
            signature="stdin: integer k, newline, space-separated integers -> stdout: string representation of a list of tuples",
            examples=[("2\n1 2 3", "[(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]")],
        ),
        tests=(
            TestCase("2\n1 2 3", "[(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)]"),
            TestCase("1\n7 8 9", "[(7,), (8,), (9,)]"),
            TestCase("3\n1 2 3", "[(1, 2, 3), (1, 3, 2), (2, 1, 3), (2, 3, 1), (3, 1, 2), (3, 2, 1)]"),
            TestCase("2\n4 4", "[(4, 4), (4, 4)]"),
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
            id="EZ 010",
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
                "Given a line of 10 characters, Claude has to pick out the # from the rest "
                "and print how many # it found. Any error in the count will result in Claude "
                "being savagely whipped."
            ),
            signature="stdin: one line of 10 characters -> stdout: one integer",
            examples=[("###d###f##", "8")],
        ),
        tests=(
            TestCase("##########", "10"),
            TestCase("#3sfbhedgh", "1"),
            TestCase("#f#d##g###", "7"),
            TestCase("##2#45#8#9", "5"),
            TestCase("eeeeeeeee ", "0"),
        ),
    ),
)


def _check_bank() -> None:
    """Refuse to start on a malformed problem rather than at the first deal.

    A `Problem` crosses the wire as pydantic; a `TestCase` is fed to
    `sandbox.run`, which encodes it. Neither is validated where it is written,
    so a list where a string belongs sits in the bank until the round it is
    dealt in - and then it takes down every connected client at once, because
    the problem travels inside `RoundChanged`. Checking at import turns that
    into a stack trace on the machine whose bank is wrong.
    """
    for entry in BANK:
        where = f"problem {entry.problem.id!r}"
        for given, wanted in entry.problem.examples:
            if not isinstance(given, str) or not isinstance(wanted, str):
                raise TypeError(f"{where}: examples must be (str, str), got ({given!r}, {wanted!r})")
        for case in entry.tests:
            if not isinstance(case.stdin, str) or not isinstance(case.expected, str):
                raise TypeError(f"{where}: TestCase takes str stdin and expected, got {case!r}")
    ids = [entry.problem.id for entry in BANK]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate problem ids in the bank: {sorted(duplicates)}")


_check_bank()

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
