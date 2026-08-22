"""Turning a submitted solution into a verdict.

`Judge` is the seam. The round engine only ever asks a judge for a verdict, so
how solutions are actually run is a decision that can be made later without
touching any of the round logic.

`SubprocessJudge` runs them for real, one sandboxed child process per test
case - see `server.sandbox` for exactly which isolation holds on which host.

`PlaceholderJudge` executes nothing and scores off markers in the source. It
is still here because it makes a round testable in milliseconds, and because a
host with no sandbox at all is better served by an honest fake than by running
strangers' code unprotected.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from protocol.rounds import CaseResult, CaseStatus, Problem, Verdict
from server import sandbox
from server.problems import BANK, TestCase, tests_for

logger = logging.getLogger("Judge")

# A submission longer than this is not a solution to a warm-up problem, and
# the limit costs nothing to enforce before any parsing happens.
MAX_SOURCE_BYTES = 64 * 1024

# Sandboxes allowed to run at once, across every player. Sized so a full room
# submitting into the biggest problem in the bank resolves in one wave: queued
# waves multiply the wall clock a client is waiting on, which is what makes a
# slow phase look like a dead server. Processes, not threads - the ceiling is
# the host's, so lower it on a small machine and raise `SubmitSolution`'s
# timeout to match.
MAX_EXPECTED_PLAYERS = 6
# Read off the bank rather than written down, so adding a problem with more
# cases than any before it widens the pool instead of quietly costing a wave.
DEFAULT_CONCURRENCY = MAX_EXPECTED_PLAYERS * max((len(entry.tests) for entry in BANK), default=1)


class Judge(Protocol):
    """Scores one submission against one problem."""

    async def evaluate(self, problem: Problem, code: str) -> Verdict: ...


def _too_long(code: str) -> Verdict | None:
    if len(code.encode()) > MAX_SOURCE_BYTES:
        return Verdict(passed=0, total=0, error=f"solution over {MAX_SOURCE_BYTES // 1024} KB")
    return None


class PlaceholderJudge:
    """Scores without running anything, so rounds work before the sandbox does.

    A submission is read for markers rather than executed:

        `# solved`   every test passes
        `# passes N` exactly N tests pass
        anything else scores one test per non-empty, non-comment line, capped
        at the number of tests, so ordinary typing moves the number

    This is a stand-in and says so in every verdict it returns, so a scoreboard
    driven by it can never be mistaken for a real one.
    """

    async def evaluate(self, problem: Problem, code: str) -> Verdict:
        oversized = _too_long(code)
        if oversized is not None:
            return oversized

        cases = tests_for(problem.id)
        total = len(cases)
        note = "placeholder judge - nothing was executed"

        lowered = code.lower()
        if "# solved" in lowered:
            return _pretend(total, total, note)

        marker = "# passes "
        if marker in lowered:
            _, _, rest = lowered.partition(marker)
            digits = rest.split()[0] if rest.split() else ""
            if digits.isdigit():
                return _pretend(min(int(digits), total), total, note)

        lines = [line for line in code.splitlines() if line.strip() and not line.strip().startswith("#")]
        return _pretend(min(len(lines), total), total, note)


def _pretend(passed: int, total: int, note: str) -> Verdict:
    """A verdict shaped like a real one, so the client renders it the same."""
    return Verdict(
        passed=passed,
        total=total,
        error=note,
        cases=[
            CaseResult(index=i, status="passed" if i <= passed else "wrong", detail="not executed")
            for i in range(1, total + 1)
        ],
    )


class SubprocessJudge:
    """Runs each hidden test case in a sandboxed child and compares stdout.

    A solution gets one process per test case, so a crash on case three does
    not take the other four with it, and state cannot be carried between them.

    `concurrency` bounds how many of those exist at once, across every player
    rather than per submission - twenty processes if four players hit a
    five-case problem together, and the point of the limits is undone if the
    server melts launching them.

    It has to be set against that room-wide total, not against one solution.
    Below it, submissions queue in waves: at eight slots those twenty cases are
    three waves, and a solution that times out in every case answers after
    three times `wall_seconds` rather than one. That is how a busy phase turns
    into clients giving up on the server. `DEFAULT_CONCURRENCY` covers a full
    room of the largest problem in the bank in a single wave.

    Read `sandbox.ISOLATION.summary()` before trusting this: on a host with no
    `bwrap` or `sandbox-exec` it still runs solutions in a separate process
    with CPU and file limits, but they can open sockets and write anywhere the
    server user can.
    """

    def __init__(self, limits: sandbox.Limits | None = None, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.limits: sandbox.Limits = limits or sandbox.Limits()
        self._slots: asyncio.Semaphore = asyncio.Semaphore(concurrency)

    async def evaluate(self, problem: Problem, code: str) -> Verdict:
        oversized = _too_long(code)
        if oversized is not None:
            return oversized

        cases = tests_for(problem.id)
        if not cases:
            return Verdict(passed=0, total=0, error="no tests for this problem")

        # Concurrently, and this is a correctness matter rather than a speed
        # one: run in sequence, a solution that times out on every case takes
        # `wall_seconds` x `len(cases)`, which overruns the protocol's request
        # timeout and the player is told the server died. Keep `concurrency`
        # at or above the largest problem's case count and the worst case
        # stays near a single `wall_seconds`.
        results = await asyncio.gather(*(self._run(code, index, case) for index, case in enumerate(cases, start=1)))

        passed = sum(1 for case in results if case.ok)
        # Results come back in case order, so the headline is the earliest
        # failure rather than whichever lost a race.
        failure = next((case for case in results if not case.ok), None)
        return Verdict(
            passed=passed,
            total=len(cases),
            error=None if failure is None else f"test {failure.index}: {failure.label()}",
            cases=list(results),
        )

    async def _run(self, code: str, index: int, case: TestCase) -> CaseResult:
        """One test case in its own sandbox."""
        started = time.perf_counter()
        async with self._slots:
            result = await sandbox.run(code, case.stdin, self.limits)
        elapsed = round((time.perf_counter() - started) * 1000)

        def outcome(status: CaseStatus, detail: str = "") -> CaseResult:
            return CaseResult(index=index, status=status, detail=detail, milliseconds=elapsed)

        if result.timed_out:
            return outcome("timeout", f"killed after {self.limits.wall_seconds:.0f}s")
        if result.exit_code != 0:
            # Their own traceback, which is exactly what they need to see.
            return outcome("crashed", _last_line(result.stderr))
        if result.truncated:
            return outcome("flooded", f"over {self.limits.output_bytes // 1024} KB of output")
        if compare(case, result.stdout):
            return outcome("passed")
        # Deliberately no expected value: that would hand over the answer.
        return outcome("wrong", f"printed {_shorten(result.stdout)}")


def _shorten(text: str, width: int = 40) -> str:
    """One line of a solution's output, for a side-by-side that fits."""
    flat = " ".join(text.split())
    return f"{flat[:width]}..." if len(flat) > width else flat or "nothing"


def _last_line(text: str) -> str:
    """The line a traceback ends on, which is the one that says what broke."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:200] if lines else "failed with no output"


def compare(case: TestCase, output: str) -> bool:
    """Whether one captured stdout matches a case, ignoring trailing space.

    Line by line rather than as one blob. Stripping only the ends of the whole
    output lets a trailing space midway through a multi-line answer fail a
    solution that printed the right thing, which is the least explicable way to
    lose a round: the client shows both as identical because a terminal cannot
    draw the difference.
    """
    return _normalise(output) == _normalise(case.expected)


def _normalise(text: str) -> list[str]:
    """Output as comparable lines: no surrounding blank, no trailing space.

    The outer `strip` is what the whole-blob comparison used to do on its own,
    kept so nothing that passed before starts failing. Indentation part-way
    through an answer still counts - a solution printing a shape is printing
    the spaces on purpose.
    """
    lines = [line.rstrip() for line in text.strip().splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines
