import importlib.metadata
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from harnessup import MARKETPLACE_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_versions_match_package():
    version = importlib.metadata.version("harnessup")
    for rel in [
        "plugin/.claude-plugin/plugin.json",
        "plugin/.codex-plugin/plugin.json",
    ]:
        assert json.loads((MARKETPLACE_DIR / rel).read_text())["version"] == version


def test_hooks_call_session_start():
    for name, rel in [("claude", "hooks.json"), ("codex", "hooks-codex.json")]:
        data = json.loads((MARKETPLACE_DIR / "plugin/hooks" / rel).read_text())
        hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        assert hook["command"] == f"harnessup session-start --harness {name}"
        assert hook["timeout"] == 30


def test_wheel_contains_marketplace(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(PROJECT_ROOT)],
        check=True,
    )
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        names = wheel.namelist()
    for rel in [
        "marketplace/.claude-plugin/marketplace.json",
        "marketplace/.agents/plugins/marketplace.json",
        "marketplace/plugin/.claude-plugin/plugin.json",
        "marketplace/plugin/.codex-plugin/plugin.json",
        "marketplace/plugin/hooks/hooks.json",
        "marketplace/plugin/hooks/hooks-codex.json",
    ]:
        assert f"harnessup/{rel}" in names


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_claude_validates_marketplace(tmp_path):
    assert (
        subprocess.run(
            ["claude", "plugin", "validate", str(MARKETPLACE_DIR)],
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
            },
            check=False,
        ).returncode
        == 0
    )
