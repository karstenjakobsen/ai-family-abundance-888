"""Local stdio MCP only: one JSON-RPC message per line, no network listener."""
import json
import sys
import sqlite3
import subprocess

from . import toolbox
from .guardian import Guardian

SUPPORTED = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}


def serve(config, brain, source=sys.stdin, destination=sys.stdout):
    initialized = False
    ready = False
    guardian = Guardian(config, brain)
    for line in source:
        request = None
        try:
            if len(line) > 65536:
                raise ValueError("Message too large")
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError("Invalid JSON-RPC request")
            ident, method = request.get("id"), request.get("method")
            if method == "notifications/initialized":
                ready = initialized
                continue
            if method and method.startswith("notifications/"):
                continue
            params = request.get("params", {})
            if method == "initialize":
                requested = params.get("protocolVersion")
                result = {"protocolVersion": requested if requested in SUPPORTED else "2025-11-25",
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "family-toolbox", "version": "0.1.0"}}
                initialized = True
            elif method == "ping":
                result = {}
            elif not ready:
                raise ValueError("Complete MCP initialization first")
            elif method == "tools/list":
                result = {"tools": toolbox.TOOLS}
            elif method == "tools/call":
                try:
                    value = toolbox.call(params["name"], params.get("arguments", {}), config, brain, guardian)
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}
                except (ValueError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
                    result = {"content": [{"type": "text", "text": str(error)[:500]}], "isError": True}
            else:
                reply = {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}}
                destination.write(json.dumps(reply) + "\n")
                destination.flush()
                continue
            reply = {"jsonrpc": "2.0", "id": ident, "result": result}
        except (ValueError, KeyError, TypeError) as error:
            reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                     "error": {"code": -32600, "message": str(error)[:200]}}
        destination.write(json.dumps(reply, ensure_ascii=False) + "\n")
        destination.flush()

