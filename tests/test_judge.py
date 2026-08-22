"""Scoring a submission: what counts as matching, and what is refused outright.

`compare` is the whole difference between a correct solution and a lost round,
and it is the one piece of the judge with no sandbox in the way of testing it.
"""

from __future__ import annotations

import pytest

from server.judge import DEFAULT_CONCURRENCY, MAX_SOURCE_BYTES, PlaceholderJudge, compare

# Aliased away from the `test` prefix: pytest collects module-level names
# that start with it, and tries to run them as tests.
from server.problems import BANK
from server.problems import TestCase as Case
from server.problems import tests_for as hidden_tests


def case(expected: str) -> Case:
    return Case(stdin="", expected=expected)


# --------------------------------------------------------------------------
# Matching output
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "printed"),
    [
        ("42", "42"),
        ("42", "42\n"),
        ("42", "  42  "),
        ("a\nb", "a\nb"),
        ("a\nb", "a\nb\n\n\n"),
        pytest.param("a\nb", "a   \nb", id="trailing-space-mid-answer"),
        pytest.param("a\nb", "a\nb   ", id="trailing-space-last-line"),
    ],
)
def test_output_that_should_match(expected: str, printed: str) -> None:
    assert compare(case(expected), printed)


@pytest.mark.parametrize(
    ("expected", "printed"),
    [
        ("42", "43"),
        ("42", ""),
        ("a\nb", "a\nc"),
        pytest.param("a\n\nb", "a\nb", id="a-missing-blank-line-is-a-different-answer"),
        pytest.param("a b", "ab", id="spacing-within-a-line-still-matters"),
        pytest.param("1\n2", "2\n1", id="order-matters"),
    ],
)
def test_output_that_should_not_match(expected: str, printed: str) -> None:
    assert not compare(case(expected), printed)


# --------------------------------------------------------------------------
# Refusals that never reach a sandbox
# --------------------------------------------------------------------------


async def test_an_oversized_solution_is_refused_without_running() -> None:
    problem = BANK[0].problem
    verdict = await PlaceholderJudge().evaluate(problem, "x" * (MAX_SOURCE_BYTES + 1))
    assert verdict.passed == 0
    assert verdict.error is not None and "KB" in verdict.error


async def test_the_placeholder_honours_its_markers() -> None:
    problem = BANK[0].problem
    total = len(hidden_tests(problem.id))
    solved = await PlaceholderJudge().evaluate(problem, "# solved")
    assert solved.passed == total and solved.perfect

    partial = await PlaceholderJudge().evaluate(problem, "# passes 2")
    assert partial.passed == 2 and not partial.perfect


async def test_a_verdict_reports_one_case_per_hidden_test() -> None:
    problem = BANK[0].problem
    verdict = await PlaceholderJudge().evaluate(problem, "# passes 1")
    assert [c.index for c in verdict.cases] == list(range(1, len(hidden_tests(problem.id)) + 1))


# --------------------------------------------------------------------------
# The bank itself
# --------------------------------------------------------------------------


def test_every_problem_resolves_to_its_own_tests() -> None:
    """Ids are the only link between a problem and its answers.

    A duplicate silently wins the `_BY_ID` lookup, so one problem is posed and
    another's tests are what a solution is judged against - a round nobody can
    win and no error anywhere to say why.
    """
    for entry in BANK:
        assert hidden_tests(entry.problem.id) is entry.tests, entry.problem.id


def test_problem_ids_are_unique() -> None:
    ids = [entry.problem.id for entry in BANK]
    assert len(ids) == len(set(ids))


def test_every_problem_has_tests_and_an_example() -> None:
    for entry in BANK:
        assert entry.tests, f"{entry.problem.id} would score every solution 0/0"
        assert entry.problem.examples, f"{entry.problem.id} poses a question with nothing to go on"


def test_the_worked_examples_agree_with_the_hidden_tests() -> None:
    """An example that contradicts the tests teaches the wrong answer."""
    for entry in BANK:
        by_stdin = {c.stdin: c.expected for c in entry.tests}
        for given, wanted in entry.problem.examples:
            if given in by_stdin:
                assert by_stdin[given] == wanted, f"{entry.problem.id}: example {given!r} disagrees with its test"


def test_there_are_enough_judge_slots_for_a_full_room_of_the_biggest_problem() -> None:
    assert DEFAULT_CONCURRENCY >= max(len(entry.tests) for entry in BANK)
