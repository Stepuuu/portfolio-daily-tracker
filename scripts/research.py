#!/usr/bin/env python3
"""Small JSON CLI for an existing research service; Python standard library only.

Examples:
  python scripts/research.py templates
  python scripts/research.py datasets
  python scripts/research.py run --dataset ds_<id> --template momentum --wait

The service owns computation, model connections, persistence and cancellation.
This client does not run generated code, load model credentials or access accounts.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_URL = "http://127.0.0.1:8000"
MAX_RESPONSE_BYTES = 50 * 1024 * 1024
RUN_ID = re.compile(r"run_[a-f0-9]{32}")
DATASET_ID = re.compile(r"ds_[a-f0-9]{24}")


class CLIError(Exception):
    def __init__(self, code, message, *, exit_code=1, **details):
        super().__init__(message)
        self.code, self.message, self.exit_code, self.details = code, message, exit_code, details


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may quote an unknown argument, including a mistakenly pasted key.
        raise CLIError("invalid_arguments", "Invalid arguments; use --help for supported commands and options.", exit_code=2)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _decode(data):
    try:
        result = json.loads(data.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        json.dumps(result, allow_nan=False)
        return result
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise CLIError("invalid_response", "The research service returned invalid JSON.") from None


def _url(value):
    try:
        value.encode("ascii")
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or len(value) > 2000 or any(ord(c) <= 32 or ord(c) == 127 for c in value)
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise CLIError("invalid_url", "Backend URL must be HTTP(S), without credentials, query strings or fragments.", exit_code=2) from None
    root = value.rstrip("/")
    return root if parsed.path.rstrip("/").endswith("/api/lab") else root + "/api/lab"


class Client:
    def __init__(self, base_url=DEFAULT_URL, *, timeout=15, opener=None):
        self.base_url = _url(base_url)
        self.timeout = timeout
        # Avoid routing loopback traffic through shell proxy settings. Redirects
        # cannot silently forward a research goal to another service.
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None):
        encoded = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=encoded, method=method,
                                         headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise CLIError("response_too_large", "The research response exceeds the client size limit.")
                return _decode(content)
        except urllib.error.HTTPError as exc:
            messages = {401: "The backend requires authentication.", 403: "This research operation is unavailable in the current mode.",
                        404: "The requested research resource was not found.",
                        409: "The research state does not allow this operation yet.",
                        422: "The backend rejected the research request; check its dataset, template and parameters.",
                        429: "The backend is busy; retry after checking task status.",
                        503: "The research service is not ready."}
            message = messages.get(exc.code, "The research service returned an HTTP error.")
            # Do not echo error bodies: upstream validation responses may include
            # request values, private paths, or model provider credentials.
            exc.close()
            raise CLIError("http_error", message, status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise CLIError("connection_error", "Cannot reach the research service; verify its address and that it is running.") from None


def _identifier(value, pattern, name):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise CLIError("invalid_arguments", f"Invalid {name} identifier.", exit_code=2)
    return value


def _params(value):
    try:
        if len(value.encode("utf-8")) > 65536:
            raise ValueError()
        result = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict) or len(result) > 32:
            raise ValueError()
        json.dumps(result, allow_nan=False)
        return result
    except (ValueError, TypeError, RecursionError):
        raise CLIError("invalid_parameters", "--params must be a finite JSON object with at most 32 parameters.", exit_code=2) from None


def build_parser():
    parser = Parser(description="Control an existing research workbench through /api/lab. Results are JSON.")
    parser.add_argument("--url", "--backend-url", dest="url", default=None,
                        help="Backend origin or /api/lab URL (default: TRACKER_LAB_URL or loopback port 8000)")
    parser.add_argument("--request-timeout", type=float, default=15, help="Timeout for one HTTP request, in seconds (default: 15)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("templates", "List registered research methods"), ("datasets", "List existing research datasets"),
                            ("connections", "List existing model connection profiles")):
        commands.add_parser(name, help=help_text)
    run = commands.add_parser("run", help="Queue a manual or agent research run")
    run.add_argument("--dataset", required=True, help="Dataset ID from datasets")
    run.add_argument("--template", default="momentum", help="Registered template ID (default: momentum)")
    run.add_argument("--goal", default="研究股票特征是否具有样本外预测能力", help="Research question")
    run.add_argument("--agent", action="store_true", help="Let the service's research agent propose and evaluate experiments")
    run.add_argument("--connection", help="Existing model connection ID; required with --agent")
    run.add_argument("--params", default="{}", help='Template parameters as JSON, e.g. {"lookback":20,"horizon":5}')
    run.add_argument("--max-experiments", type=int, default=3, help="Agent experiment limit, 1–6 (default: 3)")
    run.add_argument("--timeout", type=int, default=600, help="Research budget and optional wait limit, 30–1800 seconds")
    run.add_argument("--wait", action="store_true", help="Poll until completion; a local timeout does not cancel the research")
    run.add_argument("--request-id", help="Stable idempotency key for safely repeating the same submission")
    status = commands.add_parser("status", help="Inspect one run, or list recent runs")
    status.add_argument("run_id", nargs="?", help="Run ID; omit to list recent runs")
    cancel = commands.add_parser("cancel", help="Cancel a queued or active research run")
    cancel.add_argument("run_id")
    export = commands.add_parser("export", help="Write a private research JSON export")
    export.add_argument("run_id")
    export.add_argument("--output", required=True, help="Destination file (new files only unless --force)")
    export.add_argument("--force", action="store_true", help="Replace an existing destination file atomically")
    return parser


def _write_export(output, result, *, force=False):
    destination = Path(output).expanduser()
    if not destination.name:
        raise CLIError("invalid_output", "Choose a destination file.", exit_code=2)
    data = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".research-export-", dir=destination.parent)
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)  # Atomic no-clobber publication.
        return len(data)
    except FileExistsError:
        raise CLIError("output_exists", "Destination already exists; choose another file or explicitly use --force.", exit_code=2) from None
    except OSError:
        raise CLIError("write_error", "Cannot write the export; verify the destination directory and permissions.") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def execute(args, client, *, clock=time.monotonic, sleep=time.sleep):
    if args.command in {"templates", "datasets", "connections"}:
        return client.request("GET", "/" + args.command)
    if args.command == "status":
        suffix = "/" + _identifier(args.run_id, RUN_ID, "run") if args.run_id else ""
        return client.request("GET", "/runs" + suffix)
    if args.command in {"cancel", "export"}:
        identifier = _identifier(args.run_id, RUN_ID, "run")
        if args.command == "cancel":
            return client.request("POST", f"/runs/{identifier}/cancel")
        result = client.request("GET", f"/runs/{identifier}/export")
        size = _write_export(args.output, result, force=args.force)
        return {"run_id": identifier, "written": True, "bytes": size}
    _identifier(args.dataset, DATASET_ID, "dataset")
    _identifier(args.template, re.compile(r"[a-z][a-z0-9_]{0,63}"), "template")
    if (args.agent and not args.connection) or (args.connection and not args.agent):
        raise CLIError("invalid_arguments", "Use --agent and --connection together.", exit_code=2)
    if args.connection:
        _identifier(args.connection, re.compile(r"[a-zA-Z0-9_-]{1,64}"), "connection")
    if not 4 <= len(args.goal) <= 2000 or not args.goal.strip() or "\x00" in args.goal:
        raise CLIError("invalid_arguments", "Research goal must contain 4–2000 characters.", exit_code=2)
    if not 30 <= args.timeout <= 1800 or not 1 <= args.max_experiments <= 6:
        raise CLIError("invalid_arguments", "Use a timeout of 30–1800 seconds and 1–6 experiments.", exit_code=2)
    request_id = args.request_id or uuid.uuid4().hex
    _identifier(request_id, re.compile(r"[A-Za-z0-9_.:-]{1,128}"), "request")
    payload = {"dataset_id": args.dataset, "template_id": args.template, "objective": args.goal,
               "mode": "agent" if args.agent else "manual", "params": _params(args.params),
               "max_experiments": args.max_experiments, "timeout_seconds": args.timeout,
               "client_request_id": request_id}
    if args.connection:
        payload["connection_id"] = args.connection
    try:
        run = client.request("POST", "/runs", payload)
    except CLIError as exc:
        exc.details["client_request_id"] = request_id
        raise
    if not isinstance(run, dict) or not RUN_ID.fullmatch(str(run.get("id", ""))):
        raise CLIError("invalid_response", "The service did not return a valid run identifier.", client_request_id=request_id)
    if not args.wait:
        return run
    identifier = run["id"]
    deadline = clock() + args.timeout
    while run.get("status") not in {"completed", "failed", "cancelled"}:
        remaining = deadline - clock()
        if remaining <= 0:
            raise CLIError("wait_timeout", "Stopped waiting; the research continues on the backend. Use status or cancel with the run ID.",
                           exit_code=3, run_id=identifier, status=run.get("status"))
        sleep(min(1.0, remaining))
        remaining = deadline - clock()
        if remaining <= 0:
            continue
        previous_timeout = getattr(client, "timeout", None)
        if previous_timeout is not None:
            client.timeout = min(previous_timeout, remaining)
        try:
            run = client.request("GET", f"/runs/{identifier}")
        except CLIError as exc:
            exc.details["run_id"] = identifier
            raise
        finally:
            if previous_timeout is not None:
                client.timeout = previous_timeout
        if not isinstance(run, dict) or run.get("id") != identifier:
            raise CLIError("invalid_response", "The service returned an inconsistent task status.", run_id=identifier)
    if run["status"] != "completed":
        raise CLIError("research_" + run["status"], "Research did not complete; inspect the run in the workbench.",
                       exit_code=4, run_id=identifier, status=run["status"])
    return run


def main(argv=None, *, client_factory=Client, clock=time.monotonic, sleep=time.sleep, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        args = build_parser().parse_args(argv)
        if not math.isfinite(args.request_timeout) or not 1 <= args.request_timeout <= 300:
            raise CLIError("invalid_arguments", "HTTP request timeout must be 1–300 seconds.", exit_code=2)
        backend = args.url if args.url is not None else os.environ.get("TRACKER_LAB_URL", DEFAULT_URL)
        client = client_factory(_url(backend), timeout=args.request_timeout)
        result = execute(args, client, clock=clock, sleep=sleep)
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, allow_nan=False), file=stdout)
        return 0
    except CLIError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": exc.message, **exc.details}},
                         ensure_ascii=False, allow_nan=False), file=stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "interrupted", "message": "Client interrupted; backend research is unchanged. Use status to find its run ID."}}), file=stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
