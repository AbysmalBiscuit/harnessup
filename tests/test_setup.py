import json
import os
import sys
from pathlib import Path

import pytest

from harnessup import MARKETPLACE_DIR


@pytest.fixture
def setup_cli(repo, write_manifest, run_cli, stub, tmp_path, state_dir):
    plugin = tmp_path / "plugins/devkit"
    (plugin / "hooks").mkdir(parents=True)
    log = plugin / "bootstrap.log"
    bootstrap = plugin / "hooks/bootstrap-binaries"
    bootstrap.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n')
    bootstrap.chmod(0o755)
    write_manifest(
        repo,
        """schema = 1
[[marketplace]]
name = "devkit"
source = "o/r"
[[plugin]]
id = "devkit@devkit"
bootstrap = ["hooks/bootstrap-binaries", "claude-code"]
""",
    )
    stub("claude", list_json=[{"id": "devkit@devkit", "installPath": str(plugin)}])

    def invoke(*args, cwd=None, env=None):
        result = run_cli("setup", *args, cwd=cwd or repo, env=env)
        assert result.returncode == 0, result.stderr
        return result, json.loads((state_dir / "setup.json").read_text())

    return invoke, plugin, log


def test_registers_own_plugin_then_repo_items(setup_cli, stub_calls):
    invoke, _, _ = setup_cli
    invoke()
    calls = stub_calls("claude")
    assert calls[0] == ["plugin", "marketplace", "add", str(MARKETPLACE_DIR)]
    assert calls[1] == ["plugin", "install", "harnessup@harnessup", "--scope", "user"]
    assert ["plugin", "install", "devkit@devkit", "--scope", "user"] in calls


def test_bootstrap_runs_with_args(setup_cli):
    invoke, _, log = setup_cli
    invoke()
    assert log.read_text().strip() == "claude-code"


def test_bootstrap_retried_once(setup_cli):
    invoke, plugin, log = setup_cli
    bootstrap = plugin / "hooks/bootstrap-binaries"
    bootstrap.write_text(bootstrap.read_text() + f'test "$(wc -l < "{log}")" -ge 2\n')
    _, report = invoke()
    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == "ok"
    assert len(log.read_text().splitlines()) == 2


def test_failed_bootstrap_discards_state(setup_cli, state_dir):
    invoke, plugin, log = setup_cli
    plugin_state = state_dir.parent / "devkit"
    plugin_state.mkdir(parents=True)
    (plugin_state / "bootstrap-version").write_text("existing-version")
    (plugin_state / "keep").write_text("existing-state")
    bootstrap = plugin / "hooks/bootstrap-binaries"
    bootstrap.write_text(
        bootstrap.read_text()
        + 'state="$XDG_STATE_HOME/devkit"\n'
        + 'cat "$state/bootstrap-version"\n'
        + 'test ! -e "$state/bootstrap-failed" || echo "dirty retry"\n'
        + 'printf "failed" > "$state/bootstrap-failed"\n'
        + 'printf "changed" > "$state/bootstrap-version"\n'
        + 'rm "$state/keep"\n'
        + "exit 1\n"
    )

    _, report = invoke()

    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == "failed"
    assert len(log.read_text().splitlines()) == 2
    assert item["detail"] == "exit 1: existing-version"
    assert {path.name: path.read_text() for path in plugin_state.iterdir()} == {
        "bootstrap-version": "existing-version",
        "keep": "existing-state",
    }


@pytest.mark.parametrize("symlinked_state", [False, True])
def test_successful_bootstrap_keeps_state(
    setup_cli, state_dir, tmp_path, symlinked_state
):
    invoke, plugin, _ = setup_cli
    real_state = tmp_path / "actual-state" if symlinked_state else state_dir.parent
    if symlinked_state:
        real_state.mkdir()
        state_dir.parent.symlink_to(real_state, target_is_directory=True)
    plugin_state = state_dir.parent / "devkit"
    plugin_state.mkdir(parents=True)
    (plugin_state / "bootstrap-version").write_text("existing-version")
    (plugin_state / "bootstrap-failed").write_text("stale-failure")
    unrelated_state = state_dir.parent / "other-app"
    unrelated_state.mkdir()
    (unrelated_state / "keep").write_text("unrelated-state")
    bootstrap = plugin / "hooks/bootstrap-binaries"
    bootstrap.write_text(
        bootstrap.read_text()
        + 'state="$XDG_STATE_HOME/devkit"\n'
        + 'cp "$state/bootstrap-version" "$state/read-version"\n'
        + 'printf "installed" > "$state/bootstrap-version"\n'
        + 'rm "$state/bootstrap-failed"\n'
    )

    _, report = invoke()

    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == "ok"
    assert {path.name: path.read_text() for path in plugin_state.iterdir()} == {
        "bootstrap-version": "installed",
        "read-version": "existing-version",
    }
    assert (unrelated_state / "keep").read_text() == "unrelated-state"
    assert (real_state / "devkit/bootstrap-version").read_text() == "installed"
    if symlinked_state:
        assert state_dir.parent.is_symlink()
        assert state_dir.parent.resolve() == real_state


@pytest.mark.parametrize("succeeds", [False, True])
def test_bootstrap_handles_dangling_state_links(
    setup_cli, state_dir, tmp_path, succeeds
):
    invoke, plugin, log = setup_cli
    plugin_state = state_dir.parent / "devkit"
    plugin_state.mkdir(parents=True)
    missing_target = tmp_path / "missing-target"
    stamp = plugin_state / "bootstrap-version"
    stamp.symlink_to(missing_target)
    other_state = state_dir.parent / "other-app"
    other_state.mkdir()
    relative_link = other_state / "relative"
    relative_link.symlink_to("missing-relative")
    absolute_link = other_state / "absolute"
    absolute_link.symlink_to(tmp_path / "missing-absolute")
    bootstrap = plugin / "hooks/bootstrap-binaries"
    bootstrap.write_text(
        bootstrap.read_text()
        + 'printf "installed" > "$XDG_STATE_HOME/devkit/bootstrap-version"\n'
        + f"exit {0 if succeeds else 1}\n"
    )

    _, report = invoke()

    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == ("ok" if succeeds else "failed")
    assert len(log.read_text().splitlines()) == (1 if succeeds else 2)
    assert not missing_target.exists()
    assert relative_link.is_symlink()
    assert str(relative_link.readlink()) == "missing-relative"
    assert not relative_link.exists()
    assert absolute_link.is_symlink()
    assert absolute_link.readlink() == tmp_path / "missing-absolute"
    assert not absolute_link.exists()
    if succeeds:
        assert not stamp.is_symlink()
        assert stamp.read_text() == "installed"
    else:
        assert stamp.is_symlink()
        assert stamp.readlink() == missing_target


def test_failed_state_promotion_preserves_existing_state(
    setup_cli, state_dir, tmp_path, run_cli, repo
):
    _, plugin, log = setup_cli
    plugin_state = state_dir.parent / "devkit"
    plugin_state.mkdir(parents=True)
    (plugin_state / "bootstrap-version").write_text("existing-version")
    bootstrap = plugin / "hooks/bootstrap-binaries"
    bootstrap.write_text(
        bootstrap.read_text()
        + 'printf "installed" > "$XDG_STATE_HOME/devkit/bootstrap-version"\n'
    )
    injection = tmp_path / "injection"
    injection.mkdir()
    (injection / "sitecustomize.py").write_text(f"""import os
import shutil
from pathlib import Path
real_state = Path({str(state_dir.parent)!r})
original_rename = os.rename
original_copytree = shutil.copytree
def rename(source, destination, *args, **kwargs):
    if Path(destination) == real_state and Path(source).name == "state":
        raise OSError("injected promotion failure")
    return original_rename(source, destination, *args, **kwargs)
def copytree(source, destination, *args, **kwargs):
    if Path(destination) == real_state:
        raise OSError("injected promotion failure")
    return original_copytree(source, destination, *args, **kwargs)
os.rename = rename
shutil.copytree = copytree
""")

    result = run_cli(
        "setup",
        cwd=repo,
        env={
            "PYTHONPATH": f"{injection}:{Path(__file__).resolve().parents[1] / 'src'}"
        },
    )

    assert result.returncode == 0, result.stderr
    assert (plugin_state / "bootstrap-version").read_text() == "existing-version"
    report = json.loads((state_dir / "setup.json").read_text())
    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == "failed"
    assert "injected promotion failure" in item["detail"]
    assert len(log.read_text().splitlines()) == 2


def test_state_preparation_failure_continues_setup(
    setup_cli, state_dir, repo, write_manifest
):
    invoke, _, log = setup_cli
    other_state = state_dir.parent / "other-app"
    other_state.mkdir(parents=True)
    os.mkfifo(other_state / "events")
    manifest_path = repo / ".agents/harnessup/manifest.toml"
    write_manifest(
        repo,
        manifest_path.read_text()
        + """
[[tool]]
name = "later"
check = "exit 1"
install = "touch tool-installed"
[[file]]
source = "rules.md"
target = "AGENTS.local.md"
""",
    )
    (manifest_path.parent / "rules.md").write_text("rules")

    _, report = invoke()

    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == "failed"
    assert "named pipe" in item["detail"]
    assert not log.exists()
    assert (repo / "tool-installed").exists()
    assert (repo / "AGENTS.local.md").read_text() == "rules"


@pytest.mark.parametrize("mode", ["noop", "failed_write", "successful_write"])
def test_bootstrap_preserves_untouched_live_links(setup_cli, state_dir, tmp_path, mode):
    invoke, plugin, log = setup_cli
    state_dir.parent.mkdir(parents=True)
    external_state = tmp_path / "external-state"
    external_state.mkdir()
    data = external_state / "data"
    data.write_text("original")
    (external_state / "nested").mkdir()
    (external_state / "nested/keep").write_text("nested-state")
    (external_state / "inner-link").symlink_to("nested", target_is_directory=True)
    linked_directory = state_dir.parent / "other-app"
    linked_directory.symlink_to(external_state, target_is_directory=True)
    linked_file = state_dir.parent / "file-link"
    linked_file.symlink_to(data)
    if mode != "noop":
        bootstrap = plugin / "hooks/bootstrap-binaries"
        bootstrap.write_text(
            bootstrap.read_text()
            + 'printf "modified" > "$XDG_STATE_HOME/other-app/data"\n'
            + f'touch -r "{data}" "$XDG_STATE_HOME/other-app/data"\n'
            + f"exit {1 if mode == 'failed_write' else 0}\n"
        )

    _, report = invoke()

    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == ("failed" if mode == "failed_write" else "ok")
    assert len(log.read_text().splitlines()) == (2 if mode == "failed_write" else 1)
    assert data.read_text() == "original"
    assert linked_file.is_symlink()
    assert linked_file.readlink() == data
    assert (linked_directory / "inner-link").is_symlink()
    if mode == "successful_write":
        assert not linked_directory.is_symlink()
        assert (linked_directory / "data").read_text() == "modified"
    else:
        assert linked_directory.is_symlink()
        assert linked_directory.readlink() == external_state
        data.write_text("later")
        assert (linked_directory / "data").read_text() == "later"


def test_bootstrap_skipped_when_install_failed(setup_cli, stub):
    invoke, _, log = setup_cli
    stub("claude", fail=["devkit@devkit"])
    _, report = invoke()
    item = next(
        item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
    )
    assert item["status"] == "skipped" and item["detail"] == "plugin failed to install"
    assert not log.exists()


def test_codex_absent_is_skipped(setup_cli):
    invoke, _, _ = setup_cli
    _, report = invoke()
    items = [item for item in report["repos"][0]["items"] if item["harness"] == "codex"]
    assert items and all(
        item["status"] == "skipped" and item["detail"] == "codex not on PATH"
        for item in items
    )


def test_tool_install_only_when_check_fails(setup_cli, repo, write_manifest):
    invoke, _, _ = setup_cli
    write_manifest(
        repo,
        """schema = 1
[[tool]]
name = "present"
check = "exit 0"
install = "touch should-not-exist"
[[tool]]
name = "missing"
check = "exit 1"
install = "touch installed"
""",
    )
    _, report = invoke()
    assert not (repo / "should-not-exist").exists()
    assert (repo / "installed").exists()
    assert report["repos"][0]["items"][0]["detail"] == "already installed"


def test_deadline_skips_remaining(setup_cli, repo, write_manifest):
    invoke, _, _ = setup_cli
    path = write_manifest(
        repo,
        """schema = 1
[[tool]]
name = "slow"
check = "sleep 3"
install = "touch slow-installed"
[[tool]]
name = "later"
check = "exit 1"
install = "touch later-installed"
[[file]]
source = "rules.md"
target = "AGENTS.local.md"
""",
    )
    (path.parent / "rules.md").write_text("rules")
    _, report = invoke("--deadline", "1")
    item = next(item for item in report["repos"][0]["items"] if item["name"] == "later")
    assert item["status"] == "skipped" and item["detail"] == "setup deadline"
    assert not (repo / "later-installed").exists()
    assert (repo / "AGENTS.local.md").read_text() == "rules"


def test_install_timeout_caps_each_install(setup_cli, repo, write_manifest):
    invoke, _, _ = setup_cli
    write_manifest(
        repo,
        """schema = 1
[[tool]]
name = "slow"
check = "exit 1"
install = "sleep 3 && touch slow-installed"
""",
    )
    _, report = invoke("--install-timeout", "1")
    [item] = report["repos"][0]["items"]
    assert item["status"] == "failed" and "timed out" in item["detail"]
    assert not (repo / "slow-installed").exists()


def test_path_marketplace_is_absolute(setup_cli, repo, write_manifest, stub_calls):
    invoke, _, _ = setup_cli
    write_manifest(
        repo,
        """schema = 1
[[marketplace]]
name = "local"
source = "./local-mp"
""",
    )
    invoke()
    assert ["plugin", "marketplace", "add", str(repo / "local-mp")] in stub_calls(
        "claude"
    )


def test_invalid_manifest_recorded_exit_0(setup_cli, repo, write_manifest):
    invoke, _, _ = setup_cli
    write_manifest(repo, "schema = 2")
    _, report = invoke()
    assert report["repos"][0]["manifest_error"].startswith("schema: ")


HOME_FILE_MANIFEST = """schema = 1
[[home_file]]
source = "devkit-config.toml"
target = ".config/devkit/config.toml"
"""


def home_file_items(report):
    return [
        (item["status"], item["name"])
        for item in report["repos"][0]["items"]
        if item["kind"] == "home_file"
    ]


def test_setup_places_home_files(setup_cli, repo, write_manifest, tmp_path):
    invoke, _, _ = setup_cli
    manifest = write_manifest(repo, HOME_FILE_MANIFEST)
    (manifest.parent / "devkit-config.toml").write_text("[todo]\n")
    target = tmp_path / "home/.config/devkit/config.toml"
    for _ in range(2):
        _, report = invoke()
        assert target.read_text() == "[todo]\n"
        assert home_file_items(report) == [("ok", ".config/devkit/config.toml")]


def test_setup_keeps_existing_home_file(setup_cli, repo, write_manifest, tmp_path):
    invoke, _, _ = setup_cli
    manifest = write_manifest(repo, HOME_FILE_MANIFEST)
    (manifest.parent / "devkit-config.toml").write_text("[todo]\n")
    target = tmp_path / "home/.config/devkit/config.toml"
    target.parent.mkdir(parents=True)
    target.write_text("mine\n")
    _, report = invoke()
    assert target.read_text() == "mine\n"
    assert home_file_items(report) == [("skipped", ".config/devkit/config.toml")]


def test_setup_reports_missing_home_file_source(setup_cli, repo, write_manifest):
    invoke, _, _ = setup_cli
    write_manifest(repo, HOME_FILE_MANIFEST)
    _, report = invoke()
    assert home_file_items(report) == [("failed", ".config/devkit/config.toml")]


@pytest.mark.parametrize(
    ("claude", "env", "exit_code"),
    [
        ("silence_stop_hook = true", {"CLOUD_AGENT": "true"}, 0),
        ("silence_stop_hook = true", {}, 2),
        ("", {"CLOUD_AGENT": "true"}, 2),
    ],
)
def test_setup_silences_stop_hook_on_opt_in_in_cloud(
    setup_cli, repo, write_manifest, run_stop_hook, claude, env, exit_code
):
    invoke, _, _ = setup_cli
    write_manifest(repo, f"schema = 1\n[claude]\n{claude}\n")
    invoke(env=env)
    assert run_stop_hook()[0] == exit_code


def test_discovers_children_of_cwd(setup_cli, tmp_path, write_manifest):
    invoke, _, _ = setup_cli
    parent = tmp_path / "parent"
    (parent / "a").mkdir(parents=True)
    (parent / "b").mkdir()
    write_manifest(parent / "a", "schema = 1")
    result, report = invoke(cwd=parent)
    assert result.returncode == 0
    assert [row["root"] for row in report["repos"]] == [str(parent / "a")]


def test_exit_0_when_everything_fails(setup_cli, stub):
    invoke, _, _ = setup_cli
    stub("claude", fail=["plugin"])
    _, report = invoke()
    assert all(
        item["status"] in {"failed", "skipped"}
        for item in report["items"] + report["repos"][0]["items"]
    )


def test_codex_only_bootstrap_uses_cache_root(
    setup_cli, repo, write_manifest, stub, tmp_path, run_cli, state_dir
):
    _, _, _ = setup_cli
    home = tmp_path / "codex"
    plugin = home / "plugins/cache/devkit/devkit/0.1.0"
    (plugin / "hooks").mkdir(parents=True)
    script = plugin / "hooks/bootstrap-binaries"
    script.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath('ran').write_text('yes')\n"
    )
    script.chmod(0o755)
    write_manifest(
        repo,
        """schema = 1
[[marketplace]]
name = "devkit"
source = "o/r"
harnesses = ["codex"]
[[plugin]]
id = "devkit@devkit"
bootstrap = ["hooks/bootstrap-binaries"]
""",
    )
    stub(
        "codex",
        list_json={
            "installed": [
                {
                    "pluginId": "devkit@devkit",
                    "name": "devkit",
                    "marketplaceName": "devkit",
                    "version": "0.1.0",
                }
            ],
            "available": [],
        },
    )
    result = run_cli("setup", cwd=repo, env={"CODEX_HOME": str(home)})
    assert result.returncode == 0
    assert (plugin / "ran").read_text() == "yes"
    report = json.loads((state_dir / "setup.json").read_text())
    assert (
        next(
            item for item in report["repos"][0]["items"] if item["kind"] == "bootstrap"
        )["harness"]
        == "codex"
    )
