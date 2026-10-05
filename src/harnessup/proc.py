import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Outcome:
    returncode: int | None
    output: str
    error: str = ""
    full_output: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def describe(self) -> str:
        if self.ok:
            return ""
        reason = self.error or f"exit {self.returncode}"
        return f"{reason}: {self.output}" if self.output else reason


def run(
    cmd: Sequence[str] | str,
    *,
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> Outcome:
    argv = ["sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)
    if timeout <= 0:
        return Outcome(None, "", "timed out")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        return Outcome(None, "", "not found")
    except OSError as error:
        return Outcome(None, str(error), "could not start")
    try:
        output, _ = process.communicate(timeout=timeout)
        return Outcome(
            process.returncode, "\n".join(output.splitlines()[-20:]), full_output=output
        )
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = process.communicate()
        return Outcome(None, "\n".join(output.splitlines()[-20:]), "timed out")


class Deadline:
    def __init__(
        self, seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.clock = clock
        self.end = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self.end - self.clock())

    @property
    def expired(self) -> bool:
        return self.remaining() == 0

    def clamp(self, timeout: float) -> float:
        return min(timeout, self.remaining())
