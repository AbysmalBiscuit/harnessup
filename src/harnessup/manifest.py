import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Harness = Literal["claude", "codex"]
HARNESSES: tuple[Harness, ...] = ("claude", "codex")
MANIFEST_DIR = Path(".agents/harnessup")
MANIFEST_FILE = MANIFEST_DIR / "manifest.toml"


class ManifestError(Exception):
    pass


@dataclass(frozen=True)
class Marketplace:
    name: str
    source: str
    harnesses: tuple[Harness, ...]


@dataclass(frozen=True)
class Plugin:
    id: str
    harnesses: tuple[Harness, ...]
    bootstrap: tuple[str, ...]
    check: str | None

    @property
    def name(self) -> str:
        return self.id.split("@")[0]

    @property
    def marketplace(self) -> str:
        return self.id.split("@")[1]


@dataclass(frozen=True)
class Tool:
    name: str
    install: str
    check: str


@dataclass(frozen=True)
class FileEntry:
    source: str
    target: str


@dataclass(frozen=True)
class Manifest:
    marketplaces: tuple[Marketplace, ...]
    plugins: tuple[Plugin, ...]
    tools: tuple[Tool, ...]
    files: tuple[FileEntry, ...]
    settings_local: dict[str, object] | None
    startup: str | None
    recovery: str | None
    task_tools: bool = True
    home_files: tuple[FileEntry, ...] = ()
    silence_stop_hook: bool = False


def source_kind(source: str) -> Literal["path", "git", "github"]:
    if source.startswith("./"):
        return "path"
    return "git" if "://" in source or source.startswith("git@") else "github"


def _table(
    value: object, path: str, allowed: set[str] | None = None
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ManifestError(f"{path}: expected a table")
    if allowed is not None:
        for key in value:
            if key not in allowed:
                raise ManifestError(f"{path + '.' if path else ''}{key}: unknown key")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{path}: expected a string")
    return value


def _path(value: object, path: str) -> str:
    text = _string(value, path)
    if not text or Path(text).is_absolute() or ".." in Path(text).parts:
        raise ManifestError(f"{path}: expected a relative path without ..")
    return text


def _strings(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{path}: expected a list of strings")
    return tuple(_string(item, path) for item in value)


def _harnesses(value: object, path: str) -> tuple[Harness, ...]:
    names = _strings(value, path)
    result: list[Harness] = []
    for name in names:
        if name == "claude" or name == "codex":
            result.append(name)
        else:
            raise ManifestError(f"{path}: unsupported harness {name}")
    return tuple(result)


def _rows(
    data: dict[str, object], key: str, allowed: set[str]
) -> list[tuple[str, dict[str, object]]]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ManifestError(f"{key}: expected an array of tables")
    return [
        (f"{key}[{index}]", _table(row, f"{key}[{index}]", allowed))
        for index, row in enumerate(value)
    ]


def _files(data: dict[str, object], key: str) -> tuple[FileEntry, ...]:
    return tuple(
        FileEntry(
            _path(row.get("source"), f"{path}.source"),
            _path(row.get("target"), f"{path}.target"),
        )
        for path, row in _rows(data, key, {"source", "target"})
    )


def parse(data: dict[str, object]) -> Manifest:
    _table(
        data,
        "",
        {
            "schema",
            "marketplace",
            "plugin",
            "tool",
            "file",
            "home_file",
            "claude",
            "context",
        },
    )
    if type(data.get("schema")) is not int or data["schema"] != 1:
        raise ManifestError("schema: expected schema version 1")
    marketplaces = []
    for path, row in _rows(data, "marketplace", {"name", "source", "harnesses"}):
        name = _string(row.get("name"), f"{path}.name")
        source = _string(row.get("source"), f"{path}.source")
        if source_kind(source) == "path":
            _path(source[2:], f"{path}.source")
        elif Path(source).is_absolute() or ".." in Path(source).parts:
            raise ManifestError(f"{path}.source: expected a marketplace source")
        marketplaces.append(
            Marketplace(
                name,
                source,
                _harnesses(row.get("harnesses", list(HARNESSES)), f"{path}.harnesses"),
            )
        )
    by_name = {marketplace.name: marketplace for marketplace in marketplaces}
    plugins = []
    for path, row in _rows(data, "plugin", {"id", "harnesses", "bootstrap", "check"}):
        plugin_id = _string(row.get("id"), f"{path}.id")
        parts = plugin_id.split("@")
        if len(parts) != 2 or not all(parts) or parts[1] not in by_name:
            raise ManifestError(f"{path}.id: expected name@declared-marketplace")
        marketplace = by_name[parts[1]]
        harnesses = _harnesses(
            row.get("harnesses", list(marketplace.harnesses)), f"{path}.harnesses"
        )
        if not set(harnesses).issubset(marketplace.harnesses):
            raise ManifestError(
                f"{path}.harnesses: must be supported by the marketplace"
            )
        bootstrap = _strings(row.get("bootstrap", []), f"{path}.bootstrap")
        if bootstrap:
            _path(bootstrap[0], f"{path}.bootstrap")
        check = _string(row["check"], f"{path}.check") if "check" in row else None
        plugins.append(Plugin(plugin_id, harnesses, bootstrap, check))
    tools = tuple(
        Tool(
            *(
                _string(row.get(key), f"{path}.{key}")
                for key in ("name", "install", "check")
            )
        )
        for path, row in _rows(data, "tool", {"name", "install", "check"})
    )
    files = _files(data, "file")
    home_files = _files(data, "home_file")
    claude = _table(
        data.get("claude", {}), "claude", {"settings_local", "silence_stop_hook"}
    )
    settings = (
        _table(claude["settings_local"], "claude.settings_local")
        if "settings_local" in claude
        else None
    )
    silence_stop_hook = claude.get("silence_stop_hook", False)
    if not isinstance(silence_stop_hook, bool):
        raise ManifestError("claude.silence_stop_hook: expected a boolean")
    context = _table(
        data.get("context", {}), "context", {"startup", "recovery", "task_tools"}
    )
    task_tools = context.get("task_tools", True)
    if not isinstance(task_tools, bool):
        raise ManifestError("context.task_tools: expected a boolean")
    startup = (
        _path(context["startup"], "context.startup") if "startup" in context else None
    )
    recovery = (
        _path(context["recovery"], "context.recovery")
        if "recovery" in context
        else None
    )
    return Manifest(
        tuple(marketplaces),
        tuple(plugins),
        tools,
        files,
        settings,
        startup,
        recovery,
        task_tools,
        home_files,
        silence_stop_hook,
    )


def load(root: Path) -> Manifest | None:
    path = root / MANIFEST_FILE
    if not path.exists():
        return None
    try:
        return parse(tomllib.loads(path.read_text()))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ManifestError(f"manifest: {error}") from error
