from collections.abc import Mapping, Sequence
from filecmp import cmp, cmpfiles, dircmp
from importlib.metadata import version
from pathlib import Path
from shutil import copy2, copytree, rmtree
from tempfile import TemporaryDirectory, mkdtemp

from harnessup import MARKETPLACE_DIR
from harnessup.harness import CLIS
from harnessup.manifest import (
    MANIFEST_DIR,
    MANIFEST_FILE,
    FileEntry,
    Harness,
    ManifestError,
    load,
    source_kind,
)
from harnessup.proc import Deadline, run
from harnessup.session import prepare
from harnessup.state import Item, RepoReport, Status, state_dir, write_report

DEADLINE_S = 200.0
INSTALL_TIMEOUT_S = 120.0
CHECK_TIMEOUT_S = 30.0


def discover(repos: Sequence[Path], cwd: Path) -> list[Path]:
    if repos:
        return [path.resolve() for path in repos]
    if (cwd / MANIFEST_FILE).is_file():
        return [cwd.resolve()]
    return [
        child.resolve()
        for child in sorted(cwd.iterdir())
        if child.is_dir() and (child / MANIFEST_FILE).is_file()
    ]


def _execute(
    kind: str,
    name: str,
    harness: Harness | None,
    cmd: Sequence[str] | str,
    cwd: Path,
    deadline: Deadline,
    timeout: float = INSTALL_TIMEOUT_S,
    env: Mapping[str, str] | None = None,
) -> Item:
    if deadline.expired:
        return Item(kind, name, harness, "skipped", "setup deadline")
    if harness is not None and not CLIS[harness].available():
        return Item(kind, name, harness, "skipped", f"{harness} not on PATH")
    outcome = run(cmd, cwd=cwd, timeout=deadline.clamp(timeout), env=env)
    return Item(
        kind,
        name,
        harness,
        "ok" if outcome.ok else "failed",
        outcome.output if outcome.ok else outcome.describe(),
    )


def _promote_state(scratch_state: Path, real_state: Path) -> None:
    real_state.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="harnessup-replacement-", dir=real_state.parent
    ) as temporary:
        replacement = Path(temporary) / "state"
        copytree(scratch_state, replacement, symlinks=True)
        backup_directory = Path(
            mkdtemp(prefix="harnessup-original-", dir=real_state.parent)
        )
        original = backup_directory / "original"
        try:
            if real_state.exists():
                real_state.rename(original)
            try:
                replacement.rename(real_state)
            except OSError as error:
                if original.exists():
                    try:
                        original.rename(real_state)
                    except OSError as rollback_error:
                        raise OSError(
                            f"{error}; original state retained at {original}: "
                            f"{rollback_error}"
                        ) from rollback_error
                raise
        finally:
            if not original.exists():
                backup_directory.rmdir()
        rmtree(backup_directory, ignore_errors=True)


def _same_state(original: Path, copied: Path) -> bool:
    if original.is_symlink() and copied.is_symlink():
        return original.readlink() == copied.readlink()
    if original.is_dir() and copied.is_dir():
        comparison = dircmp(original, copied, ignore=[])
        if comparison.left_only or comparison.right_only:
            return False
        _, changed, errors = cmpfiles(
            original, copied, comparison.common_files, shallow=False
        )
        return (
            not changed
            and not errors
            and all(
                _same_state(original / name, copied / name)
                for name in comparison.common_dirs + comparison.common_funny
            )
        )
    return (
        original.is_file()
        and copied.is_file()
        and original.stat().st_mode == copied.stat().st_mode
        and cmp(original, copied, shallow=False)
    )


def _bootstrap(
    name: str,
    harness: Harness,
    argv: Sequence[str],
    cwd: Path,
    deadline: Deadline,
) -> Item:
    real_state = state_dir().parent.resolve()
    with TemporaryDirectory(prefix="harnessup-bootstrap-") as temporary:
        scratch_state = Path(temporary) / "state"
        state_links: dict[Path, tuple[Path, Path]] = {}

        def copy_state_links(directory: str, names: list[str]) -> list[str]:
            ignored = []
            for entry in names:
                source = Path(directory) / entry
                if source.is_symlink():
                    destination = scratch_state / source.relative_to(real_state)
                    state_links[destination] = (source, source.readlink())
                    if not source.exists():
                        ignored.append(entry)
            return ignored

        try:
            if real_state.exists():
                copytree(real_state, scratch_state, ignore=copy_state_links)
            else:
                scratch_state.mkdir()
        except OSError as error:
            return Item(
                "bootstrap",
                name,
                harness,
                "failed",
                f"could not prepare state: {error}",
            )
        item = _execute(
            "bootstrap",
            name,
            harness,
            argv,
            cwd,
            deadline,
            env={"XDG_STATE_HOME": str(scratch_state)},
        )
        if item.status == "ok":
            try:
                # Restore links after execution so bootstrap writes stay isolated.
                for path, (source, target) in reversed(state_links.items()):
                    if source.exists():
                        if _same_state(source, path):
                            if path.is_dir() and not path.is_symlink():
                                rmtree(path)
                            else:
                                path.unlink()
                            path.symlink_to(target)
                    elif (
                        path.parent.is_dir()
                        and not path.exists()
                        and not path.is_symlink()
                    ):
                        path.symlink_to(target)
                _promote_state(scratch_state, real_state)
            except OSError as error:
                return Item("bootstrap", name, harness, "failed", str(error))
        return item


def _place_home_file(root: Path, entry: FileEntry) -> Item:
    source = root / MANIFEST_DIR / entry.source
    target = Path.home() / entry.target

    def item(status: Status, detail: str) -> Item:
        return Item("home_file", entry.target, None, status, detail)

    if not source.is_file():
        return item("failed", f"missing source {MANIFEST_DIR}/{entry.source}")
    try:
        if target.exists() or target.is_symlink():
            if target.is_file() and cmp(source, target, shallow=False):
                return item("ok", "already in place")
            return item("skipped", "target exists with other content")
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, target)
    except OSError as error:
        return item("failed", str(error))
    return item("ok", "placed")


def setup(repos: Sequence[Path], cwd: Path, deadline: Deadline) -> Path:
    own: list[Item] = []
    reports: list[RepoReport] = []
    for harness, cli in CLIS.items():
        if cli.available():
            own.append(
                _execute(
                    "self",
                    "harnessup marketplace",
                    harness,
                    cli.marketplace_add(str(MARKETPLACE_DIR)),
                    cwd,
                    deadline,
                )
            )
            own.append(
                _execute(
                    "self",
                    "harnessup@harnessup",
                    harness,
                    cli.plugin_install("harnessup@harnessup"),
                    cwd,
                    deadline,
                )
            )
    for root in discover(repos, cwd):
        report = RepoReport(str(root))
        reports.append(report)
        try:
            manifest = load(root)
        except ManifestError as error:
            report.manifest_error = str(error)
            continue
        if manifest is None:
            continue
        for marketplace in manifest.marketplaces:
            source = (
                str((root / marketplace.source).resolve())
                if source_kind(marketplace.source) == "path"
                else marketplace.source
            )
            for harness in marketplace.harnesses:
                report.items.append(
                    _execute(
                        "marketplace",
                        marketplace.name,
                        harness,
                        CLIS[harness].marketplace_add(source),
                        root,
                        deadline,
                    )
                )
        installed_ok: dict[str, list[Harness]] = {}
        for plugin in manifest.plugins:
            for harness in plugin.harnesses:
                item = _execute(
                    "plugin",
                    plugin.id,
                    harness,
                    CLIS[harness].plugin_install(plugin.id),
                    root,
                    deadline,
                )
                report.items.append(item)
                if item.status == "ok":
                    installed_ok.setdefault(plugin.id, []).append(harness)
        listings: dict[Harness, dict[str, Path | None] | None] = {}
        for plugin in manifest.plugins:
            if not plugin.bootstrap:
                continue
            successful = installed_ok.get(plugin.id, [])
            harness = (
                "claude"
                if "claude" in successful
                else "codex"
                if "codex" in successful
                else None
            )
            if deadline.expired:
                report.items.append(
                    Item("bootstrap", plugin.id, harness, "skipped", "setup deadline")
                )
                continue
            if harness is None:
                report.items.append(
                    Item(
                        "bootstrap",
                        plugin.id,
                        None,
                        "skipped",
                        "plugin failed to install",
                    )
                )
                continue
            if harness not in listings:
                listings[harness] = CLIS[harness].installed(deadline.clamp(10))
            listing = listings[harness]
            plugin_root = listing.get(plugin.id) if listing is not None else None
            if deadline.expired:
                report.items.append(
                    Item("bootstrap", plugin.id, harness, "skipped", "setup deadline")
                )
                continue
            if plugin_root is None:
                report.items.append(
                    Item(
                        "bootstrap",
                        plugin.id,
                        harness,
                        "failed",
                        "plugin root not found",
                    )
                )
                continue
            argv = [str(plugin_root / plugin.bootstrap[0]), *plugin.bootstrap[1:]]
            item = _bootstrap(plugin.id, harness, argv, plugin_root, deadline)
            if item.status == "failed":
                item = _bootstrap(plugin.id, harness, argv, plugin_root, deadline)
            report.items.append(item)
        for tool in manifest.tools:
            item = _execute(
                "tool", tool.name, None, tool.check, root, deadline, CHECK_TIMEOUT_S
            )
            if item.status == "ok":
                item.detail = "already installed"
            elif item.status == "failed":
                item = _execute("tool", tool.name, None, tool.install, root, deadline)
            report.items.append(item)
        report.items.extend(
            _place_home_file(root, entry) for entry in manifest.home_files
        )
        try:
            changes = prepare(root, manifest)
            report.items.extend(
                Item("workspace", "prepare", None, "failed", problem)
                for problem in changes.problems
            )
        except Exception as error:  # noqa: BLE001
            report.items.append(
                Item("workspace", "prepare", None, "failed", str(error))
            )
    return write_report(own, reports, version("harnessup"))
