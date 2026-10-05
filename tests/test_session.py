import json
import shutil
import subprocess

import pytest

from harnessup.workspace import read_exclude_block


@pytest.fixture
def session_cli(repo, write_manifest, run_cli, stub):
    path = write_manifest(
        repo,
        """
        schema = 1
        [[file]]
        source = "rules.md"
        target = "AGENTS.local.md"
        [claude.settings_local]
        model = "test"
        [context]
        startup = "startup.md"
        recovery = "recovery.md"
    """,
    )
    (path.parent / "rules.md").write_text("rules")
    (path.parent / "startup.md").write_text("Read AGENTS.local.md\n")
    (path.parent / "recovery.md").write_text("Recover the ledger\n")
    stub("claude", list_json=[])
    stub("codex", list_json={"installed": [], "available": []})

    def invoke(source="startup", harness="claude", env=None, stdin=None):
        return run_cli(
            "session-start",
            "--harness",
            harness,
            cwd=repo,
            env={"CLAUDE_CODE_REMOTE": "true", **(env or {})},
            stdin=json.dumps({"source": source, "cwd": str(repo)})
            if stdin is None
            else stdin,
        )

    return invoke


def test_gate_off_prints_nothing(session_cli, repo):
    result = session_cli(env={"CLAUDE_CODE_REMOTE": "false"})
    assert result.returncode == 0 and result.stdout == ""
    assert not (repo / "AGENTS.local.md").exists()


def test_no_manifest_prints_nothing(run_cli, repo):
    result = run_cli(
        "session-start", "--harness", "claude", cwd=repo, env={"CLOUD_AGENT": "true"}
    )
    assert result.returncode == 0 and result.stdout == ""


@pytest.mark.parametrize("source", ["startup", "clear"])
def test_startup_places_and_prints(session_cli, repo, source):
    result = session_cli(source)
    assert result.returncode == 0
    assert result.stdout.startswith(
        "Read AGENTS.local.md\n\nTrack progress with TaskCreate"
    )
    assert (repo / "AGENTS.local.md").exists() and (
        repo / ".claude/settings.local.json"
    ).exists()
    assert "/AGENTS.local.md" in read_exclude_block(repo)


def test_cloud_agent_env_also_opens_gate(session_cli):
    assert session_cli(
        env={"CLAUDE_CODE_REMOTE": "false", "CLOUD_AGENT": "true"}
    ).stdout.startswith("Read AGENTS.local.md")


@pytest.mark.parametrize("source", ["resume", "compact", "fork"])
def test_recovery_sources_print_recovery_only(session_cli, repo, source):
    result = session_cli(source)
    assert result.stdout == "Recover the ledger\n"
    assert not (repo / "AGENTS.local.md").exists()


def test_codex_task_line(session_cli):
    assert "Track progress with `update_plan`" in session_cli(harness="codex").stdout


@pytest.mark.parametrize("payload", ["not json", "{}", "[]"])
def test_payload_without_source_or_cwd(session_cli, payload):
    assert session_cli(stdin=payload).stdout.startswith(
        "Read AGENTS.local.md\n\nTrack progress"
    )


def test_setup_failures_scoped_to_repo(session_cli, state_dir, repo):
    state_dir.mkdir(parents=True)
    (state_dir / "setup.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "root": str(repo),
                        "manifest_error": None,
                        "items": [
                            {
                                "kind": "tool",
                                "name": "own",
                                "harness": None,
                                "status": "failed",
                                "detail": "exit 1",
                            }
                        ],
                    },
                    {
                        "root": str(repo.parent / "other"),
                        "manifest_error": None,
                        "items": [
                            {
                                "kind": "tool",
                                "name": "foreign",
                                "harness": None,
                                "status": "failed",
                                "detail": "exit 1",
                            }
                        ],
                    },
                ]
            }
        )
    )
    output = session_cli().stdout
    assert "setup: tool own" in output and "foreign" not in output


def test_plugin_check_failure_reported(session_cli, repo, write_manifest, stub):
    write_manifest(
        repo,
        """schema = 1
[[marketplace]]
name = "devkit"
source = "o/r"
[[plugin]]
id = "devkit@devkit"
check = "exit 1"
""",
    )
    stub(
        "claude", list_json=[{"id": "devkit@devkit", "installPath": "/plugins/devkit"}]
    )
    assert (
        "harnessup: `exit 1` failed: devkit's binaries are missing; its SessionStart hook retries the install"
        in session_cli().stdout
    )


def test_missing_plugin_reported_on_startup_only(session_cli, repo, write_manifest):
    write_manifest(
        repo,
        """schema = 1
[[marketplace]]
name = "mcpls"
source = "o/r"
[[plugin]]
id = "mcpls@mcpls"
""",
    )
    assert "mcpls@mcpls not installed" in session_cli().stdout
    assert "not installed" not in session_cli("resume").stdout


def test_manifest_error_reported(session_cli, repo, write_manifest):
    write_manifest(repo, "schema = 2")
    result = session_cli()
    assert result.returncode == 0
    assert "harnessup: manifest error: schema:" in result.stdout


def test_never_exits_nonzero(session_cli, repo):
    shutil.rmtree(repo / ".git/info")
    (repo / ".git/info").write_text("not a directory")
    result = session_cli()
    assert result.returncode == 0
    assert result.stdout.startswith("harnessup: session-start failed:")


@pytest.mark.parametrize(
    ("initial", "refresh"),
    [
        ("./AGENTS.local.md", "AGENTS.local.md"),
        ("AGENTS.local.md", "./AGENTS.local.md"),
    ],
)
def test_equivalent_file_targets_remain_ignored_and_owned(
    session_cli, repo, write_manifest, initial, refresh
):
    path = write_manifest(
        repo,
        f'''schema = 1
[[file]]
source = "rules.md"
target = "{initial}"
''',
    )
    result = session_cli()
    assert result.returncode == 0 and "skipped" not in result.stdout
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--", "AGENTS.local.md"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    assert read_exclude_block(repo) == {"/AGENTS.local.md"}
    (path.parent / "rules.md").write_text("updated rules")
    write_manifest(
        repo,
        f'''schema = 1
[[file]]
source = "rules.md"
target = "{refresh}"
''',
    )
    result = session_cli()
    assert result.returncode == 0 and "skipped" not in result.stdout
    assert (repo / "AGENTS.local.md").read_text() == "updated rules"
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--", "AGENTS.local.md"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
