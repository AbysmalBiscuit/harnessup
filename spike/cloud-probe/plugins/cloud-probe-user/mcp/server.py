"""Throwaway cloud probe: a stdio MCP server with one tool, to show plugin MCP servers start."""

import json
import sys
import time
from pathlib import Path


def log(line):
    for directory in (Path("/var/tmp/cloud-probe"), Path("/tmp/cloud-probe")):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "mcp.log").open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")
            return
        except OSError:
            continue


def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()


TOOL = {
    "name": "probe_ping",
    "description": "Cloud probe: returns pong to show this plugin's MCP server is connected.",
    "inputSchema": {"type": "object", "properties": {}},
}

log("started")
for line in sys.stdin:
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    log(f"received {method}")
    if method == "initialize":
        version = message.get("params", {}).get("protocolVersion", "2025-06-18")
        send({"id": request_id, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "cloud-probe", "version": "0.0.0"},
        }})
    elif method == "tools/list":
        send({"id": request_id, "result": {"tools": [TOOL]}})
    elif method == "tools/call":
        send({"id": request_id, "result": {"content": [{"type": "text", "text": "pong from cloud-probe-user"}]}})
    elif request_id is not None:
        send({"id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}})
