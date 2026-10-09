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
SILENT_STOP_HOOK = "#!/bin/sh\nexit 0\n"


def in_cloud(env: Mapping[str, str]) -> bool:
    return env.get("CLOUD_AGENT") == "true" or env.get("CLAUDE_CODE_REMOTE") == "true"


def session_enabled(env: Mapping[str, str]) -> bool:
    """Session start acts in the cloud, and wherever HARNESSUP_SESSION asks for it."""
    return in_cloud(env) or env.get("HARNESSUP_SESSION") == "true"


def silence_stop_hook() -> list[str]:
    """Turn Claude cloud's every-turn commit-signing stop hook into a no-op."""
    # The cloud launcher registers this hook, so the script must stay in place:
    # a registered hook whose script is missing errors on every turn.
    path = Path.home() / ".claude/stop-hook-git-check.sh"
    try:
        if path.is_file() and path.read_text() != SILENT_STOP_HOOK:
            path.write_text(SILENT_STOP_HOOK)
    except (OSError, UnicodeDecodeError) as error:
        return [f"could not silence stop hook {path}: {error}"]
    return []


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
    if not session_enabled(env):
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
    problems = (
        silence_stop_hook()
        if harness == "claude" and manifest.silence_stop_hook and in_cloud(env)
        else []
    )
    if startup:
        problems.extend(prepare(root, manifest).problems)
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
