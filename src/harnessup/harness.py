import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from harnessup.manifest import Harness
from harnessup.proc import run


@dataclass(frozen=True)
class HarnessCli:
    name: Harness
    binary: str
    task_line: str

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def marketplace_add(self, source: str) -> list[str]:
        return [self.binary, "plugin", "marketplace", "add", source]

    def plugin_install(self, plugin_id: str) -> list[str]:
        if self.name == "claude":
            return [self.binary, "plugin", "install", plugin_id, "--scope", "user"]
        return [self.binary, "plugin", "add", plugin_id]

    def installed(self, timeout: float) -> dict[str, Path | None] | None:
        outcome = run(
            [self.binary, "plugin", "list", "--json"], cwd=Path.cwd(), timeout=timeout
        )
        if not outcome.ok:
            return None
        try:
            data = json.loads(outcome.full_output)
        except json.JSONDecodeError:
            return None
        if self.name == "codex":
            if not isinstance(data, dict):
                return None
            data = data.get("installed")
        if not isinstance(data, list):
            return None
        installed: dict[str, Path | None] = {}
        for row in data:
            if not isinstance(row, dict):
                return None
            if self.name == "claude":
                plugin_id, path = row.get("id"), row.get("installPath")
                if not isinstance(plugin_id, str) or not isinstance(path, str):
                    return None
                installed[plugin_id] = Path(path)
            else:
                plugin_id, marketplace, name, version = (
                    row.get(key)
                    for key in ("pluginId", "marketplaceName", "name", "version")
                )
                if (
                    not isinstance(plugin_id, str)
                    or not isinstance(marketplace, str)
                    or not isinstance(name, str)
                    or not isinstance(version, str)
                ):
                    return None
                home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
                installed[plugin_id] = (
                    home / "plugins/cache" / marketplace / name / version
                )
        return installed


CLIS: dict[Harness, HarnessCli] = {
    "claude": HarnessCli(
        "claude",
        "claude",
        "Track progress with TaskCreate, TaskUpdate, TaskList and TaskGet, alongside the workflow's progress ledger, so the work stays visible while the session runs.",
    ),
    "codex": HarnessCli(
        "codex",
        "codex",
        "Track progress with `update_plan`, alongside the workflow's progress ledger, so the work stays visible while the session runs.",
    ),
}
