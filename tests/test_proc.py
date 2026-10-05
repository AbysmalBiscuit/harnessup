import time

from harnessup.proc import Deadline, run


def test_run_shell_string(tmp_path):
    out = run("echo hi; echo err >&2; exit 3", cwd=tmp_path, timeout=5)
    assert out.returncode == 3 and "hi" in out.output and "err" in out.output
    assert out.describe().startswith("exit 3: ")


def test_run_timeout_kills_children(tmp_path):
    started = time.monotonic()
    out = run("sleep 30 & wait", cwd=tmp_path, timeout=0.5)
    assert out.returncode is None and out.describe().startswith("timed out")
    assert time.monotonic() - started < 5


def test_run_missing_executable(tmp_path):
    assert (
        run(["no-such-binary-xyz"], cwd=tmp_path, timeout=5).describe() == "not found"
    )


def test_output_keeps_last_20_lines(tmp_path):
    out = run("seq 1 50", cwd=tmp_path, timeout=5)
    assert out.output.splitlines() == [str(i) for i in range(31, 51)]


def test_deadline_clamps():
    now = [0.0]
    deadline = Deadline(10, clock=lambda: now[0])
    assert deadline.clamp(120) == 10
    now[0] = 11
    assert deadline.expired and deadline.remaining() == 0
