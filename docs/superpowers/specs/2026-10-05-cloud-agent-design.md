# cloud-agent design

- Issues: [SWE-12024](https://linear.app/adaptyv-bio/issue/SWE-12024/package-cloud-agent-bootstrapping-framework) (this framework), [SWE-12480](https://linear.app/adaptyv-bio/issue/SWE-12480/install-cloud-agent-in-the-cloud-container) (monorepo install), parent [SWE-12018](https://linear.app/adaptyv-bio/issue/SWE-12018/bootstrap-cloud-agent-sandbox-with-the-harnesses)
- Prototype: `AbysmalBiscuit/devkit`, `.agents/skills/cloud/`
- Date: 2026-10-05

## Goal

Every cloud session starts already steered: the harnesses (devkit, mcpls, agent-guard, testplan, and whatever comes next) are installed and active before the agent reads its task, with no step done by hand.

The devkit prototype does this by bundling scripts, templates, and hook settings inside each repository's `.agents/` directory. cloud-agent extracts the mechanism into one installable tool. A repository then carries only a manifest and its own content.

### Success criteria

- A cloud environment's setup script is two lines that never change per repository.
- A repository opts in by committing `.agents/cloud-agent/manifest.toml` and the files it references.
- A fresh cloud session in the adaptyv monorepo starts with every manifest plugin loaded, its binaries on PATH, its files in place, and its startup context injected (the SWE-12480 done-when).
- The same package works as a Claude Code plugin and ships a Codex plugin.

### Ownership

cloud-agent is pure mechanism. It installs, copies, links, and prints. All content (standing rules, workflow skill, recovery text, devkit config, settings) belongs to the repository.

## Environment facts

Measured with a probe session in an Anthropic-hosted Claude cloud environment (branch `cloud-probe` of this repository) on 2026-10-05. The design depends on each of these.

| Fact | Evidence |
| :- | :- |
| The repository is cloned **before** the setup script runs | Session checklist: "Cloned repository", then "Ran setup script", then "Started Claude Code"; setup saw `/home/user/cloud-agent/.git` |
| The setup script's cwd is `/home/user`, the clone's parent, and `CLAUDE_PROJECT_DIR` is unset | Setup log |
| Setup and session both run as root, `HOME=/root` | `id` in both phases |
| `/root/.local/bin` and `/root/.cargo/bin` are on PATH in both phases | Setup and session PATH |
| Python 3.11.15, uv 0.8.17, Claude Code 2.1.289, `gh`, `git` are preinstalled | Setup log |
| A plugin installed at user scope by the setup script loads its SessionStart hook, skills, and MCP server | Hook marker, skill listed, MCP tool answered |
| A plugin enabled only in the repository's `enabledPlugins` loads once the setup script registered its marketplace | Its SessionStart hook ran |
| A directory marketplace is read in place, not copied to the plugin cache | `claude plugin list` reports "Read from: /opt/cloud-probe/..." |
| `uv tool install git+https://github.com/...` works in setup and its executable is on the session's PATH | Setup log and session `command -v` |
| `CLAUDE_CODE_REMOTE=true` is set in both phases | Setup log, session |
| `SKIP_PLUGIN_MARKETPLACE` is set: Claude Code does not fetch marketplaces the repository declares | Setup env names; matches the documented "cloud sessions don't install repository plugins" |

From the Claude Code docs: the setup script must exit 0 or the session fails to start; a setup finishing within roughly five minutes is snapshotted and reused, skipping the setup script for later sessions until the script or network settings change or the cache expires; SessionStart hooks run on every session, including resumed ones.

Not yet measured: whether a session started from a cached snapshot re-clones the repository or reuses the snapshot's checkout. The design holds either way (per-session work runs at SessionStart), and the SWE-12480 acceptance run measures it.

## Architecture

```
cloud environment setup script
  uv tool install git+https://github.com/AbysmalBiscuit/cloud-agent[@vX.Y.Z]
  cloud-agent setup
      |- registers cloud-agent's own plugin (directory marketplace inside the installed package)
      |- adds manifest marketplaces, installs manifest plugins (per harness)
      |- runs each plugin's own bootstrap script (binaries, version-matched by the plugin)
      |- runs manifest tool installs whose check fails
      |- does the per-session work (below)
      `- records every item's result in setup.json, exits 0

Claude Code / Codex launches, then the cloud-agent plugin's SessionStart hook runs
  cloud-agent session-start --harness claude|codex
      |- copies [[file]] entries, writes .claude/settings.local.json
      |- links cloud-only skills, rewrites the .git/info/exclude block
      `- prints startup or recovery context, task-tool line, problems
```

Version matching between a plugin and its binaries is the plugin's job. devkit and mcpls each ship `hooks/bootstrap-binaries`, which reads the plugin's own `plugin.json` version, installs the matching release, writes a `bootstrap-version` stamp under `$XDG_STATE_HOME/<app>/`, and upgrades when the plugin version moves. cloud-agent runs that script during setup so the binaries exist before the first session's MCP servers start; afterwards the plugin's own SessionStart hook keeps them current.

cloud-agent's own plugin ships inside the Python package and is registered as a directory marketplace pointing into the installed package, so the hooks always run against the same version as the CLI.

## Manifest

Location: `.agents/cloud-agent/manifest.toml`. Paths in `[[file]]` sources and `[context]` are relative to `.agents/cloud-agent/`; `[[file]]` targets are relative to the repository root.

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

[[plugin]]
id = "devkit@devkit"
bootstrap = "hooks/bootstrap-binaries"

[[plugin]]
id = "mcpls@mcpls"
bootstrap = "hooks/bootstrap-binaries"

[[plugin]]
id = "agent-guard@agent-guard"

[[tool]]
name = "testplan"
install = "uv tool install git+https://github.com/adaptyv-bio/agent_pr_testing"
check = "testplan --help"

[[file]]
source = "AGENTS.local.md"
target = "AGENTS.local.md"

[[file]]
source = "devkit.local.toml"
target = "devkit.local.toml"

[claude.settings_local]
permissions = { allow = ["Bash(devrun task:*)"] }

[context]
startup = "startup.md"
recovery = "recovery.md"
```

### Fields

| Key | Type | Required | Meaning |
| :- | :- | :- | :- |
| `schema` | int | yes | Manifest schema version; `1` |
| `marketplace.name` | str | yes | Marketplace name, as plugin ids reference it |
| `marketplace.source` | str | yes | GitHub `owner/repo`, a git URL, or a path relative to the repository root |
| `marketplace.harnesses` | list of `"claude"`, `"codex"` | no, default both | Harnesses to register it with |
| `plugin.id` | str | yes | `name@marketplace` |
| `plugin.harnesses` | list | no, default both | Harnesses to install it into |
| `plugin.bootstrap` | str | no | Executable path inside the installed plugin, run once during setup |
| `tool.name` | str | yes | Label for reporting |
| `tool.install` | str | yes | Shell command, run with `sh -c` from the repository root |
| `tool.check` | str | yes | Shell command; exit 0 means installed, so `install` is skipped |
| `file.source` | str | yes | File under `.agents/cloud-agent/` |
| `file.target` | str | yes | Destination under the repository root |
| `claude.settings_local` | table | no | Written as JSON to `.claude/settings.local.json` |
| `context.startup` | str | no | Printed on `startup` and `clear` |
| `context.recovery` | str | no | Printed on `resume` and `compact` |

Validation is strict: an unknown key, an unsupported `schema`, a missing required key, a wrong type, a plugin id whose marketplace is not declared, or a path that is absolute or contains `..` is an error naming the key path. A harness absent from the machine is not an error; its items are skipped.

Plugins, binaries, tools, and settings are declared only here. cloud-agent does not read the repository's `.claude/settings.json`.

### Cloud-only skills

Each directory under `.agents/cloud-agent/skills/` is a skill (`<name>/SKILL.md`). These need no manifest entry.

## Commands

### `cloud-agent setup [--repo PATH]...`

Runs in the environment's setup script. Ungated: it only ever runs where it was put.

Repository discovery: each `--repo`; with none, the cwd when it contains `.agents/cloud-agent/manifest.toml`; otherwise every direct child of the cwd that does. Several repositories are processed in turn.

First, once: register cloud-agent's own marketplace (the package's `marketplace/` directory) with each harness CLI on PATH, and install the `cloud-agent` plugin at user scope.

Then per repository, in order:

1. Add each `[[marketplace]]` to each of its harnesses.
2. Install each `[[plugin]]` at user scope into each of its harnesses.
3. For each plugin with `bootstrap`, run that script once from the installed plugin root. Claude reports the root as `installPath` in `claude plugin list --json`. The script is skipped when the plugin failed to install.
4. For each `[[tool]]`, run `check`; run `install` when it fails.
5. Run the per-session work of `session-start` for `startup`, without printing context.

Each subprocess has a timeout (installs and bootstraps 240 s, checks 30 s) so one stuck item cannot push setup past the cache limit. A failing item is recorded and setup moves on. Setup always exits 0.

Harness CLI commands:

| Step | Claude | Codex |
| :- | :- | :- |
| Add marketplace | `claude plugin marketplace add <source>` | `codex plugin marketplace add <source>` |
| Install plugin | `claude plugin install <id> --scope user` | `codex plugin add <id>` |

#### `setup.json`

Written to `$XDG_STATE_HOME/cloud-agent/setup.json` (default `~/.local/state`):

```json
{
  "cloud_agent_version": "0.1.0",
  "finished_at": "2026-10-05T09:54:15Z",
  "repos": [
    {
      "root": "/home/user/adaptyv",
      "manifest_error": null,
      "items": [
        {"kind": "plugin", "name": "devkit@devkit", "harness": "claude", "status": "ok", "detail": ""},
        {"kind": "bootstrap", "name": "devkit@devkit", "harness": "claude", "status": "failed", "detail": "exit 1: ..."},
        {"kind": "plugin", "name": "devkit@devkit", "harness": "codex", "status": "skipped", "detail": "codex not on PATH"}
      ]
    }
  ]
}
```

`status` is `ok`, `skipped`, or `failed`; `detail` holds the last lines of output for a failure.

### `cloud-agent session-start --harness claude|codex`

The cloud-agent plugin's SessionStart hook: `cloud-agent session-start --harness claude` in `hooks/hooks.json`, `--harness codex` in `hooks/hooks-codex.json`, timeout 10 s. It reads the hook JSON from stdin and takes `source` from it.

Gate: it does nothing unless `CLOUD_AGENT=true` or `CLAUDE_CODE_REMOTE=true`.

Repository root: `CLAUDE_PROJECT_DIR` when set, otherwise the git top level of the hook payload's `cwd`.

On `startup` and `clear`:

1. Copy each `[[file]]` source to its target, creating parent directories and overwriting.
2. Write `claude.settings_local` to `.claude/settings.local.json` when the table is present.
3. Link cloud-only skills (below).
4. Rewrite the exclude block (below).
5. Print, in order: the `context.startup` file; the harness's task-tool line; one line per problem.

On `resume` and `compact`: print the `context.recovery` file, then one line per problem.

Problems are: a manifest error; a missing `[[file]]` or context source; a skipped skill link; each `failed` item in `setup.json`; each manifest plugin that is not installed for this harness ("installs next session, after the environment cache rebuilds"), read from `claude plugin list --json` for Claude and skipped for Codex until its CLI's equivalent is confirmed. session-start makes no network calls and installs nothing. It never exits non-zero.

Task-tool lines:

| Harness | Line |
| :- | :- |
| `claude` | Track progress with TaskCreate, TaskUpdate, TaskList and TaskGet, alongside the workflow's progress ledger, so the work stays visible while the session runs. |
| `codex` | Track progress with `update_plan`, alongside the workflow's progress ledger, so the work stays visible while the session runs. |

#### Skill links

For a skill `.agents/cloud-agent/skills/<name>/`:

1. Candidate directories are `.claude/skills/` (Claude) and `.agents/skills/` (Codex), always both.
2. Resolve each candidate with `realpath`, creating it as a plain directory when missing, and drop duplicates. A repository whose `.claude/skills` symlinks to `.agents/skills` gets one directory; one with separate directories gets two.
3. In each resolved directory, create `<name>` as a relative symlink to the skill's source directory, replacing a previous cloud-agent link.
4. When `<name>` in that directory is tracked by git, or is a file or directory cloud-agent did not create, skip it and report the collision. A tracked skill is never shadowed.

Symlinks rather than copies: Claude Code follows symlinked skill directories (documented), so there is one source and nothing goes stale.

#### Exclude block

Generated paths are ignored through `.git/info/exclude`, resolved with `git rev-parse --git-path info/exclude`, not through the tracked `.gitignore`: editing `.gitignore` would leave a modified tracked file in every session for an agent to commit. The exclude file lives in the common git directory, so it also covers worktrees.

```
# >>> cloud-agent (generated; rewritten every session)
/AGENTS.local.md
/devkit.local.toml
/.claude/settings.local.json
/.agents/skills/cloud
# <<< cloud-agent
```

Entries are every `[[file]]` target, `.claude/settings.local.json` when written, and each skill link path inside the repository. The block is replaced in place on every run; lines outside it are preserved; a missing block is appended.

## Package layout

```
AbysmalBiscuit/cloud-agent
|- pyproject.toml
|- src/cloud_agent/
|  |- cli.py                 # argparse entry point: setup, session-start
|  |- manifest.py            # TOML to frozen dataclasses; strict validation
|  |- harness.py             # per harness: CLI commands, skill directory, task-tool line
|  |- setup.py               # setup steps, setup.json
|  |- session.py             # files, settings, skill links, exclude block, context
|  `- marketplace/           # package data
|     |- .claude-plugin/marketplace.json
|     |- .agents/plugins/marketplace.json
|     `- plugin/
|        |- .claude-plugin/plugin.json
|        |- .codex-plugin/plugin.json
|        `- hooks/
|           |- hooks.json
|           `- hooks-codex.json
`- tests/
```

- Standard library only at runtime (`tomllib`, `json`, `subprocess`, `argparse`, `importlib.metadata`).
- `requires-python = ">=3.11"`, the cloud image's interpreter, so the install never downloads one. `.python-version` is `3.14`; development and the primary CI target use the latest stable Python. ruff `target-version = "py311"` and pyrefly `python-version = "3.11"` reject syntax newer than the floor.
- Build backend `uv_build`. A packaging test asserts the wheel contains the `marketplace/` dot-directories; if the backend drops them, switch to `hatchling`.
- Entry point `cloud-agent = "cloud_agent.cli:main"`. The version comes from `importlib.metadata`.
- Install: `uv tool install git+https://github.com/AbysmalBiscuit/cloud-agent` follows `main`; `@vX.Y.Z` pins a release. No wheel or PyPI publishing: the git install builds in under a second, and the name `cloud-agent` is likely refused by PyPI as too close to the existing `cloudagent`.

## Errors

| Where | Failure | Behaviour |
| :- | :- | :- |
| setup | One item fails | Recorded as `failed`; later items still run; a plugin's bootstrap is skipped when the plugin failed |
| setup | Harness CLI absent | That harness's items recorded as `skipped` |
| setup | Subprocess exceeds its timeout | Killed, recorded as `failed` |
| setup | Manifest invalid | Nothing installed for that repository; `manifest_error` recorded |
| setup | Unexpected exception | Caught at the top level, recorded, exit 0 |
| session-start | Any problem | One context line each; exit 0 |
| session-start | Not in a cloud | No output, exit 0 |

## Testing

pytest, driving the real CLI entry point against temporary git repositories.

- `session-start`: hook JSON on stdin; files copied; settings written; recovery printed on `resume`; silent with the gate off; skill links with `.claude/skills` symlinked to `.agents/skills`, with separate directories, and with `.claude/skills` missing; a tracked skill name skipped and reported; the exclude block rewritten idempotently while preserving lines outside it; problems from `setup.json` and missing plugins reported.
- `setup`: stub `claude` and `codex` executables on PATH that record their argv and emit `plugin list --json`; a stub plugin whose bootstrap records that it ran; one failing item does not stop the rest; `setup.json` contents; exit 0 on failure and on an invalid manifest; repository discovery from a parent cwd.
- Manifest: each validation error names its key path.
- Packaging: build the wheel and assert the marketplace files are present; run `claude plugin validate` on the marketplace directory when `claude` is on PATH, skip otherwise.

### CI

`.github/workflows/ci.yml` on pull requests and pushes to `main`:

- `concurrency: { group: ci-${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: ${{ github.ref != 'refs/heads/main' }} }`: a new push cancels the superseded run on a branch; every `main` commit gets a complete run.
- `permissions: contents: read`.
- Jobs, each with `timeout-minutes: 5`, using `astral-sh/setup-uv`: lint (`ruff check`, `ruff format --check`), typecheck (`pyrefly check`), test (`pytest`, matrix Python 3.14 and 3.11).
- ruff, pyrefly, and pytest are pinned in the `dev` dependency group.

### Releases

release-please, as in devkit:

- `release-please-config.json`: `release-type: python`, `package-name: cloud-agent`, `include-component-in-tag: false`, `bump-minor-pre-major: true`, `bump-patch-for-minor-pre-major: true` (before 1.0, a breaking change bumps minor and everything else bumps patch), and `extra-files` updating `$.version` in both `plugin.json` files.
- `.github/workflows/release-please.yml` on pushes to `main`: `googleapis/release-please-action@v5`, `timeout-minutes: 5`, `concurrency: { group: release-please, cancel-in-progress: false }`.

### Acceptance

Manual, in a real cloud environment: a fresh session, then a second session from the cached snapshot. Proof is `setup.json`, the injected startup context, and `claude plugin list`. The second session also records whether the checkout was re-cloned.

## Rollout

1. **cloud-agent v0.1.0** (SWE-12024): implement this spec, merge with CI green, release. The README documents the setup script and the manifest. Then delete the `cloud-probe` branch and the probe environment.
2. **commit-patch moves into devkit** (a devkit issue): `git-commit-patch.py` backs devkit's `commit-patch` task and belongs with devkit. cloud-agent does not depend on it.
3. **devkit dogfoods cloud-agent**: replace `.agents/skills/cloud/` with `.agents/cloud-agent/` (manifest, `AGENTS.local.md`, `devkit.local.toml`, `startup.md`, `recovery.md`, `skills/cloud/`); remove the cloud SessionStart and Setup hooks from devkit's `.claude/settings.json` and the `cloud` CI job. Verify with a fresh devkit cloud session.
4. **Monorepo** (SWE-12480): commit `.agents/cloud-agent/` with devkit, mcpls, agent-guard and superpowers plugins, the testplan tool, and the content files; set the environment's setup script; run the acceptance check.

## Dropped from the prototype

| Prototype piece | Replacement |
| :- | :- |
| `install_plugin_release` and `DEVKIT_INSTALL_DIR` / `MCPLS_INSTALL_DIR` | The plugin's own `bootstrap-binaries`, run by setup; binaries land in `CARGO_HOME`, already on PATH |
| `commit-msg` attribution hook | devkit's command guard blocks raw `git commit`, and its `commit` task requires `coauthors` from agents |
| `git-commit-patch.py` | Moves into devkit |
| `@COMMIT_HELPER@` template substitution | None; `[[file]]` is a plain copy |
| `CLOUD_AGENT_TYPE` | `--harness` in each harness's hook file |
| devkit presence report | `setup.json` problems; each plugin's bootstrap reports its own binaries |
| Claude `Setup` hook and `--handoff` | None |

## Out of scope

- Version pinning of plugins or marketplaces; plugins follow their marketplace's default branch.
- Codex settings files.
- Carrying cloud-only skills into worktrees created inside the session.
- Verifying Codex end to end in a Codex cloud environment.

## Known gaps

- Codex skips plugin hooks until a person trusts them (agent-guard's README), which may stop cloud-agent's Codex hook from running in an unattended cloud session.
- Whether Codex follows symlinked skill directories is unverified.
- Locating an installed plugin's root through the Codex CLI is unverified; a plugin installed only for Codex may have its bootstrap skipped until this is known.
- testplan installs from a private repository. Whether `uv tool install git+https://github.com/<private>` authenticates through the cloud GitHub proxy is untested.

## Unresolved questions

1. What form does commit-patch take in devkit: a built-in subcommand, or the Python helper shipped in devkit's plugin? Until it ships, the monorepo's `devkit.local.toml` either drops the `commit-patch` task or carries the helper as a repository file.
2. Who creates the org-shared cloud environment for the monorepo, so sessions other people start are bootstrapped too?
3. If the private testplan install fails in setup, is testplan published, or is a deploy token stored in an environment variable?
