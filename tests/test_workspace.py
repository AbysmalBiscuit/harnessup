import json
import shutil
import subprocess

import pytest

from harnessup.manifest import FileEntry
from harnessup.workspace import (
    EXCLUDE_BEGIN,
    EXCLUDE_END,
    link_skills,
    merge_settings,
    place_files,
    read_exclude_block,
    write_exclude_block,
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def track(root, path):
    subprocess.run(["git", "-C", str(root), "add", "-f", "--", path], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "test fixture"], check=True
    )


def test_place_files_writes_absent_target(repo):
    source = write(repo / ".agents/harnessup/rules.md", "rules")
    source.chmod(0o755)
    changes = place_files(repo, [FileEntry("rules.md", "AGENTS.local.md")], set())
    assert (repo / "AGENTS.local.md").read_text() == "rules"
    assert (repo / "AGENTS.local.md").stat().st_mode & 0o777 == 0o755
    assert changes.entries == ["/AGENTS.local.md"]


def test_place_files_overwrites_owned(repo):
    write(repo / ".agents/harnessup/rules.md", "new")
    write(repo / "AGENTS.local.md", "old")
    place_files(repo, [FileEntry("rules.md", "AGENTS.local.md")], {"/AGENTS.local.md"})
    assert (repo / "AGENTS.local.md").read_text() == "new"


@pytest.mark.parametrize("tracked", [False, True])
def test_place_files_skips_foreign_or_tracked(repo, tracked):
    write(repo / ".agents/harnessup/rules.md", "new")
    write(repo / "AGENTS.local.md", "old")
    if tracked:
        track(repo, "AGENTS.local.md")
    changes = place_files(
        repo,
        [FileEntry("rules.md", "AGENTS.local.md")],
        {"/AGENTS.local.md"} if tracked else set(),
    )
    reason = "tracked by git" if tracked else "not written by harnessup"
    assert changes.problems == [f"skipped AGENTS.local.md: {reason}"]
    assert changes.entries == []
    assert (repo / "AGENTS.local.md").read_text() == "old"


def test_place_files_missing_source(repo):
    assert place_files(repo, [FileEntry("nope.md", "target")], set()).problems == [
        "missing [[file]] source .agents/harnessup/nope.md"
    ]


def test_removed_file_entry_is_left_alone(repo):
    write(repo / ".agents/harnessup/rules.md", "rules")
    changes = place_files(repo, [FileEntry("rules.md", "AGENTS.local.md")], set())
    write_exclude_block(repo, changes.entries)
    place_files(repo, (), read_exclude_block(repo))
    write_exclude_block(repo, [])
    assert (repo / "AGENTS.local.md").read_text() == "rules"
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "?? AGENTS.local.md" in result.stdout


def test_merge_settings_keeps_existing_keys(repo):
    target = write(
        repo / ".claude/settings.local.json",
        '{"permissions": {"allow": ["Read"]}, "model": "x"}',
    )
    changes = merge_settings(
        repo, {"permissions": {"allow": ["Bash(devrun task:*)", "Read"]}}
    )
    assert json.loads(target.read_text()) == {
        "permissions": {"allow": ["Read", "Bash(devrun task:*)"]},
        "model": "x",
    }
    assert changes.entries == ["/.claude/settings.local.json"]


@pytest.mark.parametrize("text", ["{", "[]"])
def test_merge_settings_invalid_json_reported(repo, text):
    target = write(repo / ".claude/settings.local.json", text)
    changes = merge_settings(repo, {"model": "new"})
    assert changes.problems == [
        "skipped .claude/settings.local.json: not a JSON object"
    ]
    assert target.read_text() == text


def test_merge_settings_skips_tracked(repo):
    target = write(repo / ".claude/settings.local.json", '{"model": "old"}')
    track(repo, ".claude/settings.local.json")
    changes = merge_settings(repo, {"model": "new"})
    assert "tracked by git" in changes.problems[0]
    assert json.loads(target.read_text()) == {"model": "old"}


def skill(repo):
    write(repo / ".agents/harnessup/skills/cloud/SKILL.md", "cloud rules")
    return repo / ".agents/harnessup/skills/cloud"


def test_link_skills_shared_dir(repo):
    source = skill(repo)
    (repo / ".agents/skills").mkdir()
    (repo / ".claude").mkdir()
    (repo / ".claude/skills").symlink_to("../.agents/skills")
    changes = link_skills(repo)
    assert changes.entries == ["/.agents/skills/cloud"]
    assert (repo / ".agents/skills/cloud").resolve() == source


def test_link_skills_separate_dirs(repo):
    source = skill(repo)
    for name in (".agents/skills", ".claude/skills"):
        (repo / name).mkdir(parents=True)
    changes = link_skills(repo)
    assert sorted(changes.entries) == ["/.agents/skills/cloud", "/.claude/skills/cloud"]
    assert all(
        (repo / name / "cloud").resolve() == source
        for name in (".agents/skills", ".claude/skills")
    )


def test_link_skills_creates_missing_dirs(repo):
    source = skill(repo)
    assert not link_skills(repo).problems
    assert (repo / ".claude/skills/cloud").resolve() == source


def test_link_skills_never_shadows_tracked(repo):
    skill(repo)
    target = write(repo / ".agents/skills/cloud/SKILL.md", "tracked")
    track(repo, ".agents/skills/cloud")
    changes = link_skills(repo)
    assert any("tracked by git" in text for text in changes.problems)
    assert target.read_text() == "tracked"


def test_link_skills_replaces_own_link(repo):
    skill(repo)
    link_skills(repo)
    assert not link_skills(repo).problems


def test_exclude_block_idempotent_and_preserves_lines(repo):
    exclude = repo / ".git/info/exclude"
    exclude.write_text("# mine\n*.log\n")
    write_exclude_block(repo, ["/b", "/a"])
    write_exclude_block(repo, ["/b", "/a"])
    assert (
        exclude.read_text()
        == f"# mine\n*.log\n{EXCLUDE_BEGIN}\n/a\n/b\n{EXCLUDE_END}\n"
    )
    assert read_exclude_block(repo) == {"/a", "/b"}


def test_exclude_block_created_when_missing(repo):
    shutil.rmtree(repo / ".git/info")
    write_exclude_block(repo, ["/a"])
    assert read_exclude_block(repo) == {"/a"}


def test_exclude_from_worktree_uses_common_dir(repo, tmp_path):
    write(repo / "readme", "repo")
    track(repo, "readme")
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-qb", "other", str(worktree)],
        check=True,
    )
    write_exclude_block(worktree, ["/a"])
    assert "/a" in (repo / ".git/info/exclude").read_text()
