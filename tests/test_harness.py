from pathlib import Path

import pytest

from harnessup.harness import CLIS


def test_claude_installed_maps_install_path(stub):
    stub("claude", list_json=[{"id": "devkit@devkit", "installPath": "/c/devkit"}])
    assert CLIS["claude"].installed(10) == {"devkit@devkit": Path("/c/devkit")}


def test_codex_installed_derives_cache_root(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    stub(
        "codex",
        list_json={
            "installed": [
                {
                    "pluginId": "devkit@devkit",
                    "name": "devkit",
                    "marketplaceName": "devkit",
                    "version": "0.14.10",
                }
            ],
            "available": [],
        },
    )
    assert CLIS["codex"].installed(10) == {
        "devkit@devkit": tmp_path / "cx/plugins/cache/devkit/devkit/0.14.10"
    }


def test_installed_returns_none_on_failure(stub):
    stub("claude", fail=["list"])
    assert CLIS["claude"].installed(10) is None


@pytest.mark.parametrize(
    "payload", [{}, [{"id": "x"}], [None], "bad", [{"id": 1, "installPath": "/x"}]]
)
def test_installed_returns_none_on_malformed_listing(stub, payload):
    stub("claude", list_json=payload)
    assert CLIS["claude"].installed(10) is None


def test_task_lines_match_spec():
    assert CLIS["codex"].task_line.startswith("Track progress with `update_plan`")
    assert "TaskCreate, TaskUpdate, TaskList and TaskGet" in CLIS["claude"].task_line


def test_installed_parses_listing_longer_than_output_tail(stub):
    stub(
        "claude",
        list_json=[
            {"id": f"plugin{i}@market", "installPath": f"/plugins/{i}"}
            for i in range(8)
        ],
    )
    installed = CLIS["claude"].installed(10)
    assert installed is not None
    assert installed["plugin0@market"] == Path("/plugins/0")
    assert installed["plugin7@market"] == Path("/plugins/7")
