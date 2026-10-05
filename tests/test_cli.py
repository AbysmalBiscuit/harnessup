from importlib.metadata import version


def test_version(run_cli, tmp_path):
    result = run_cli("--version", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == f"harnessup {version('harnessup')}"


def test_unknown_command_exits_2(run_cli, tmp_path):
    assert run_cli("bogus", cwd=tmp_path).returncode == 2
