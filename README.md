# harnessup

harnessup prepares Claude Code and Codex cloud sessions from a repository manifest. It registers plugins, runs their binary bootstraps, installs declared tools, places repository-owned files and skills, and injects startup or recovery context. The package supplies the mechanism; each repository owns its rules and content.

## Cloud environment setup

Put this in the cloud environment's setup script. The repository must already be cloned.

```sh
uv tool install git+https://github.com/AbysmalBiscuit/harnessup
harnessup setup
```

The git install follows `main`; append `@vX.Y.Z` to pin a release. Setup discovers the manifest in its working directory, or in its direct children. Use repeated `--repo PATH` arguments to select repositories explicitly. It registers the bundled harnessup plugin with each available harness, then processes each repository's manifest. A missing harness is skipped. Failed items are recorded, later items still run, and setup exits successfully so a cloud session can start and report the problems.

## Repository manifest

Commit `.agents/harnessup/manifest.toml` and the content it references:

```toml
schema = 1

[[marketplace]]
name = "devkit"
source = "AbysmalBiscuit/devkit"

[[marketplace]]
name = "mcpls"
source = "AbysmalBiscuit/mcpls"

[[marketplace]]
name = "agent-guard"
source = "AbysmalBiscuit/agent-guard"

[[marketplace]]
name = "superpowers-marketplace"
source = "obra/superpowers-marketplace"
harnesses = ["claude"]

[[plugin]]
id = "devkit@devkit"
bootstrap = ["hooks/bootstrap-binaries"]
check = "devkit --version"

[[plugin]]
id = "mcpls@mcpls"
bootstrap = ["hooks/bootstrap-binaries", "claude-code"]
check = "mcpls --version"

[[plugin]]
id = "agent-guard@agent-guard"

[[plugin]]
id = "superpowers@superpowers-marketplace"

[[tool]]
name = "shellcheck"
install = "apt-get install -y shellcheck"
check = "shellcheck --version"

[[file]]
source = "AGENTS.local.md"
target = "AGENTS.local.md"

[[file]]
source = "CLAUDE.local.md"
target = "CLAUDE.local.md"

[[file]]
source = "devkit.local.toml"
target = "devkit.local.toml"

[[home_file]]
source = "devkit-config.toml"
target = ".config/devkit/config.toml"

[claude.settings_local]
permissions = { allow = ["Bash(devrun task:*)"] }

[context]
startup = "startup.md"
recovery = "recovery.md"
```

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schema` | integer | yes | Manifest schema version, `1` |
| `marketplace.name` | string | yes | Must match the name in the marketplace's own `marketplace.json` |
| `marketplace.source` | string | yes | GitHub `owner/repo`, git URL containing `://` or beginning with `git@`, or repository-relative path beginning with `./` |
| `marketplace.harnesses` | list of `claude`, `codex` | no | Defaults to both harnesses |
| `plugin.id` | string | yes | `name@marketplace`; the marketplace must be declared |
| `plugin.harnesses` | list of `claude`, `codex` | no | Defaults to its marketplace's harnesses; must be a subset of them |
| `plugin.bootstrap` | list of strings | no | Executable path inside the installed plugin, followed by arguments; run during setup |
| `plugin.check` | string | no | Shell command checked at session start; failure reports missing binaries |
| `tool.name` | string | yes | Label used in reports |
| `tool.install` | string | yes | Shell command run from the repository root when the check fails |
| `tool.check` | string | yes | Shell command; a successful exit skips installation |
| `file.source` | string | yes | File relative to `.agents/harnessup/` |
| `file.target` | string | yes | Destination relative to the repository root |
| `home_file.source` | string | yes | File relative to `.agents/harnessup/` |
| `home_file.target` | string | yes | Destination relative to the home directory; placed only by `harnessup setup` |
| `claude.settings_local` | table | no | Recursively merged into `.claude/settings.local.json`; lists gain missing entries and manifest scalars win |
| `context.startup` | string | no | File relative to `.agents/harnessup/`, printed for startup and clear |
| `context.recovery` | string | no | File relative to `.agents/harnessup/`, printed for resume, compact and fork |
| `context.task_tools` | boolean | no | `false` drops the task-tool reminder from startup context, for a repository whose startup context says how to track work; defaults to `true` |

Unknown keys, unsupported schemas, missing required values, wrong types, undeclared marketplaces, absolute paths and paths containing `..` are errors naming the relevant key. List every plugin the cloud session needs in the manifest, including plugins enabled in `.claude/settings.json`. harnessup does not read that file, and cloud sessions do not fetch its marketplaces.

## Cloud-only skills and session files

Put each cloud-only skill in `.agents/harnessup/skills/<name>/SKILL.md`. No manifest entry is needed. harnessup links the skill into both `.claude/skills/` and `.agents/skills/`, resolving and deduplicating shared directories. It skips tracked skills and foreign files or directories instead of shadowing them.

The bundled SessionStart hooks invoke `harnessup session-start --harness claude` or `--harness codex`. They do nothing unless `CLOUD_AGENT=true` or `CLAUDE_CODE_REMOTE=true`, and stay silent in repositories without a manifest. Startup and clear place files, merge Claude's local settings, link skills, and print startup context and, unless `context.task_tools = false`, a task-tool reminder. Resume, compact and fork print recovery context. All sources report problems without failing the hook. Session start makes no network calls and installs nothing.

File targets are written only when absent or listed in harnessup's current `.git/info/exclude` block. Tracked files and existing foreign targets are skipped. The generated exclude block also covers local settings and skill links; lines outside it survive. Removing a file entry leaves the old target in place and removes its exclude entry. Existing Claude settings keys survive the recursive merge, and invalid JSON is left untouched and reported.

Home files are for configuration a tool reads only from the user's home directory. `harnessup setup` copies each one into place and reports it as a `home_file` item; an existing target with other content is left alone and reported `skipped`, so running setup on a workstation never overwrites the user's own files. Session start never writes them, so a repository's hook cannot reach outside the checkout.

Setup prepares these files before the first session. Changes to settings during a SessionStart hook take effect in a later session because the harness has already read its settings. Cloud snapshot checkout reuse and symlinked skill loading still need acceptance testing in the target cloud environment.

## Setup report

Setup writes `$XDG_STATE_HOME/harnessup/setup.json`, defaulting to `~/.local/state/harnessup/setup.json`. It records the package version, finish time, own plugin registration, repository roots, manifest errors and each item's kind, name, harness, status and command output. Status is `ok`, `failed` or `skipped`. Items skipped by the shared setup deadline carry `setup deadline`; workspace preparation still runs after that deadline. `--deadline SECONDS` overrides the deadline for controlled runs.

Session start reports failures and deadline skips for its repository, checks declared plugin binaries, and reports missing plugins on startup or clear. A corrupt or foreign setup report is ignored. It never reruns installation.

## Plugin bootstraps

A plugin owns version matching and binary installation. harnessup runs its declared bootstrap from the installed plugin root, retrying once after failure. Each attempt runs with `XDG_STATE_HOME` pointed at a fresh copy of the real state directory, so the bootstrap can read existing version stamps. A successful attempt replaces the real state with its copy; a failed attempt discards its copy. Failure stamps from setup therefore cannot disable the plugin's own SessionStart retries in a cloud snapshot. The plugin's normal hook remains responsible for retries after setup.

## Commit a patch

`harnessup commit-patch --patch FILE --message MSG` commits a patch based on HEAD while preserving unrelated staged changes, including independent hunks in the same file. Relative patch paths resolve from the current directory. The helper applies the patch to a private index and leaves working-tree files alone. A stale patch or a patch that conflicts with staged changes exits non-zero without changing HEAD or the shared index.

Define the task in the repository's `devkit.local.toml`:

```toml
[tasks.commit-patch]
description = "Commit a patch against HEAD while preserving unrelated staging"
run = ["harnessup", "commit-patch", "--patch", "{{ patch }}", "--message", "{{ message }}"]
```

Run it with harnessup on PATH:

```sh
devrun task commit-patch --arg patch=/tmp/selected.patch --arg message="fix: commit selected hunk"
```

For a message with a body or coauthor trailers, pass its complete contents with `--arg-file message=FILE`. Hooks and signing run normally. The command requires Git with `merge-tree --write-tree` and `--merge-base` support, and refuses an active merge or rebase, unresolved index entries, and custom or union merge drivers on files changed by both the patch and staging. If commit publication or index installation is uncertain, it retains recovery files and the index lock and reports their paths. Inspect HEAD and recover the index before retrying; HEAD and index updates are not atomic across crashes.

## Development and releases

Run `devrun task verify` for lint, formatting, type checks and tests. Packaging tests build a wheel, check bundled marketplace files and compare plugin versions with the installed package. They also validate the marketplace with Claude when its CLI is available.

Release please updates the Python package and bundled plugin versions together. The repository's `RELEASE_PLEASE_TOKEN` secret must be a fine-grained token with contents and pull requests write permissions, so release PRs trigger CI. Releases use git tags; there is no package registry publishing workflow.
