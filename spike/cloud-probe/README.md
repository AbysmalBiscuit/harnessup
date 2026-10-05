# Cloud probe (throwaway)

Answers two questions before cloud-agent's design depends on them:

1. Is the session's repository cloned when the cloud environment's setup script runs?
2. Does a plugin installed by the setup script load its SessionStart hook, skill, and MCP server in the session? Does a plugin enabled only by the repository's `.claude/settings.json` load once the setup script registered its marketplace?

It also checks that a `uv tool install` from a git URL works in setup and lands on the session's PATH.

| Piece | Tests |
| :- | :- |
| `setup.sh` | Setup phase: user, cwd, repository presence, available tools; installs `cloud-probe-user` and the `cloud-probe` CLI |
| `plugins/cloud-probe-user` | SessionStart hook, skill `probe`, MCP tool `probe_ping`; installed at user scope by `setup.sh` |
| `plugins/cloud-probe-repo` | SessionStart hook; enabled only in `.claude/settings.json` |
| `.claude/settings.json` | Repository SessionStart hook as a control, and the `cloud-probe-repo` enablement |
| `report.sh` | Session phase: every probe log, PATH, installed plugins |

Hooks log to `/var/tmp/cloud-probe` and print a `CLOUD-PROBE:` line, and run only when `CLAUDE_CODE_REMOTE=true`.

## Run

1. Create a cloud environment with Trusted network access and paste `setup.sh` as its setup script.
2. Start a session in that environment on this repository's `cloud-probe` branch with this prompt:

> Run `bash spike/cloud-probe/report.sh` and include its full output verbatim in a code block. Then answer from your own context, not from files: (1) quote every line in your context containing `CLOUD-PROBE`; (2) is a skill named `cloud-probe-user:probe` available; (3) is there an MCP tool whose name contains `probe_ping`? If so, call it and quote the result. Do not change any files.
