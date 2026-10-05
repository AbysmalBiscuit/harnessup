from collections.abc import Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from shutil import copy2, copytree, rmtree
from tempfile import TemporaryDirectory, mkdtemp

from harnessup import MARKETPLACE_DIR
from harnessup.harness import CLIS
from harnessup.manifest import MANIFEST_FILE, Harness, ManifestError, load, source_kind
from harnessup.proc import Deadline, run
from harnessup.session import prepare
from harnessup.state import Item, RepoReport, state_dir, write_report

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
        dangling_links: dict[Path, Path] = {}

        def copy_state_file(source: str, destination: str) -> str:
            path = Path(source)
            if path.is_symlink() and not path.exists():
                dangling_links[Path(destination)] = path.readlink()
                return destination
            return copy2(source, destination)

        try:
            if real_state.exists():
                copytree(real_state, scratch_state, copy_function=copy_state_file)
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
            # Dangling links stay absent during execution to prevent write-through.
            for path, target in dangling_links.items():
                if path.parent.is_dir() and not path.exists() and not path.is_symlink():
                    path.symlink_to(target)
            try:
                _promote_state(scratch_state, real_state)
            except OSError as error:
                return Item("bootstrap", name, harness, "failed", str(error))
        return item


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
