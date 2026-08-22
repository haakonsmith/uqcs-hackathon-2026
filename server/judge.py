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
from typing import Protocol

from protocol.rounds import Problem, Verdict
from server import sandbox
from server.problems import TestCase, tests_for

logger = logging.getLogger("Judge")

# A submission longer than this is not a solution to a warm-up problem, and
# the limit costs nothing to enforce before any parsing happens.
MAX_SOURCE_BYTES = 64 * 1024


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
            return Verdict(passed=total, total=total, error=note)

        marker = "# passes "
        if marker in lowered:
            _, _, rest = lowered.partition(marker)
            digits = rest.split()[0] if rest.split() else ""
            if digits.isdigit():
                return Verdict(passed=min(int(digits), total), total=total, error=note)

        lines = [line for line in code.splitlines() if line.strip() and not line.strip().startswith("#")]
        return Verdict(passed=min(len(lines), total), total=total, error=note)


class SubprocessJudge:
    """Runs each hidden test case in a sandboxed child and compares stdout.

    A solution gets one process per test case, so a crash on case three does
    not take the other four with it, and state cannot be carried between them.

    `concurrency` bounds how many of those exist at once. Four players
    submitting into a five-case problem is twenty processes if nothing says
    otherwise, and the point of the limits is undone if the server melts
    launching them.

    Read `sandbox.ISOLATION.summary()` before trusting this: on a host with no
    `bwrap` or `sandbox-exec` it still runs solutions in a separate process
    with CPU and file limits, but they can open sockets and write anywhere the
    server user can.
    """

    def __init__(self, limits: sandbox.Limits | None = None, concurrency: int = 8) -> None:
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
        results = await asyncio.gather(*(self._run(code, case) for case in cases))

        passed = sum(1 for outcome in results if outcome is None)
        # Results come back in case order, so the error a player is shown is
        # always the earliest failing case rather than whichever lost a race.
        first_error = next((outcome for outcome in results if outcome is not None), None)
        return Verdict(passed=passed, total=len(cases), error=None if passed == len(cases) else first_error)

    async def _run(self, code: str, case: TestCase) -> str | None:
        """One test case in its own sandbox. None if it passed, else why not."""
        async with self._slots:
            result = await sandbox.run(code, case.stdin, self.limits)

        if result.timed_out:
            return f"timed out after {self.limits.wall_seconds:.0f}s"
        if result.exit_code != 0:
            return _last_line(result.stderr)
        if result.truncated:
            return "printed too much output"
        # Only that it was wrong, never what was expected: handing back the
        # answer would turn one wrong submission into a correct one.
        return None if compare(case, result.stdout) else "wrong answer"


def _last_line(text: str) -> str:
    """The line a traceback ends on, which is the one that says what broke."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:200] if lines else "failed with no output"


def compare(case: TestCase, output: str) -> bool:
    """Whether one captured stdout matches a case, ignoring trailing space."""
    return output.strip() == case.expected.strip()
