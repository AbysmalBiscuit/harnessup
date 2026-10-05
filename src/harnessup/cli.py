import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harnessup")
    parser.add_argument(
        "--version", action="version", version=f"harnessup {version('harnessup')}"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    commands.add_parser("session-start")
    parser.parse_args(argv)
    return 0
