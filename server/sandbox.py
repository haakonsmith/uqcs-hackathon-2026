"""Running a stranger's Python somewhere it cannot reach the game server.

Three layers, and it is worth being precise about which of them actually hold,
because a sandbox that is trusted further than it goes is worse than none:

1. **A separate process.** Always. The solution never shares an interpreter
   with the board, the socket list or the player roster, and it is started
   with `-I` so nothing of this project is importable from it.

2. **Resource limits**, applied by the child before the solution loads
   (`_runner.py`). CPU time, file size, open files and core dumps hold
   everywhere. Address space holds on Linux and is silently ignored by macOS,
   which accepts `RLIMIT_AS` in the API and refuses every value.

3. **A platform sandbox**, when one is installed. `bwrap` on Linux and
   `sandbox-exec` on macOS both block network access and confine writes to a
   scratch directory. Without one, the solution can open sockets and write
   wherever the server user can. `probe()` reports which of these is true so
   the server can say so at startup rather than implying an isolation it does
   not have.

On top of that the parent enforces a wall clock by killing the whole process
group, and caps how much output it will read, so a solution cannot exhaust the
server's memory by printing at it.

What none of this stops: a solution that allocates until the machine swaps, on
a platform with no address-space limit and no container. The wall clock is what
ends that, so `wall_seconds` is a safety limit and not just a fairness one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("Sandbox")

RUNNER = Path(__file__).with_name("_runner.py")


@dataclass(frozen=True)
class Limits:
    """What one submission is allowed to consume."""

    cpu_seconds: int = 5
    # The parent kills at this, whatever the child is doing - including sleeping,
    # which CPU time does not notice.
    wall_seconds: float = 5.0
    memory_bytes: int = 512 * 1024 * 1024
    file_bytes: int = 8 * 1024 * 1024
    open_files: int = 64
    # Read no more than this from the pipes. A solution printing in a loop
    # should not be able to grow the *server's* memory.
    output_bytes: int = 256 * 1024


@dataclass(frozen=True)
class Isolation:
    """What is actually enforced here, so it can be reported honestly."""

    wrapper: str
    blocks_network: bool
    confines_writes: bool
    blocks_fork: bool
    limits_memory: bool

    def summary(self) -> str:
        holds = [
            name
            for name, ok in (
                ("network", self.blocks_network),
                ("writes", self.confines_writes),
                ("fork", self.blocks_fork),
                ("memory", self.limits_memory),
            )
            if ok
        ]
        missing = [
            name
            for name, ok in (
                ("network", self.blocks_network),
                ("writes", self.confines_writes),
                ("fork", self.blocks_fork),
                ("memory", self.limits_memory),
            )
            if not ok
        ]
        parts = [f"sandbox: {self.wrapper}"]
        parts.append("blocks " + ", ".join(holds) if holds else "blocks nothing")
        if missing:
            parts.append("DOES NOT limit " + ", ".join(missing))
        return "; ".join(parts)


@dataclass(frozen=True)
class Completed:
    """What came back from one run."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def probe() -> Isolation:
    """Work out which sandbox is available, once, at startup."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return Isolation(
            wrapper="sandbox-exec",
            blocks_network=True,
            confines_writes=True,
            blocks_fork=True,
            # macOS accepts RLIMIT_AS and then refuses every value.
            limits_memory=False,
        )
    if shutil.which("bwrap"):
        return Isolation(
            wrapper="bwrap",
            blocks_network=True,
            confines_writes=True,
            # bwrap can unshare PIDs, but forking inside is still allowed;
            # RLIMIT_AS is what stops it turning into a problem.
            blocks_fork=False,
            limits_memory=sys.platform.startswith("linux"),
        )
    return Isolation(
        wrapper="none",
        blocks_network=False,
        confines_writes=False,
        blocks_fork=False,
        limits_memory=sys.platform.startswith("linux"),
    )


ISOLATION = probe()


def _macos_profile(scratch: Path) -> str:
    """A `sandbox-exec` profile: read anything, write only to the scratch.

    Reads stay open because the interpreter has to load its own standard
    library, and narrowing that to a list of paths breaks on every Python
    install that is not the one it was written against.

    `/tmp` is a symlink to `/private/tmp` on macOS and the profile is matched
    against the resolved path, so a rule naming the unresolved one silently
    matches nothing.
    """
    return f"""(version 1)
(deny default)
(deny network*)
(deny process-fork)
(allow process-exec*)
(allow sysctl-read)
(allow mach-lookup)
(allow signal (target self))
(allow file-read*)
(allow file-write* (subpath "{scratch.resolve()}"))
"""


def _wrapper_argv(scratch: Path) -> list[str]:
    """The isolation command the interpreter is launched underneath."""
    if ISOLATION.wrapper == "sandbox-exec":
        profile = scratch / "profile.sb"
        _ = profile.write_text(_macos_profile(scratch))
        return ["sandbox-exec", "-f", str(profile)]

    if ISOLATION.wrapper == "bwrap":
        return [
            "bwrap",
            "--unshare-all",  # no network, no PID namespace sharing
            "--die-with-parent",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--bind", str(scratch.resolve()), str(scratch.resolve()),
            "--chdir", str(scratch.resolve()),
        ]

    return []


def _environment(scratch: Path) -> dict[str, str]:
    """A deliberately bare environment. The server's is not inherited."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }


async def run(source: str, stdin: str, limits: Limits | None = None) -> Completed:
    """Run `source` on `stdin` under whatever isolation this machine has."""
    limits = limits or Limits()
    scratch = Path(tempfile.mkdtemp(prefix="termination-run-"))
    try:
        solution = scratch / "solution.py"
        _ = solution.write_text(source)

        argv = [
            *_wrapper_argv(scratch),
            sys.executable,
            "-I",  # ignore PYTHON* vars, and keep the cwd off sys.path
            "-B",  # no .pyc files next to the solution
            str(RUNNER),
            str(limits.cpu_seconds),
            str(limits.file_bytes),
            str(limits.open_files),
            str(limits.memory_bytes),
            str(solution),
        ]
        return await _spawn(argv, stdin, limits, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _spawn(argv: list[str], stdin: str, limits: Limits, scratch: Path) -> Completed:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=scratch,
        env=_environment(scratch),
        # Its own process group, so a solution that spawns children can be
        # killed as a whole rather than leaving orphans behind.
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin.encode()),
            timeout=limits.wall_seconds,
        )
    except TimeoutError:
        await _kill(process)
        return Completed(stdout="", stderr="timed out", exit_code=-1, timed_out=True)
    except (BrokenPipeError, ConnectionResetError):
        # The solution exited without reading its input, which is its right.
        await _kill(process)
        return Completed(stdout="", stderr="closed its input", exit_code=-1)

    truncated = len(stdout) > limits.output_bytes or len(stderr) > limits.output_bytes
    return Completed(
        stdout=_decode(stdout[: limits.output_bytes]),
        stderr=_decode(stderr[: limits.output_bytes]),
        exit_code=process.returncode if process.returncode is not None else -1,
        truncated=truncated,
    )


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Take down the whole group, then reap it so no zombie is left."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(TimeoutError):
        _ = await asyncio.wait_for(process.wait(), timeout=2.0)


def _decode(raw: bytes) -> str:
    """Never raise on a solution's output, whatever bytes it produced."""
    return raw.decode("utf8", errors="replace")
