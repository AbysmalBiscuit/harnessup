import tomllib
from pathlib import Path

import pytest

from harnessup.manifest import FileEntry, ManifestError, load, parse, source_kind

FULL = tomllib.loads((Path(__file__).parent / "data/manifest_example.toml").read_text())


def test_parse_spec_example():
    manifest = parse(FULL)
    assert [p.id for p in manifest.plugins][:2] == ["devkit@devkit", "mcpls@mcpls"]
    assert manifest.plugins[1].bootstrap == ("hooks/bootstrap-binaries", "claude-code")
    assert manifest.plugins[0].check == "devkit --version"
    assert manifest.plugins[3].harnesses == ("claude",)
    assert manifest.plugins[2].harnesses == ("claude", "codex")
    assert manifest.files[1] == FileEntry("CLAUDE.local.md", "CLAUDE.local.md")
    assert manifest.settings_local == {
        "permissions": {"allow": ["Bash(devrun task:*)"]}
    }
    assert (manifest.startup, manifest.recovery) == ("startup.md", "recovery.md")


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({}, "schema"),
        ({"schema": 2}, "schema"),
        ({"schema": True}, "schema"),
        ({"schema": 1, "extra": 1}, "extra"),
        ({"schema": 1, "plugin": [{"id": "x@missing"}]}, "plugin[0].id"),
        ({"schema": 1, "plugin": [{"id": "noat"}]}, "plugin[0].id"),
        (
            {
                "schema": 1,
                "marketplace": [{"name": "m", "source": "o/r"}],
                "plugin": [{"id": "p@m", "bootstrap": "hooks/x"}],
            },
            "plugin[0].bootstrap",
        ),
        (
            {
                "schema": 1,
                "marketplace": [
                    {"name": "m", "source": "o/r", "harnesses": ["claude"]}
                ],
                "plugin": [{"id": "p@m", "harnesses": ["codex"]}],
            },
            "plugin[0].harnesses",
        ),
        (
            {
                "schema": 1,
                "marketplace": [{"name": "m", "source": "o/r", "harnesses": ["vim"]}],
            },
            "marketplace[0].harnesses",
        ),
        ({"schema": 1, "file": [{"source": "../x", "target": "x"}]}, "file[0].source"),
        (
            {"schema": 1, "file": [{"source": "x", "target": "/etc/x"}]},
            "file[0].target",
        ),
        (
            {"schema": 1, "marketplace": [{"name": "m", "source": "./../m"}]},
            "marketplace[0].source",
        ),
        ({"schema": 1, "tool": [{"name": "t", "install": "i"}]}, "tool[0].check"),
        ({"schema": 1, "claude": {"settings_local": []}}, "claude.settings_local"),
        ({"schema": 1, "context": {"startup": 3}}, "context.startup"),
        ({"schema": 1, "context": {"task_tools": "no"}}, "context.task_tools"),
        (
            {"schema": 1, "file": [{"source": "x", "target": "y", "typo": 1}]},
            "file[0].typo",
        ),
        ({"schema": 1, "tool": {}}, "tool"),
    ],
)
def test_validation_names_key_path(data, path):
    with pytest.raises(ManifestError) as err:
        parse(data)
    assert str(err.value).startswith(f"{path}: ")


def test_load_absent_returns_none(repo):
    assert load(repo) is None


def test_load_syntax_error(repo, write_manifest):
    write_manifest(repo, "schema = ")
    with pytest.raises(ManifestError, match=r"^manifest: "):
        load(repo)


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("./plugins", "path"),
        ("https://x/y.git", "git"),
        ("git@github.com:o/r.git", "git"),
        ("o/r", "github"),
    ],
)
def test_source_kind(source, kind):
    assert source_kind(source) == kind
