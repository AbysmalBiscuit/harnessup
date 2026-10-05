import json
import subprocess
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state" / "harnessup"


@pytest.fixture
def run_cli(
    tmp_path: Path, stub_bin: Path, state_dir: Path
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def invoke(
        *args: str, cwd: Path, env: dict[str, str] | None = None, stdin: str = ""
    ) -> subprocess.CompletedProcess[str]:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        environment = {
            "HOME": str(home),
            "XDG_STATE_HOME": str(state_dir.parent),
            "PATH": f"{stub_bin}:{Path(sys.executable).parent}:/usr/bin:/bin",
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        }
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, "-m", "harnessup", *args],
            cwd=cwd,
            env=environment,
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )

    return invoke


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "core.excludesfile", "/dev/null"], check=True
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    return root


@pytest.fixture
def write_manifest() -> Callable[[Path, str], Path]:
    def write(root: Path, text: str) -> Path:
        path = root / ".agents/harnessup/manifest.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
        return path

    return write


@pytest.fixture
def stub(stub_bin: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    monkeypatch.setenv(
        "PATH", f"{stub_bin}:{Path(sys.executable).parent}:/usr/bin:/bin"
    )

    def create(
        name: str, *, list_json: object = None, fail: Sequence[str] = ()
    ) -> Path:
        path = stub_bin / name
        path.write_text(f"""#!{sys.executable}
import json
import sys
from pathlib import Path
args = sys.argv[1:]
with Path(__file__).with_suffix(".log").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if any(arg in {tuple(fail)!r} for arg in args):
    sys.exit(1)
if args == ["plugin", "list", "--json"]:
    print(json.dumps({list_json!r}, indent=2))
""")
        path.chmod(0o755)
        return path

    return create


@pytest.fixture
def stub_calls(stub_bin: Path) -> Callable[[str], list[list[str]]]:
    def read(name: str) -> list[list[str]]:
        path = stub_bin / f"{name}.log"
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    return read
