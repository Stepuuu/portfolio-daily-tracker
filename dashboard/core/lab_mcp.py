"""MCP stdio bridge: python -m core.lab_mcp --url http://127.0.0.1:8000

Connects to an already running workbench. It neither opens local account files
nor exposes arbitrary HTTP routes, shell commands, model keys or file paths.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from core.lab_store import encode
from core.lab_tools import tool_definitions, tool_request


def handle(message, invoke):
    identifier = message.get("id")
    method = message.get("method")
    if identifier is None:
        return None
    response = {"jsonrpc": "2.0", "id": identifier}
    try:
        if method == "initialize":
            version = message.get("params", {}).get("protocolVersion", "2025-11-25")
            if version not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
                version = "2025-11-25"
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "portfolio-research", "version": "3.2.0"},
                      "instructions": "Research only. Import datasets in the workbench first. Preserve failed experiments and use validation before final holdout."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            params = message.get("params", {})
            http_method, path, body = tool_request(params.get("name"), params.get("arguments", {}))
            try:
                content = invoke(http_method, path, body)
                result = {"content": [{"type": "text", "text": encode(content)}], "isError": False}
            except Exception:
                result = {"content": [{"type": "text", "text": "Research service request failed; check the workbench connection and task status."}], "isError": True}
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
            return response
        response["result"] = result
    except (ValueError, TypeError, AttributeError):
        response["error"] = {"code": -32602, "message": "Invalid research tool parameters"}
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("TRACKER_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error("Use an HTTP(S) origin without credentials or query parameters")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def invoke(method, path, body):
        headers = {"Content-Type": "application/json"}
        if os.environ.get("TRACKER_ACCESS_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["TRACKER_ACCESS_TOKEN"]
        request = urllib.request.Request(args.url.rstrip("/") + "/api/lab" + path,
                                         data=encode(body).encode() if body else None, method=method, headers=headers)
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read(50 * 1024 * 1024))

    while True:
        line = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not line:
            break
        try:
            if len(line) > 1024 * 1024:
                raise ValueError()
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError()
            response = handle(message, invoke)
        except (ValueError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC message"}}
        if response:
            print(encode(response), flush=True)


if __name__ == "__main__":
    main()
