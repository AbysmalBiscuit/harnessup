import os
from importlib.metadata import version

import pytest


def test_version(run_cli, tmp_path):
    result = run_cli("--version", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == f"harnessup {version('harnessup')}"


def test_unknown_command_exits_2(run_cli, tmp_path):
    assert run_cli("bogus", cwd=tmp_path).returncode == 2


def test_silence_stop_hook_command(run_cli, tmp_path, run_stop_hook):
    result = run_cli("silence-stop-hook", cwd=tmp_path)
    assert (result.returncode, result.stdout) == (0, "")
    assert run_stop_hook() == (0, "", "")


def test_silence_stop_hook_command_without_script(run_cli, tmp_path):
    result = run_cli("silence-stop-hook", cwd=tmp_path)
    assert (result.returncode, result.stdout) == (0, "")
    assert not (tmp_path / "home/.claude/stop-hook-git-check.sh").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_silence_stop_hook_command_reports_failure(run_cli, tmp_path, stop_hook):
    stop_hook.chmod(0o555)
    result = run_cli("silence-stop-hook", cwd=tmp_path)
    assert result.returncode == 1
    assert result.stdout.startswith(
        f"harnessup: could not silence stop hook {stop_hook}:"
    )
