from collections.abc import Mapping
from pathlib import Path

from harnessup.harness import CLIS
from harnessup.manifest import MANIFEST_DIR, Harness, Manifest, ManifestError, load
from harnessup.proc import run
from harnessup.state import read_report_for
from harnessup.workspace import (
    Changes,
    link_skills,
    merge_settings,
    place_files,
    read_exclude_block,
    write_exclude_block,
)

STARTUP_SOURCES = frozenset({"startup", "clear"})


def in_cloud(env: Mapping[str, str]) -> bool:
    return env.get("CLOUD_AGENT") == "true" or env.get("CLAUDE_CODE_REMOTE") == "true"


def repo_root(payload_cwd: Path, env: Mapping[str, str]) -> Path:
    if env.get("CLAUDE_PROJECT_DIR"):
        return Path(env["CLAUDE_PROJECT_DIR"]).resolve()
    outcome = run(
        ["git", "-C", str(payload_cwd), "rev-parse", "--show-toplevel"],
        cwd=payload_cwd,
        timeout=2,
    )
    return Path(outcome.output).resolve() if outcome.ok else payload_cwd.resolve()


def prepare(root: Path, manifest: Manifest) -> Changes:
    changes = place_files(root, manifest.files, read_exclude_block(root))
    if manifest.settings_local is not None:
        changes.extend(merge_settings(root, manifest.settings_local))
    changes.extend(link_skills(root))
    write_exclude_block(root, changes.entries)
    return changes


def session_start(
    harness: Harness, payload: Mapping[str, object], env: Mapping[str, str]
) -> str:
    if not in_cloud(env):
        return ""
    cwd = payload.get("cwd")
    root = repo_root(Path(cwd) if isinstance(cwd, str) else Path.cwd(), env)
    source = payload.get("source", "startup")
    startup = isinstance(source, str) and source in STARTUP_SOURCES
    cli = CLIS[harness]
    try:
        manifest = load(root)
    except ManifestError as error:
        return "\n\n".join(
            part
            for part in [
                cli.task_line if startup else "",
                f"harnessup: manifest error: {error}",
            ]
            if part
        )
    if manifest is None:
        return ""
    problems = prepare(root, manifest).problems if startup else []
    context_name = manifest.startup if startup else manifest.recovery
    context = ""
    if context_name:
        path = root / MANIFEST_DIR / context_name
        if path.is_file():
            context = path.read_text().strip()
        else:
            problems.append(f"missing context file {MANIFEST_DIR}/{context_name}")
    report = read_report_for(root)
    if report is not None:
        if report.manifest_error:
            problems.append(f"setup: manifest error: {report.manifest_error}")
        for item in report.items:
            if item.status == "failed" or (
                item.status == "skipped" and item.detail == "setup deadline"
            ):
                label = f" [{item.harness}]" if item.harness else ""
                problems.append(
                    f"setup: {item.kind} {item.name}{label} {item.status}: {item.detail}"
                )
    plugins = [plugin for plugin in manifest.plugins if harness in plugin.harnesses]
    for plugin in plugins:
        if plugin.check and not run(plugin.check, cwd=root, timeout=2).ok:
            problems.append(
                f"`{plugin.check}` failed: {plugin.name}'s binaries are missing; its SessionStart hook retries the install"
            )
    if startup:
        installed = cli.installed(10)
        if installed is None:
            problems.append(f"could not list installed {harness} plugins")
        else:
            for plugin in plugins:
                if plugin.id not in installed:
                    problems.append(
                        f"{plugin.id} not installed; setup reruns when the environment's setup script changes or its cache expires"
                    )
    return "\n\n".join(
        part
        for part in [
            context,
            cli.task_line if startup and manifest.task_tools else "",
            "\n".join(f"harnessup: {text}" for text in problems),
        ]
        if part
    )
