"""Turning a submitted solution into a verdict.

`Judge` is the seam. The round engine only ever asks a judge for a verdict, so
how solutions are actually run is a decision that can be made later without
touching any of the round logic.

`PlaceholderJudge` is what ships today and it **does not execute anything**.
Running a stranger's Python inside the process that owns the game state would
hand them the board, the socket list and the host; a stub that is merely
convenient is exactly how that ends up shipped by accident. It scores by
looking for the markers described on `PlaceholderJudge`, which is enough to
drive a real round end to end.

`SubprocessJudge` is the shape the real one takes, and deliberately refuses to
run until it is finished - see its docstring for what "finished" has to mean.
"""

from __future__ import annotations

import logging
from typing import Protocol

from protocol.rounds import Problem, Verdict
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
    """The real judge: run the solution somewhere it cannot reach the server.

    Not implemented, and it raises rather than falling back to something
    weaker, because a judge that silently degrades to running code in-process
    is the failure that matters here.

    Finishing it means all of:

    - a separate process, not a thread and not `exec` - the round engine must
      survive a solution that segfaults or never returns
    - a wall-clock timeout per test case, enforced by killing the process
      rather than by asking it to stop
    - memory and file-descriptor caps (`resource.setrlimit` in a `preexec_fn`,
      or a container), so one submission cannot take the host down
    - no network, no filesystem outside a scratch directory, and no
      environment inherited from the server process
    - an unprivileged user, and ideally a container or jail, because CPython
      offers no in-process sandbox worth trusting
    - the submission's stdout captured and compared, never `eval`'d

    Until every one of those holds, `PlaceholderJudge` is the honest choice.
    """

    async def evaluate(self, problem: Problem, code: str) -> Verdict:
        raise NotImplementedError(
            "SubprocessJudge is a placeholder for a sandboxed runner; "
            "see its docstring for what it has to do before it can be used"
        )


def compare(case: TestCase, output: str) -> bool:
    """Whether one captured stdout matches a case, ignoring trailing space."""
    return output.strip() == case.expected.strip()
