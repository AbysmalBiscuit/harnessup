import subprocess
import sys
import textwrap
from collections.abc import Callable
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
