import argparse
import json
import os
import sys
import time
from importlib.metadata import version
from pathlib import Path

from harnessup import commit_patch
from harnessup.proc import Deadline
from harnessup.session import session_start, silence_stop_hook
from harnessup.setup import DEADLINE_S, setup
from harnessup.state import Item, write_report


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(prog="harnessup")
    parser.add_argument(
        "--version", action="version", version=f"harnessup {version('harnessup')}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("--repo", type=Path, action="append", default=[])
    setup_parser.add_argument("--deadline", type=float, default=DEADLINE_S)
    session = commands.add_parser("session-start")
    session.add_argument("--harness", choices=["claude", "codex"], required=True)
    commit_patch.add_arguments(commands.add_parser("commit-patch"))
    commands.add_parser("silence-stop-hook")
    args = parser.parse_args(argv)
    if args.command == "commit-patch":
        return commit_patch.run(args.patch, args.message)
    if args.command == "silence-stop-hook":
        problems = silence_stop_hook()
        for problem in problems:
            print(f"harnessup: {problem}")
        return 1 if problems else 0
    if args.command == "setup":
        try:
            setup(
                args.repo,
                Path.cwd(),
                Deadline(args.deadline - (time.monotonic() - started)),
            )
        except Exception as error:  # noqa: BLE001
            try:
                write_report(
                    [Item("self", "setup", None, "failed", str(error))],
                    [],
                    version("harnessup"),
                )
            except OSError as state_error:
                print(
                    f"harnessup: setup failed: {error}; could not write setup.json: {state_error}"
                )
    if args.command == "session-start":
        try:
            try:
                payload = json.loads(sys.stdin.read())
            except json.JSONDecodeError:
                payload = {}
            output = session_start(
                args.harness, payload if isinstance(payload, dict) else {}, os.environ
            )
            if output:
                print(output)
        except Exception as error:  # noqa: BLE001
            print(f"harnessup: session-start failed: {error}")
    return 0
