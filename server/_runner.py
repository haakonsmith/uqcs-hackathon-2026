"""Child process entry point. Applies its own limits, then runs the solution.

Kept separate from the parent so that nothing in the game server is imported
into the process that runs a stranger's code - not the board, not the socket
list, not the player roster.

Limits are set here rather than with `preexec_fn` on the parent side, because
`preexec_fn` runs between fork and exec in a process that may hold locks from
other threads, and CPython warns against it. Setting them in the child, before
the solution is loaded, is both portable and easier to reason about: the soft
and hard limits are set to the same value, so the solution cannot raise them
back afterwards.

Invoked as:

    python -I -B _runner.py CPU_SECONDS FILE_BYTES OPEN_FILES MEMORY_BYTES SOLUTION
"""

from __future__ import annotations

import os
import runpy
import sys

try:
  import resource
except ImportError:
  resource = None

# Exit code used when the runner itself refuses to start, to tell a crash in
# the harness apart from a crash in the solution.
HARNESS_ERROR = 111


def clamp(name: str, value: int) -> bool:
    """Set a limit as low as asked, hard as well as soft. True if it took.

    Skips, quietly, any limit the platform does not really implement: macOS
    accepts `RLIMIT_AS` and `RLIMIT_DATA` in the API and then refuses every
    value, so treating that as fatal would mean no sandbox at all there.

    Quietly matters. This process's stderr is the solution's own diagnostic
    channel and gets shown to the player who wrote it; a note about the host's
    rlimit support on every single run would bury the traceback they need.
    `sandbox.probe()` is where the parent reports what actually holds.
    """
    if resource is None:
        return False
    
    limit = getattr(resource, name, None)
    if limit is None:
        return False
    try:
        _, hard = resource.getrlimit(limit)
        ceiling = value if hard < 0 else min(value, hard)
        resource.setrlimit(limit, (ceiling, ceiling))
    except (ValueError, OSError):
        return False
    return True


def main() -> int:
    if len(sys.argv) != 6:
        print(f"[runner] expected 5 arguments, got {len(sys.argv) - 1}", file=sys.stderr)
        return HARNESS_ERROR

    cpu_seconds, file_bytes, open_files, memory_bytes = (int(value) for value in sys.argv[1:5])
    solution = sys.argv[5]

    # No core dump: a segfaulting solution should not write a few hundred MB
    # into the scratch directory on its way out.
    _ = clamp("RLIMIT_CORE", 0)
    # CPU time is the backstop for a solution that never returns. The parent's
    # wall clock catches sleeping too, but this one costs nothing to enforce.
    _ = clamp("RLIMIT_CPU", cpu_seconds)
    _ = clamp("RLIMIT_FSIZE", file_bytes)
    _ = clamp("RLIMIT_NOFILE", open_files)
    # Address space, where the platform honours it. Linux does; macOS does not.
    _ = clamp("RLIMIT_AS", memory_bytes)

    # Deliberately not RLIMIT_NPROC: it counts every process the *user* owns,
    # not this one's children, so any value low enough to stop a fork bomb is
    # also low enough to break a developer machine that is already running
    # hundreds of processes. Forking is blocked by the sandbox profile instead.

    sys.argv = [solution]
    # The solution reads stdin and writes stdout; the harness compares those.
    # Nothing here inspects it, and nothing eval's its output.
    try:
        _ = runpy.run_path(solution, run_name="__main__")
    except SystemExit as exit_request:
        code = exit_request.code
        return code if isinstance(code, int) else 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MemoryError:
        # Out of address space: report it plainly rather than as a traceback
        # that itself needs memory to format.
        print("[runner] out of memory", file=sys.stderr)
        os._exit(1)
