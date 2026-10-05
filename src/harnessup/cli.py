import argparse
import json
import os
import sys
from importlib.metadata import version

from harnessup.session import session_start


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnessup")
    parser.add_argument(
        "--version", action="version", version=f"harnessup {version('harnessup')}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    session = commands.add_parser("session-start")
    session.add_argument("--harness", choices=["claude", "codex"], required=True)
    args = parser.parse_args(argv)
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
