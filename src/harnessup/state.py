import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Status = Literal["ok", "skipped", "failed"]


@dataclass
class Item:
    kind: str
    name: str
    harness: str | None
    status: Status
    detail: str = ""


@dataclass
class RepoReport:
    root: str
    manifest_error: str | None = None
    items: list[Item] = field(default_factory=list)


def state_dir() -> Path:
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "harnessup"
    )


def write_report(own: list[Item], repos: list[RepoReport], version: str) -> Path:
    path = state_dir() / "setup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "harnessup_version": version,
        "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": [asdict(item) for item in own],
        "repos": [asdict(report) for report in repos],
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def read_report_for(root: Path) -> RepoReport | None:
    try:
        data = json.loads((state_dir() / "setup.json").read_text())
        if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
            return None
        for row in data["repos"]:
            if not isinstance(row, dict) or not isinstance(row.get("root"), str):
                return None
            if Path(row["root"]).resolve() != root.resolve():
                continue
            error = row.get("manifest_error")
            if error is not None and not isinstance(error, str):
                return None
            if not isinstance(row.get("items"), list):
                return None
            items = []
            for item in row["items"]:
                if not isinstance(item, dict):
                    return None
                kind, name, harness, status, detail = (
                    item.get(key)
                    for key in ("kind", "name", "harness", "status", "detail")
                )
                if (
                    not isinstance(kind, str)
                    or not isinstance(name, str)
                    or not isinstance(detail, str)
                ):
                    return None
                if harness is not None and not isinstance(harness, str):
                    return None
                if status != "ok" and status != "failed" and status != "skipped":
                    return None
                items.append(Item(kind, name, harness, status, detail))
            return RepoReport(row["root"], error, items)
    except (OSError, ValueError, TypeError):
        return None
    return None
