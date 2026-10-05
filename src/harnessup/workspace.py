import copy
import json
import os
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from harnessup.manifest import MANIFEST_DIR, FileEntry
from harnessup.proc import run

EXCLUDE_BEGIN = "# >>> harnessup (generated; rewritten every session)"
EXCLUDE_END = "# <<< harnessup"
SKILL_DIRS = (".claude/skills", ".agents/skills")


@dataclass
class Changes:
    entries: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def extend(self, other: "Changes") -> None:
        self.entries.extend(other.entries)
        self.problems.extend(other.problems)


def is_tracked(root: Path, rel: str) -> bool:
    return run(
        [
            "git",
            "--literal-pathspecs",
            "-C",
            str(root),
            "ls-files",
            "--error-unmatch",
            "--",
            rel,
        ],
        cwd=root,
        timeout=2,
    ).ok


def _exclude_path(root: Path) -> Path:
    out = run(
        ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
        cwd=root,
        timeout=2,
    )
    if not out.ok:
        raise OSError(f"could not locate git exclude: {out.describe()}")
    return (root / out.output).resolve()


def read_exclude_block(root: Path) -> set[str]:
    path = _exclude_path(root)
    if not path.exists():
        return set()
    text = path.read_text()
    if EXCLUDE_BEGIN not in text or EXCLUDE_END not in text:
        return set()
    return set(
        text.split(EXCLUDE_BEGIN, 1)[1].split(EXCLUDE_END, 1)[0].strip().splitlines()
    )


def write_exclude_block(root: Path, entries: Iterable[str]) -> None:
    path = _exclude_path(root)
    text = path.read_text() if path.exists() else ""
    block = "\n".join([EXCLUDE_BEGIN, *sorted(set(entries)), EXCLUDE_END])
    if EXCLUDE_BEGIN in text and EXCLUDE_END in text:
        prefix, rest = text.split(EXCLUDE_BEGIN, 1)
        _, suffix = rest.split(EXCLUDE_END, 1)
        text = prefix + block + suffix
    else:
        text += ("\n" if text and not text.endswith("\n") else "") + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def place_files(root: Path, files: Sequence[FileEntry], owned: set[str]) -> Changes:
    changes = Changes()
    for entry in files:
        source = root / MANIFEST_DIR / entry.source
        target = root / entry.target
        exclude = "/" + entry.target
        if not source.is_file():
            changes.problems.append(
                f"missing [[file]] source {MANIFEST_DIR}/{entry.source}"
            )
        elif is_tracked(root, entry.target):
            changes.problems.append(f"skipped {entry.target}: tracked by git")
        elif (target.exists() or target.is_symlink()) and exclude not in owned:
            changes.problems.append(f"skipped {entry.target}: not written by harnessup")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            changes.entries.append(exclude)
    return changes


def deep_merge(
    base: dict[str, object], overlay: Mapping[str, object]
) -> dict[str, object]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            merged[key] = existing + [
                item
                for index, item in enumerate(value)
                if item not in existing and item not in value[:index]
            ]
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def merge_settings(root: Path, settings: Mapping[str, object]) -> Changes:
    rel = ".claude/settings.local.json"
    target = root / rel
    if is_tracked(root, rel):
        return Changes(problems=[f"skipped {rel}: tracked by git"])
    try:
        base = json.loads(target.read_text()) if target.exists() else {}
    except (json.JSONDecodeError, UnicodeError):
        base = None
    if not isinstance(base, dict):
        return Changes(problems=[f"skipped {rel}: not a JSON object"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(deep_merge(base, settings), indent=2) + "\n")
    return Changes(entries=["/" + rel])


def link_skills(root: Path) -> Changes:
    changes = Changes()
    source_dir = root / MANIFEST_DIR / "skills"
    if not source_dir.is_dir():
        return changes
    sources = [
        path
        for path in sorted(source_dir.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    ]
    directories = []
    for rel in SKILL_DIRS:
        candidate = root / rel
        candidate.mkdir(parents=True, exist_ok=True)
        directory = candidate.resolve()
        if directory not in directories:
            directories.append(directory)
    for source in sources:
        for directory in directories:
            target = directory / source.name
            rel = os.path.relpath(target, root.resolve())
            if is_tracked(root, rel):
                changes.problems.append(
                    f"skipped skill {source.name} in {directory}: tracked by git"
                )
                continue
            if target.is_symlink() and target.resolve().is_relative_to(
                source_dir.resolve()
            ):
                target.unlink()
            elif target.exists() or target.is_symlink():
                changes.problems.append(
                    f"skipped skill {source.name} in {directory}: exists"
                )
                continue
            target.symlink_to(
                os.path.relpath(source.resolve(), directory), target_is_directory=True
            )
            if target.is_relative_to(root.resolve()):
                changes.entries.append(
                    "/" + target.relative_to(root.resolve()).as_posix()
                )
    return changes
