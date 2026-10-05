# harnessup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship harnessup v0.1.0: a stdlib-only Python CLI plus a bundled Claude Code / Codex plugin that installs a repository's declared harnesses during a cloud environment's setup script and prepares each session from `.agents/harnessup/manifest.toml`.

**Architecture:** `harnessup setup` runs once in the setup script: it registers its own bundled plugin, then for each repository installs the manifest's marketplaces, plugins, bootstraps, and tools under one deadline, and records every item in `setup.json`. `harnessup session-start` is the bundled plugin's SessionStart hook: it places files, merges settings, links cloud-only skills, rewrites the `.git/info/exclude` block, and prints context plus problems. Modules split by responsibility: parsing, subprocess, state file, workspace writes, harness CLIs, then the two commands.

**Tech Stack:** Python >=3.11 stdlib (`tomllib`, `json`, `subprocess`, `argparse`, `importlib.metadata`), uv with `uv_build`, pytest, ruff, pyrefly, GitHub Actions, release-please.

**Spec:** `docs/superpowers/specs/2026-10-05-harnessup-design.md`. Read it before any task; section names below refer to it.

## Global Constraints

- Runtime dependencies: none. `requires-python = ">=3.11"`; `.python-version` stays `3.14`.
- ruff `target-version = "py311"`; pyrefly `python-version = "3.11"`.
- Build backend `uv_build` (`requires = ["uv_build>=0.11,<0.13"]`); it keeps the `marketplace/` dot-directories in the wheel (checked with uv 0.11.26).
- Entry point `harnessup = "harnessup.cli:main"`; version from `importlib.metadata.version("harnessup")`.
- `setup` always exits 0. `session-start` never exits non-zero and makes no network calls.
- Problem lines are printed as `harnessup: <text>`, one per line.
- Paths in the manifest are relative; `[[file]].source`, `context.*` resolve against `.agents/harnessup/`, `[[file]].target` against the repository root.
- Timeouts: setup deadline 200 s; installs and bootstraps 120 s; tool checks 30 s; plugin checks 2 s; `plugin list --json` calls 10 s; hook timeout 30 s.
- Commit through `devrun task commit --arg files='a b' --arg commit_subject='...' --arg coauthors='<model> <noreply@anthropic.com>'`; raw `git add`/`git commit` are blocked by the devkit harness. Conventional Commits.
- Run commands with absolute paths or `-C`; the worktree is `/home/lev/Git/lev/harnessup_worktrees/swe-12024-cloud-agent-design` (written `$W` below).

## Review Focus

1. **Manifest edits between sessions.** A `[[file]]` removed from the manifest leaves its old target in place and drops it from the exclude block, so it shows as untracked. Expected: the file stays and is reported by git, never deleted. Pinned in Task 4 (`test_removed_file_entry_is_left_alone`).
2. **Hook payload without `source` or `cwd`** (Codex payload shape is unverified). Expected: missing `source` is treated as `startup`, missing `cwd` falls back to the process cwd, and invalid JSON on stdin is treated as `{}`. Pinned in Task 6 (`test_payload_without_source_or_cwd`).
3. **A corrupt or foreign `setup.json`** (truncated write, older schema). Expected: session-start ignores it and still prints context. Pinned in Task 3 (`test_read_items_tolerates_corrupt_file`).
4. **`.claude/settings.local.json` holding invalid JSON** (hand-edited). Expected: left untouched, reported as a problem, other steps still run. Pinned in Task 4 (`test_merge_settings_invalid_json_reported`).
5. **The exclude file missing or `.git/info/` absent** (fresh clone variants, worktrees). Expected: the directory and file are created and the block is appended. Pinned in Task 4 (`test_exclude_block_created_when_missing`).

---

## File Structure

```
pyproject.toml                       modify: build system, script, dev group, tool config
main.py                              delete
README.md                            modify: setup script, manifest reference (Task 9)
devkit.toml                          unchanged
release-please-config.json           create (Task 9)
.release-please-manifest.json        create (Task 9)
.github/workflows/ci.yml             create (Task 1)
.github/workflows/release-please.yml create (Task 9)
src/harnessup/__init__.py            empty
src/harnessup/__main__.py            calls cli.main
src/harnessup/cli.py                 argparse: setup, session-start, --version
src/harnessup/manifest.py            TOML -> frozen dataclasses, strict validation
src/harnessup/proc.py                subprocess with timeout, output tail, Deadline
src/harnessup/state.py               setup.json read/write
src/harnessup/workspace.py           files, settings merge, skill links, exclude block
src/harnessup/harness.py             per-harness CLI argv, plugin lists, plugin roots, task lines
src/harnessup/session.py             session-start: gate, root, prepare, render
src/harnessup/setup.py               setup: discovery, install steps, deadline
src/harnessup/marketplace/...        bundled plugin (Task 7)
tests/conftest.py                    git repo, manifest, CLI runner, stub harness CLIs
tests/test_*.py                      one file per module
```

---

### Task 1: Project scaffold and CI

**Files:**
- Modify: `pyproject.toml`
- Delete: `main.py`
- Create: `src/harnessup/__init__.py`, `src/harnessup/__main__.py`, `src/harnessup/cli.py`, `tests/conftest.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `cli.main(argv: list[str] | None = None) -> int`; `python -m harnessup` runs it via `sys.exit(main())`.
- Produces (tests/conftest.py): fixture `run_cli` returning `Callable[..., subprocess.CompletedProcess[str]]` with signature `run_cli(*args: str, cwd: Path, env: dict[str, str] | None = None, stdin: str = "") -> CompletedProcess[str]`. It runs `[sys.executable, "-m", "harnessup", *args]` with `text=True`, capturing output. Its base environment is `{"HOME": tmp_home, "XDG_STATE_HOME": tmp_state, "PATH": f"{stub_bin}:{Path(sys.executable).parent}:/usr/bin:/bin"}`; the PATH keeps real `claude`/`codex` out. `env` entries are layered on top.
- Produces fixtures `stub_bin: Path` (empty directory first on that PATH) and `state_dir: Path` (`tmp_state / "harnessup"`).

- [ ] **Step 1: Rewrite `pyproject.toml`**

Keep `name`, `version = "0.1.0"`, `description`, `readme`. Set `requires-python = ">=3.11"`, `dependencies = []`, `[project.scripts] harnessup = "harnessup.cli:main"`, `[build-system]` per Global Constraints, `[dependency-groups] dev = ["ruff==0.16.10", "pyrefly==1.3.2", "pytest==9.1.1"]`, `[tool.ruff] target-version = "py311"`, `[tool.ruff.lint] extend-select = ["I", "UP", "B"]`, `[tool.pyrefly] python-version = "3.11"` with `project-includes = ["src", "tests"]`, `[tool.pytest.ini_options] testpaths = ["tests"]`. Delete `main.py`. Run `uv lock --directory $W`.

- [ ] **Step 2: Write the failing test** in `tests/test_cli.py`

```python
def test_version(run_cli, tmp_path):
    result = run_cli("--version", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "harnessup 0.1.0"

def test_unknown_command_exits_2(run_cli, tmp_path):
    assert run_cli("bogus", cwd=tmp_path).returncode == 2
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run --directory $W pytest tests/test_cli.py -v`
Expected: FAIL, `No module named harnessup` or no `--version`.

- [ ] **Step 4: Implement `cli.main`** with an argparse parser (`prog="harnessup"`, `--version` printing `harnessup <version>`) and two subparsers, `setup` and `session-start`, whose handlers print nothing and return 0 for now. Tasks 6 and 8 fill them.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --directory $W pytest tests/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 6: Write `.github/workflows/ci.yml`**

Triggers `pull_request` and `push: branches: [main]`. Top level: `permissions: contents: read` and `concurrency: { group: ci-${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: ${{ github.ref != 'refs/heads/main' }} }`. Three jobs on `ubuntu-latest`, each with `timeout-minutes: 5`, `actions/checkout@v7` and `astral-sh/setup-uv@v10`:
- `lint`: `uv run ruff check` then `uv run ruff format --check`
- `typecheck`: `uv run pyrefly check`
- `test`: matrix `python: ["3.14", "3.11"]`, `uv run --python ${{ matrix.python }} pytest`

- [ ] **Step 7: Verify the whole project**

Run: `devrun task -C $W verify`
Expected: lint, fmt-check, typecheck, test all pass.

- [ ] **Step 8: Commit**: files `pyproject.toml uv.lock main.py src tests .github/workflows/ci.yml`, subject `build: scaffold package and ci`.

---

### Task 2: Manifest parsing and validation

**Files:**
- Create: `src/harnessup/manifest.py`, `tests/test_manifest.py`, `tests/data/manifest_example.toml`
- Modify: `tests/conftest.py` (fixtures `repo`, `write_manifest`)

**Interfaces:**
- Produces:

```python
Harness = Literal["claude", "codex"]
HARNESSES: tuple[Harness, ...] = ("claude", "codex")
MANIFEST_DIR = Path(".agents/harnessup")
MANIFEST_FILE = MANIFEST_DIR / "manifest.toml"

class ManifestError(Exception): ...   # str(e) starts with the key path, e.g. "plugin[1].id: ..."

@dataclass(frozen=True)
class Marketplace: name: str; source: str; harnesses: tuple[Harness, ...]
@dataclass(frozen=True)
class Plugin:
    id: str; harnesses: tuple[Harness, ...]; bootstrap: tuple[str, ...]; check: str | None
    @property
    def name(self) -> str: ...         # part before "@"
    @property
    def marketplace(self) -> str: ...  # part after "@"
@dataclass(frozen=True)
class Tool: name: str; install: str; check: str
@dataclass(frozen=True)
class FileEntry: source: str; target: str
@dataclass(frozen=True)
class Manifest:
    marketplaces: tuple[Marketplace, ...]; plugins: tuple[Plugin, ...]
    tools: tuple[Tool, ...]; files: tuple[FileEntry, ...]
    settings_local: dict[str, object] | None
    startup: str | None; recovery: str | None

def parse(data: dict[str, object]) -> Manifest
def load(root: Path) -> Manifest | None   # None when the manifest file is absent; raises ManifestError (TOML syntax errors included, key path "manifest")
def source_kind(source: str) -> Literal["path", "git", "github"]
```

- Produces (conftest): fixture `repo: Path`, a `git init`ed temp directory with `user.email`/`user.name` set; fixture `write_manifest(root: Path, text: str) -> Path` writing `root/.agents/harnessup/manifest.toml` (dedented).

`bootstrap` is `()` when absent. `source_kind`: `"path"` when it starts with `./`, `"git"` when it contains `://` or starts with `git@`, else `"github"`. `plugin.harnesses` defaults to its marketplace's harnesses.

- [ ] **Step 1: Write the failing tests** in `tests/test_manifest.py`

```python
# tests/data/manifest_example.toml is the spec's example manifest, copied verbatim
FULL = tomllib.loads((Path(__file__).parent / "data/manifest_example.toml").read_text())

def test_parse_spec_example():
    m = parse(FULL)
    assert [p.id for p in m.plugins][:2] == ["devkit@devkit", "mcpls@mcpls"]
    assert m.plugins[1].bootstrap == ("hooks/bootstrap-binaries", "claude-code")
    assert m.plugins[0].check == "devkit --version"
    superpowers = next(p for p in m.plugins if p.name == "superpowers")
    assert superpowers.harnesses == ("claude",)       # inherited from its marketplace
    assert m.plugins[2].harnesses == ("claude", "codex")
    assert m.files[1] == FileEntry("CLAUDE.local.md", "CLAUDE.local.md")
    assert m.settings_local == {"permissions": {"allow": ["Bash(devrun task:*)"]}}
    assert (m.startup, m.recovery) == ("startup.md", "recovery.md")

@pytest.mark.parametrize(("data", "path"), [
    ({}, "schema"),
    ({"schema": 2}, "schema"),
    ({"schema": 1, "extra": 1}, "extra"),
    ({"schema": 1, "plugin": [{"id": "x@missing"}]}, "plugin[0].id"),
    ({"schema": 1, "plugin": [{"id": "noat"}]}, "plugin[0].id"),
    ({"schema": 1, "marketplace": [{"name": "m", "source": "o/r"}], "plugin": [{"id": "p@m", "bootstrap": "hooks/x"}]}, "plugin[0].bootstrap"),
    ({"schema": 1, "marketplace": [{"name": "m", "source": "o/r", "harnesses": ["claude"]}], "plugin": [{"id": "p@m", "harnesses": ["codex"]}]}, "plugin[0].harnesses"),
    ({"schema": 1, "marketplace": [{"name": "m", "source": "o/r", "harnesses": ["vim"]}]}, "marketplace[0].harnesses"),
    ({"schema": 1, "file": [{"source": "../x", "target": "x"}]}, "file[0].source"),
    ({"schema": 1, "file": [{"source": "x", "target": "/etc/x"}]}, "file[0].target"),
    ({"schema": 1, "marketplace": [{"name": "m", "source": "./../m"}]}, "marketplace[0].source"),
    ({"schema": 1, "tool": [{"name": "t", "install": "i"}]}, "tool[0].check"),
    ({"schema": 1, "claude": {"settings_local": []}}, "claude.settings_local"),
    ({"schema": 1, "context": {"startup": 3}}, "context.startup"),
])
def test_validation_names_key_path(data, path):
    with pytest.raises(ManifestError) as err:
        parse(data)
    assert str(err.value).startswith(f"{path}: ")

def test_load_absent_returns_none(repo): assert load(repo) is None
def test_load_syntax_error(repo, write_manifest):
    write_manifest(repo, "schema = ")
    with pytest.raises(ManifestError, match=r"^manifest: "): load(repo)

@pytest.mark.parametrize(("source", "kind"), [("./plugins", "path"), ("https://x/y.git", "git"), ("git@github.com:o/r.git", "git"), ("o/r", "github")])
def test_source_kind(source, kind): assert source_kind(source) == kind
```

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_manifest.py -v`, expected FAIL on import.

- [ ] **Step 3: Implement `manifest.py`.** One small checker per table; a `bool` is not an `int` for `schema`. A relative path is invalid when it is absolute or any part is `..`; for a `path` marketplace source, check the part after `./`.

- [ ] **Step 4: Run to verify they pass**: same command, all pass.

- [ ] **Step 5: Commit**: `src/harnessup/manifest.py tests/test_manifest.py tests/data/manifest_example.toml tests/conftest.py`, subject `feat: parse and validate the manifest`.

---

### Task 3: Subprocess runner, deadline, and setup.json

**Files:**
- Create: `src/harnessup/proc.py`, `src/harnessup/state.py`, `tests/test_proc.py`, `tests/test_state.py`

**Interfaces:**
- Produces (`proc.py`):

```python
@dataclass(frozen=True)
class Outcome:
    returncode: int | None   # None when timed out or the executable was not found
    output: str              # combined stdout+stderr, last 20 lines
    @property
    def ok(self) -> bool: ...  # returncode == 0
    def describe(self) -> str: ...  # "" if ok; "timed out" / "not found" / f"exit {code}", then ": " + output when output

def run(cmd: Sequence[str] | str, *, cwd: Path, timeout: float, env: Mapping[str, str] | None = None) -> Outcome
    # str runs as ["sh", "-c", cmd]; env entries are layered over os.environ; stdin is DEVNULL

class Deadline:
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None
    def remaining(self) -> float          # never negative
    @property
    def expired(self) -> bool
    def clamp(self, timeout: float) -> float  # min(timeout, remaining())
```

On timeout, kill the process group (`start_new_session=True`, `os.killpg`) so `sh -c` children die too.

- Produces (`state.py`):

```python
Status = Literal["ok", "skipped", "failed"]
@dataclass
class Item: kind: str; name: str; harness: str | None; status: Status; detail: str = ""
@dataclass
class RepoReport: root: str; manifest_error: str | None = None; items: list[Item] = field(default_factory=list)

def state_dir() -> Path                 # $XDG_STATE_HOME or ~/.local/state, then /harnessup
def write_report(own: list[Item], repos: list[RepoReport], version: str) -> Path
def read_report_for(root: Path) -> RepoReport | None   # None when the file or the repo entry is missing or unreadable
```

`setup.json` shape: the spec's example plus a top-level `"items"` array (an addition to the spec) for harnessup's own marketplace and plugin registration (`kind` `"self"`). `finished_at` is UTC `%Y-%m-%dT%H:%M:%SZ`. Repo roots match by `Path.resolve()`.

- [ ] **Step 1: Write the failing tests**

```python
# test_proc.py
def test_run_shell_string(tmp_path):
    out = run("echo hi; echo err >&2; exit 3", cwd=tmp_path, timeout=5)
    assert out.returncode == 3 and "hi" in out.output and "err" in out.output
    assert out.describe().startswith("exit 3: ")

def test_run_timeout_kills_children(tmp_path):
    started = time.monotonic()
    out = run("sleep 30 & wait", cwd=tmp_path, timeout=0.5)
    assert out.returncode is None and out.describe().startswith("timed out")
    assert time.monotonic() - started < 5

def test_run_missing_executable(tmp_path):
    assert run(["no-such-binary-xyz"], cwd=tmp_path, timeout=5).describe() == "not found"

def test_output_keeps_last_20_lines(tmp_path):
    out = run("seq 1 50", cwd=tmp_path, timeout=5)
    assert out.output.splitlines() == [str(i) for i in range(31, 51)]

def test_deadline_clamps():
    now = [0.0]
    d = Deadline(10, clock=lambda: now[0])
    assert d.clamp(120) == 10
    now[0] = 11
    assert d.expired and d.remaining() == 0

# test_state.py
def test_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = write_report([], [RepoReport(str(tmp_path / "r"), None, [Item("plugin", "a@b", "claude", "failed", "exit 1")])], "0.1.0")
    assert path == tmp_path / "harnessup" / "setup.json"
    data = json.loads(path.read_text())
    assert data["harnessup_version"] == "0.1.0" and data["items"] == []
    report = read_report_for(tmp_path / "r")
    assert report is not None and report.items[0].status == "failed"
    assert read_report_for(tmp_path / "other") is None

def test_read_items_tolerates_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (tmp_path / "harnessup").mkdir()
    (tmp_path / "harnessup" / "setup.json").write_text('{"repos": [{"root": 1')
    assert read_report_for(tmp_path) is None
```

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_proc.py tests/test_state.py -v`.
- [ ] **Step 3: Implement `proc.py` and `state.py`** to the interfaces above.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit**: `src/harnessup/proc.py src/harnessup/state.py tests/test_proc.py tests/test_state.py`, subject `feat: add subprocess runner and setup state`.

---

### Task 4: Workspace writes

**Files:**
- Create: `src/harnessup/workspace.py`, `tests/test_workspace.py`

**Interfaces:**
- Consumes: `manifest.FileEntry`, `manifest.MANIFEST_DIR`.
- Produces:

```python
EXCLUDE_BEGIN = "# >>> harnessup (generated; rewritten every session)"
EXCLUDE_END = "# <<< harnessup"
SKILL_DIRS = (".claude/skills", ".agents/skills")

@dataclass
class Changes:
    entries: list[str] = field(default_factory=list)   # exclude entries, "/<repo-relative path>"
    problems: list[str] = field(default_factory=list)  # problem text, no "harnessup: " prefix
    def extend(self, other: "Changes") -> None

def is_tracked(root: Path, rel: str) -> bool               # git ls-files --error-unmatch
def read_exclude_block(root: Path) -> set[str]
def write_exclude_block(root: Path, entries: Iterable[str]) -> None
def place_files(root: Path, files: Sequence[FileEntry], owned: set[str]) -> Changes
def merge_settings(root: Path, settings: Mapping[str, object]) -> Changes
def deep_merge(base: dict[str, object], overlay: Mapping[str, object]) -> dict[str, object]
def link_skills(root: Path) -> Changes
```

Rules, from the spec's session-start steps 1 to 4, "Skill links" and "Exclude block":
- `place_files`: a missing source is a problem `missing [[file]] source .agents/harnessup/<source>`. A target is written (parents created, bytes copied, mode kept) when absent or its entry is in `owned`. A tracked target, or one present but not owned, gives problem `skipped <target>: <tracked by git | not written by harnessup>` and no entry.
- `merge_settings`: target `.claude/settings.local.json`. Tracked: skip with a problem. Invalid JSON or a non-object: leave it, problem `skipped .claude/settings.local.json: not a JSON object`. Otherwise write `deep_merge(existing, settings)` with `indent=2` and a trailing newline. Entry `/.claude/settings.local.json` whenever written.
- `deep_merge`: dicts merge recursively, lists gain missing items in overlay order, other overlay values win; `base` is not mutated.
- `link_skills`: for each `.agents/harnessup/skills/<name>/` holding `SKILL.md`, resolve each of `SKILL_DIRS` with `Path.resolve()` after `mkdir(parents=True, exist_ok=True)` (a symlinked `.claude/skills` resolves to the real directory and is not replaced), dedupe, then create `<name>` as a relative symlink (`os.path.relpath` from the resolved directory). An existing symlink that resolves into `.agents/harnessup/skills/` is replaced. A tracked path, or anything else already there, gives problem `skipped skill <name> in <dir>: <tracked by git | exists>`. Entries are the link paths relative to the resolved repo root; a resolved directory outside the repo gives no entry.
- Exclude path from `git -C root rev-parse --git-path info/exclude`, resolved against `root`. The block is replaced in place, lines outside it kept, appended when missing; parent directories created. Entries are written sorted.

- [ ] **Step 1: Write the failing tests** in `tests/test_workspace.py`, using `repo` and helpers to write `.agents/harnessup/<file>` and commit a path (`git add -f` + `git commit` in a fixture helper; tests may run raw git).

```python
def test_place_files_writes_absent_target(repo): ...       # target content equals source; entries == ["/AGENTS.local.md"]
def test_place_files_overwrites_owned(repo): ...           # pre-existing target + owned={"/AGENTS.local.md"} -> overwritten
def test_place_files_skips_foreign(repo): ...              # pre-existing, not owned -> unchanged, problem "skipped AGENTS.local.md: not written by harnessup"
def test_place_files_skips_tracked(repo): ...              # committed target, owned -> unchanged, problem contains "tracked by git"
def test_place_files_missing_source(repo): ...             # problem == "missing [[file]] source .agents/harnessup/nope.md"
def test_removed_file_entry_is_left_alone(repo): ...       # file placed, then place_files with () and write_exclude_block([]) -> target still exists
def test_merge_settings_keeps_existing_keys(repo):
    write(repo / ".claude/settings.local.json", '{"permissions": {"allow": ["Read"]}, "model": "x"}')
    merge_settings(repo, {"permissions": {"allow": ["Bash(devrun task:*)", "Read"]}})
    assert json.loads(read(...)) == {"permissions": {"allow": ["Read", "Bash(devrun task:*)"]}, "model": "x"}
def test_merge_settings_invalid_json_reported(repo): ...   # "{" left as is; problem "skipped .claude/settings.local.json: not a JSON object"
def test_link_skills_shared_dir(repo):                      # .claude/skills -> ../.agents/skills symlink: one link, one entry "/.agents/skills/cloud"
def test_link_skills_separate_dirs(repo):                   # entries == ["/.agents/skills/cloud", "/.claude/skills/cloud"] (sorted), both links resolve to the source
def test_link_skills_creates_missing_dirs(repo): ...
def test_link_skills_never_shadows_tracked(repo): ...       # committed .agents/skills/cloud/SKILL.md -> untouched, problem names "tracked by git"
def test_link_skills_replaces_own_link(repo): ...           # run twice -> no problems second time
def test_exclude_block_idempotent_and_preserves_lines(repo):
    exclude = repo / ".git/info/exclude"
    exclude.write_text("# mine\n*.log\n")
    write_exclude_block(repo, ["/b", "/a"]); write_exclude_block(repo, ["/b", "/a"])
    assert exclude.read_text() == f"# mine\n*.log\n{EXCLUDE_BEGIN}\n/a\n/b\n{EXCLUDE_END}\n"
    assert read_exclude_block(repo) == {"/a", "/b"}
def test_exclude_block_created_when_missing(repo): ...      # rm -r .git/info -> block written
def test_exclude_from_worktree_uses_common_dir(repo, tmp_path): ...  # git worktree add; write via the worktree; block lands in repo/.git/info/exclude
```

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_workspace.py -v`.
- [ ] **Step 3: Implement `workspace.py`.**
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit**: `src/harnessup/workspace.py tests/test_workspace.py tests/conftest.py`, subject `feat: place files, settings, skills, exclude block`.

---

### Task 5: Harness CLIs and stub executables

**Files:**
- Create: `src/harnessup/harness.py`, `tests/test_harness.py`
- Modify: `tests/conftest.py` (fixture `stub`)

**Interfaces:**
- Consumes: `manifest.Harness`, `proc.run`, `proc.Outcome`.
- Produces:

```python
@dataclass(frozen=True)
class HarnessCli:
    name: Harness
    binary: str            # "claude" | "codex"
    task_line: str         # exact text from the spec's task-tool table
    def available(self) -> bool                                     # shutil.which(binary)
    def marketplace_add(self, source: str) -> list[str]             # argv
    def plugin_install(self, plugin_id: str) -> list[str]           # argv
    def installed(self, timeout: float) -> dict[str, Path | None] | None
        # plugin id -> root; None when listing fails

CLIS: dict[Harness, HarnessCli]   # {"claude": ..., "codex": ...}
```

- Claude: `["claude", "plugin", "marketplace", "add", source]`, `["claude", "plugin", "install", id, "--scope", "user"]`; `installed` parses `claude plugin list --json` (array of `{"id", "installPath", ...}`) to `{id: Path(installPath)}`.
- Codex: `["codex", "plugin", "marketplace", "add", source]`, `["codex", "plugin", "add", id]`; `installed` parses `codex plugin list --json`, object with `installed` array of `{"pluginId", "name", "marketplaceName", "version", ...}`, to `{pluginId: codex_home / "plugins/cache" / marketplaceName / name / version}` where `codex_home` is `$CODEX_HOME` or `~/.codex`.

- Produces (conftest): fixture `stub(name: str, *, list_json: object = None, fail: Sequence[str] = ()) -> Path`, which writes an executable Python script `stub_bin/<name>`. The script appends its argv (without argv[0]) as a JSON line to `stub_bin/<name>.log`. When argv is `plugin list --json`, it prints `json.dumps(list_json)`. It exits 1 when any argv element equals an entry in `fail`. Helper `stub_calls(name) -> list[list[str]]` reads the log.

- [ ] **Step 1: Write the failing tests**

```python
def test_claude_installed_maps_install_path(stub):
    stub("claude", list_json=[{"id": "devkit@devkit", "installPath": "/c/devkit"}])
    assert CLIS["claude"].installed(10) == {"devkit@devkit": Path("/c/devkit")}

def test_codex_installed_derives_cache_root(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    stub("codex", list_json={"installed": [{"pluginId": "devkit@devkit", "name": "devkit", "marketplaceName": "devkit", "version": "0.14.10"}], "available": []})
    assert CLIS["codex"].installed(10) == {"devkit@devkit": tmp_path / "cx/plugins/cache/devkit/devkit/0.14.10"}

def test_installed_returns_none_on_failure(stub): ...   # stub exits 1 on "list" -> None

def test_task_lines_match_spec():
    assert CLIS["codex"].task_line.startswith("Track progress with `update_plan`")
    assert "TaskCreate, TaskUpdate, TaskList and TaskGet" in CLIS["claude"].task_line
```

These tests put `stub_bin` first on PATH with `monkeypatch.setenv("PATH", ...)`; `installed` uses `proc.run` with `cwd=Path.cwd()`.

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_harness.py -v`.
- [ ] **Step 3: Implement `harness.py`.** Malformed JSON or a missing key gives `None`.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit**: `src/harnessup/harness.py tests/test_harness.py tests/conftest.py`, subject `feat: wrap claude and codex plugin commands`.

---

### Task 6: `session-start`

**Files:**
- Create: `src/harnessup/session.py`, `tests/test_session.py`
- Modify: `src/harnessup/cli.py`

**Interfaces:**
- Consumes: `manifest.load`, `workspace.*`, `state.read_report_for`, `harness.CLIS`, `proc.run`.
- Produces:

```python
STARTUP_SOURCES = frozenset({"startup", "clear"})   # anything else ("resume", "compact", "fork") is recovery
def in_cloud(env: Mapping[str, str]) -> bool        # CLOUD_AGENT == "true" or CLAUDE_CODE_REMOTE == "true"
def repo_root(payload_cwd: Path, env: Mapping[str, str]) -> Path
def prepare(root: Path, manifest: Manifest) -> Changes   # spec steps 1-4: files, settings, skills, exclude block; setup reuses it
def session_start(harness: Harness, payload: Mapping[str, object], env: Mapping[str, str]) -> str  # the text to print
```

- CLI: `harnessup session-start --harness {claude,codex}` reads stdin (invalid JSON gives `{}`), prints `session_start(...)` when non-empty, and returns 0. Any exception is caught; the CLI then prints `harnessup: session-start failed: <exc>` and returns 0.
- `repo_root`: `CLAUDE_PROJECT_DIR` when set, else `git rev-parse --show-toplevel` from `payload["cwd"]` (default `Path.cwd()`), else that cwd.
- `session_start` returns `""` when not `in_cloud` or `load` gives `None`. A `ManifestError` produces only the context-free output: the task line on startup, plus the problem `manifest error: <msg>`.
- Startup output, joined with blank lines (`"\n\n"`) and empty parts dropped: the stripped startup file contents; the task line; problem lines joined with `"\n"`, each `harnessup: <text>`. Recovery output: the stripped recovery file, then the problems.
- Problems, in this order:
  1. `prepare` problems (startup only);
  2. `missing context file .agents/harnessup/<name>`;
  3. from `read_report_for(root)`, each item that is `failed`, or `skipped` with detail `setup deadline`, as `setup: <kind> <name> [<harness>] <status>: <detail>`, and `setup: manifest error: <msg>` when recorded;
  4. each plugin for this harness with a `check` failing under `proc.run(check, cwd=root, timeout=2)`, as ``` `<check>` failed: <plugin.name>'s binaries are missing; its SessionStart hook retries the install ```;
  5. on startup only, each manifest plugin for this harness absent from `CLIS[harness].installed(10)`, as `<id> not installed; setup reruns when the environment's setup script changes or its cache expires`. A `None` listing adds `could not list installed <harness> plugins`.

- [ ] **Step 1: Write the failing tests** in `tests/test_session.py`, driving `run_cli("session-start", "--harness", h, cwd=repo, env={"CLAUDE_CODE_REMOTE": "true", ...}, stdin=json.dumps({"source": s, "cwd": str(repo)}))` with stub `claude`/`codex` listing every manifest plugin as installed unless the test says otherwise.

```python
def test_gate_off_prints_nothing(...): ...                   # no CLOUD_AGENT/CLAUDE_CODE_REMOTE -> stdout "" and no files written
def test_no_manifest_prints_nothing(...): ...
def test_startup_places_and_prints(...):
    # manifest with [[file]] AGENTS.local.md, settings_local, startup "startup.md" containing "Read AGENTS.local.md"
    out = ...stdout
    assert out.startswith("Read AGENTS.local.md\n\nTrack progress with TaskCreate")
    assert (repo / "AGENTS.local.md").exists() and (repo / ".claude/settings.local.json").exists()
    assert "/AGENTS.local.md" in read_exclude_block(repo)
def test_cloud_agent_env_also_opens_gate(...): ...           # CLOUD_AGENT=true, CLAUDE_CODE_REMOTE unset
@pytest.mark.parametrize("source", ["resume", "compact", "fork"])
def test_recovery_sources_print_recovery_only(...): ...      # stdout starts with recovery text; no task line; AGENTS.local.md not created
def test_codex_task_line(...): ...                           # --harness codex -> "Track progress with `update_plan`"
def test_payload_without_source_or_cwd(...): ...             # stdin "not json", cwd=repo -> treated as startup in repo
def test_setup_failures_scoped_to_repo(...): ...             # setup.json with failed items for repo and for another root -> only this repo's line
def test_plugin_check_failure_reported(...): ...             # check = "exit 1" -> "harnessup: `exit 1` failed: devkit's binaries are missing; ..."
def test_missing_plugin_reported_on_startup_only(...): ...   # stub list lacks mcpls@mcpls -> line on startup, absent on resume
def test_manifest_error_reported(...): ...                   # schema = 2 -> "harnessup: manifest error: schema: ..."
def test_never_exits_nonzero(...): ...                       # .git/info is a file (exclude write fails) -> returncode 0, stdout starts "harnessup: session-start failed"
```

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_session.py -v`.
- [ ] **Step 3: Implement `session.py`** and wire `cli.py`.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit**: `src/harnessup/session.py src/harnessup/cli.py tests/test_session.py`, subject `feat: add session-start hook command`.

---

### Task 7: Bundled plugin and marketplace

**Files:**
- Create under `src/harnessup/marketplace/`: `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, `plugin/.claude-plugin/plugin.json`, `plugin/.codex-plugin/plugin.json`, `plugin/hooks/hooks.json`, `plugin/hooks/hooks-codex.json`
- Create: `tests/test_packaging.py`

**Interfaces:**
- Produces: `MARKETPLACE_DIR = Path(harnessup.__file__).parent / "marketplace"` (exported from `harnessup/__init__.py`); marketplace name `harnessup`; plugin id `harnessup@harnessup`.

Shapes follow devkit's (`/home/lev/Git/lev/devkit/.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, `plugin/.claude-plugin/plugin.json`, `plugin/.codex-plugin/plugin.json`). The plugin source is `./plugin`. Both `plugin.json` files carry `"version": "0.1.0"`, which release-please bumps. The Codex `plugin.json` sets `"hooks": "./hooks/hooks-codex.json"`.

Hook files: SessionStart has no matcher and a single command hook, `harnessup session-start --harness claude` (Claude, `"timeout": 30`) or `--harness codex` (Codex, `"timeout": 30, "async": false`).

- [ ] **Step 1: Write the failing tests** in `tests/test_packaging.py`

```python
def test_versions_match_package():
    v = importlib.metadata.version("harnessup")
    for rel in ["plugin/.claude-plugin/plugin.json", "plugin/.codex-plugin/plugin.json"]:
        assert json.loads((MARKETPLACE_DIR / rel).read_text())["version"] == v

def test_hooks_call_session_start():
    claude = json.loads((MARKETPLACE_DIR / "plugin/hooks/hooks.json").read_text())
    assert claude["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "harnessup session-start --harness claude"
    # same for hooks-codex.json with --harness codex

def test_wheel_contains_marketplace(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(PROJECT_ROOT)], check=True)
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    for rel in ["marketplace/.claude-plugin/marketplace.json", "marketplace/.agents/plugins/marketplace.json",
                "marketplace/plugin/.claude-plugin/plugin.json", "marketplace/plugin/.codex-plugin/plugin.json",
                "marketplace/plugin/hooks/hooks.json", "marketplace/plugin/hooks/hooks-codex.json"]:
        assert f"harnessup/{rel}" in names

@pytest.mark.skipif(shutil.which("claude") is None, reason="claude not on PATH")
def test_claude_validates_marketplace():
    assert subprocess.run(["claude", "plugin", "validate", str(MARKETPLACE_DIR)]).returncode == 0
```

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_packaging.py -v`.
- [ ] **Step 3: Write the six JSON files and `MARKETPLACE_DIR`.**
- [ ] **Step 4: Run to verify they pass** (the validate test runs locally because `claude` is on the real PATH).
- [ ] **Step 5: Commit**: `src/harnessup/marketplace src/harnessup/__init__.py tests/test_packaging.py`, subject `feat: bundle the harnessup plugin`.

---

### Task 8: `setup`

**Files:**
- Create: `src/harnessup/setup.py`, `tests/test_setup.py`
- Modify: `src/harnessup/cli.py`

**Interfaces:**
- Consumes: `manifest.*`, `proc.run/Deadline/Outcome`, `state.*`, `harness.CLIS`, `session.prepare`, `MARKETPLACE_DIR`.
- Produces:

```python
DEADLINE_S = 200.0
INSTALL_TIMEOUT_S = 120.0
CHECK_TIMEOUT_S = 30.0
def discover(repos: Sequence[Path], cwd: Path) -> list[Path]
def setup(repos: Sequence[Path], cwd: Path, deadline: Deadline) -> Path   # returns the setup.json path
```

- CLI: `harnessup setup [--repo PATH]... [--deadline SECONDS]` (default `DEADLINE_S`; the flag exists so tests can shorten it). It always returns 0. An unexpected exception still writes `setup.json` with the exception as a `self` item named `setup`, status `failed`.
- `discover`: the `--repo` paths when given; else `[cwd]` when `cwd / MANIFEST_FILE` exists; else sorted direct children of `cwd` that have it.
- Own registration, once, for each available harness: `marketplace_add(str(MARKETPLACE_DIR))`, then `plugin_install("harnessup@harnessup")`; items of kind `self`.
- Per repository, spec "Commands > setup" steps 1 to 5, items in this order:
  1. `marketplace` items. A `path` source is passed as `str((root / source).resolve())`, and every marketplace subprocess runs with `cwd=root`.
  2. `plugin` items.
  3. `bootstrap` items, one per plugin with a bootstrap. The root comes from Claude's `installed` when the plugin was installed for Claude, else from Codex's. argv[0] is joined to the root, then run with `cwd=root_of_plugin` and `env={"HARNESSUP_SETUP": "1"}`, and retried once on failure. The item's `harness` is the one whose root was used. A plugin with no `ok` install item gets `skipped` with detail `plugin failed to install`; no root found gets `failed` with detail `plugin root not found`.
  4. `tool` items: `check` under `CHECK_TIMEOUT_S`, then `install` under `INSTALL_TIMEOUT_S` when the check fails, both via `sh -c` from the repo root; `ok` with detail `already installed` when the check passes.
  5. `session.prepare(root, manifest)`, which runs regardless of the deadline.
- Each item: a harness not `available()` gives `skipped` with detail `<binary> not on PATH`. An expired deadline gives `skipped` with detail `setup deadline`. Otherwise the timeout is `deadline.clamp(...)`, and status `ok`/`failed` follows `Outcome.ok`, with detail `Outcome.output` on success and `Outcome.describe()` on failure.
- An invalid manifest gives `RepoReport(manifest_error=str(err))` and no items.

- [ ] **Step 1: Write the failing tests** in `tests/test_setup.py`, using the stub fixture plus a fake plugin directory `tmp_path / "plugins/devkit"`. Its `hooks/bootstrap-binaries` script appends `"$@ HARNESSUP_SETUP=$HARNESSUP_SETUP"` to a log; the stub `claude` lists it as `installPath`.

```python
def test_registers_own_plugin_then_repo_items(...):
    calls = stub_calls("claude")
    assert calls[0] == ["plugin", "marketplace", "add", str(MARKETPLACE_DIR)]
    assert calls[1] == ["plugin", "install", "harnessup@harnessup", "--scope", "user"]
    assert ["plugin", "install", "devkit@devkit", "--scope", "user"] in calls
def test_bootstrap_runs_with_args_and_env(...): ...          # log == "claude-code HARNESSUP_SETUP=1"
def test_bootstrap_retried_once(...): ...                     # bootstrap fails first run, succeeds second -> item ok, log has 2 lines
def test_bootstrap_skipped_when_install_failed(...): ...      # stub fail=["devkit@devkit"] -> bootstrap item skipped "plugin failed to install"
def test_codex_absent_is_skipped(...): ...                    # no codex stub -> codex items skipped "codex not on PATH"
def test_tool_install_only_when_check_fails(...): ...         # check "exit 0" -> install not run; check "exit 1" -> install marker file exists
def test_deadline_skips_remaining(...): ...                   # --deadline 1, first tool check "sleep 3" -> later items skipped "setup deadline"; AGENTS.local.md still placed
def test_path_marketplace_is_absolute(...): ...               # source "./local-mp" -> add called with str(repo / "local-mp")
def test_invalid_manifest_recorded_exit_0(...): ...           # schema = 2 -> returncode 0, manifest_error starts "schema: "
def test_discovers_children_of_cwd(...): ...                  # cwd=tmp_path with repos a/ (manifest) and b/ (none) -> only a in setup.json
def test_exit_0_when_everything_fails(...): ...               # stub fail on every argv -> returncode 0, all items failed or skipped
```

- [ ] **Step 2: Run to verify they fail**: `uv run --directory $W pytest tests/test_setup.py -v`.
- [ ] **Step 3: Implement `setup.py`** and wire `cli.py`.
- [ ] **Step 4: Run to verify they pass**, then `devrun task -C $W verify`.
- [ ] **Step 5: Commit**: `src/harnessup/setup.py src/harnessup/cli.py tests/test_setup.py tests/conftest.py`, subject `feat: add setup command`.

---

### Task 9: Releases and README

**Files:**
- Create: `release-please-config.json`, `.release-please-manifest.json`, `.github/workflows/release-please.yml`
- Modify: `README.md`

- [ ] **Step 1: Write `release-please-config.json`** with `$schema` as in devkit, `include-component-in-tag: false`, and `packages["."]`: `release-type: python`, `package-name: harnessup`, `bump-minor-pre-major: true`, `bump-patch-for-minor-pre-major: true`, `extra-files` of type `json` with `jsonpath: "$.version"` for `src/harnessup/marketplace/plugin/.claude-plugin/plugin.json` and `src/harnessup/marketplace/plugin/.codex-plugin/plugin.json`. `.release-please-manifest.json`: `{".": "0.1.0"}`.

- [ ] **Step 2: Write `release-please.yml`** on `push: branches: [main]`, `permissions: contents: write, pull-requests: write`, `concurrency: { group: release-please, cancel-in-progress: false }`, and one job with `timeout-minutes: 5` running `googleapis/release-please-action@v5` with `config-file`/`manifest-file`. It uses the default `GITHUB_TOKEN`: no workflow is triggered by the tag, so devkit's PAT is not needed.

- [ ] **Step 3: Rewrite `README.md`**: one-paragraph purpose; the setup script; the manifest location, the spec's example manifest and its fields table; cloud-only skills; what `setup.json` holds and where; a "Plugin bootstraps" note stating the `HARNESSUP_SETUP=1` contract. The setup script is:

```sh
uv tool install git+https://github.com/AbysmalBiscuit/harnessup
harnessup setup
```

  Keep it timeless: no counts.

- [ ] **Step 4: Validate**: `jq . $W/release-please-config.json`, `yq . $W/.github/workflows/*.yml`, and `devrun task -C $W verify`, all succeed.

- [ ] **Step 5: Commit**: `release-please-config.json .release-please-manifest.json .github/workflows/release-please.yml README.md`, subject `ci: add release-please and document usage`.

---

## Outside this plan (spec Rollout)

- Rollout 1 (devkit and mcpls `bootstrap-binaries` honour `HARNESSUP_SETUP`, devkit bootstrap emits `additionalContext`) are changes in other repositories. They must land before the acceptance run. harnessup's own code and tests do not depend on them.
- Rollout 3 and 4 (commit-patch into devkit, devkit dogfood), the acceptance run in a real cloud environment, and deleting `cloud-probe` follow v0.1.0.

## Unresolved questions

1. Codex SessionStart payload shape is unverified; the plan treats a missing `source` as `startup` and a missing `cwd` as the process cwd. Acceptable until a Codex cloud run?
2. The `--deadline` flag on `setup` is not in the spec; it exists for tests. Keep it public, or hide it with `argparse.SUPPRESS`?
3. release-please uses `GITHUB_TOKEN`, so the release PR's CI will not trigger automatically. That is fine for a project with no tag-triggered workflow, but do you want devkit's `RELEASE_PLEASE_TOKEN` PAT here so the release PR gets CI?
