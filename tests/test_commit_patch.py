import shutil
import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout


def test_commit_patch_preserves_unrelated_staging(repo, run_cli):
    baseline = "".join(f"line {number}\n" for number in range(20))
    selected = baseline.replace("line 17\n", "selected change\n")
    staged = baseline.replace("line 2\n", "staged change\n")
    both = staged.replace("line 17\n", "selected change\n")
    target = repo / "changes.txt"
    target.write_text(baseline)
    git(repo, "add", "changes.txt")
    git(repo, "commit", "-m", "test: initialize fixture")
    target.write_text(selected)
    patch = repo / "selected.patch"
    patch.write_text(git(repo, "diff", "HEAD", "--", "changes.txt"))
    target.write_text(staged)
    git(repo, "add", "changes.txt")
    target.write_text(both)

    result = run_cli(
        "commit-patch",
        "--patch",
        str(patch),
        "--message",
        "fix: selected hunk",
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert git(repo, "show", "HEAD:changes.txt") == selected
    assert git(repo, "show", ":changes.txt") == both
    assert target.read_text() == both
    assert git(repo, "diff", "--cached", "--name-only").strip() == "changes.txt"
    assert "staged change" in git(repo, "diff", "--cached")
    assert "selected change" not in git(repo, "diff", "--cached", "--unified=0")
    assert git(repo, "diff", "--name-only") == ""


@pytest.fixture
def selected_patch(repo):
    for name in ("selected.txt", "unrelated.txt"):
        (repo / name).write_text("before\n")
    git(repo, "add", "selected.txt", "unrelated.txt")
    git(repo, "commit", "-m", "test: initialize fixture")
    (repo / "selected.txt").write_text("selected change\n")
    patch = repo / "selected.patch"
    patch.write_text(git(repo, "diff", "HEAD", "--", "selected.txt"))
    (repo / "unrelated.txt").write_text("unrelated change\n")
    git(repo, "add", "unrelated.txt")
    return patch


def test_commit_patch_rejects_stale_patch(repo, selected_patch, run_cli):
    selected_patch.write_text(selected_patch.read_text().replace("-before", "-stale"))
    head = git(repo, "rev-parse", "HEAD")
    index = (repo / ".git/index").read_bytes()
    working_tree = {
        path.name: path.read_bytes() for path in repo.iterdir() if path.is_file()
    }

    result = run_cli(
        "commit-patch",
        "--patch",
        str(selected_patch),
        "--message",
        "fix: stale patch",
        cwd=repo,
    )

    assert result.returncode != 0
    assert "git apply:" in result.stderr
    assert "patch does not apply" in result.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git/index").read_bytes() == index
    assert {
        path.name: path.read_bytes() for path in repo.iterdir() if path.is_file()
    } == working_tree
    assert not (repo / ".git/index.lock").exists()


def test_commit_patch_preserves_unrelated_file_staging(repo, selected_patch, run_cli):
    nested = repo / "nested"
    nested.mkdir()
    result = run_cli(
        "commit-patch",
        "--patch",
        "../selected.patch",
        "--message",
        "fix: selected patch",
        cwd=nested,
    )

    assert result.returncode == 0, result.stderr
    assert git(repo, "show", "HEAD:selected.txt") == "selected change\n"
    assert git(repo, "show", "HEAD:unrelated.txt") == "before\n"
    assert git(repo, "show", ":unrelated.txt") == "unrelated change\n"
    assert git(repo, "diff", "--cached", "--name-only").strip() == "unrelated.txt"
    assert git(repo, "diff", "--name-only") == ""


def test_commit_patch_rejects_overlapping_staged_lines(repo, selected_patch, run_cli):
    (repo / "selected.txt").write_text("staged change\n")
    git(repo, "add", "selected.txt")
    head = git(repo, "rev-parse", "HEAD")
    index = (repo / ".git/index").read_bytes()

    result = run_cli(
        "commit-patch",
        "--patch",
        str(selected_patch),
        "--message",
        "fix: overlapping patch",
        cwd=repo,
    )

    assert result.returncode != 0
    assert "patch overlaps staged changes" in result.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "show", ":selected.txt") == "staged change\n"
    assert (repo / "selected.txt").read_text() == "staged change\n"


def test_commit_patch_reports_unavailable_merge_tree(
    repo, selected_patch, stub_bin, run_cli
):
    real_git = shutil.which("git")
    assert real_git is not None
    shim = stub_bin / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'for arg; do [ "$arg" = merge-tree ] && { echo "merge-tree is unavailable" >&2; exit 128; }; done\n'
        f'exec "{real_git}" "$@"\n'
    )
    shim.chmod(0o755)
    head = git(repo, "rev-parse", "HEAD")
    index = (repo / ".git/index").read_bytes()

    result = run_cli(
        "commit-patch",
        "--patch",
        str(selected_patch),
        "--message",
        "fix: selected patch",
        cwd=repo,
    )

    assert result.returncode != 0
    assert "merge-tree is unavailable" in result.stderr
    assert "overlap" not in result.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git/index").read_bytes() == index
