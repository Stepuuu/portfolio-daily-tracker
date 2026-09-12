"""Offline command contract tests. No real HTTP, models or market data."""
import importlib.util
import io
import json
from pathlib import Path
import stat
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = next(path for path in (ROOT / "scripts/research.py", ROOT / "scripts/research_lab.py") if path.exists())
SPEC = importlib.util.spec_from_file_location("research_cli", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
RUN = "run_" + "a" * 32
DATASET = "ds_" + "b" * 24


class FakeClient:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        self.base_url = None

    def factory(self, base_url, **kwargs):
        self.base_url = base_url
        return self

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        if self.responses:
            result = self.responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"id": RUN, "status": "running"}


def invoke(args, client=None, **kwargs):
    client = client or FakeClient()
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.main(args, client_factory=client.factory, stdout=stdout, stderr=stderr, **kwargs)
    return code, json.loads(stdout.getvalue() or stderr.getvalue()), client


@pytest.mark.parametrize("command", ["templates", "datasets", "connections"])
def test_list_commands_only_call_existing_api(command, monkeypatch):
    monkeypatch.delenv("TRACKER_LAB_URL", raising=False)
    code, output, client = invoke([command], FakeClient([[{"id": "example"}]]))
    assert code == 0 and output == {"ok": True, "data": [{"id": "example"}]}
    assert client.requests == [("GET", "/" + command, None)]
    assert client.base_url == "http://127.0.0.1:8000/api/lab"


def test_backend_env_and_explicit_override(monkeypatch):
    monkeypatch.setenv("TRACKER_LAB_URL", "http://localhost:8765")
    _, _, client = invoke(["templates"])
    assert client.base_url == "http://localhost:8765/api/lab"
    _, _, client = invoke(["--url", "http://127.0.0.1:9876/api/lab", "templates"])
    assert client.base_url == "http://127.0.0.1:9876/api/lab"


def test_run_payload_and_idempotency_key_are_forwarded():
    code, _, client = invoke(["run", "--dataset", DATASET, "--template", "momentum", "--request-id", "stable-key",
                             "--params", '{"lookback":30}', "--goal", "Study trend persistence"])
    assert code == 0
    method, path, payload = client.requests[0]
    assert (method, path) == ("POST", "/runs")
    assert payload["params"] == {"lookback": 30}
    assert payload["mode"] == "manual" and "connection_id" not in payload
    assert payload["client_request_id"] == "stable-key"


def test_agent_run_uses_existing_connection():
    code, _, client = invoke(["run", "--dataset", DATASET, "--agent", "--connection", "existing-model"])
    assert code == 0
    assert client.requests[0][2]["connection_id"] == "existing-model"
    assert client.requests[0][2]["mode"] == "agent"


@pytest.mark.parametrize("args", [
    ["run", "--dataset", DATASET, "--agent"],
    ["run", "--dataset", DATASET, "--connection", "unused"],
    ["run", "--dataset", "invalid"],
    ["run", "--dataset", DATASET, "--params", '{"x":NaN}'],
    ["run", "--dataset", DATASET, "--params", '{"x":1e999}'],
    ["run", "--dataset", DATASET, "--params", '[]'],
    ["run", "--dataset", DATASET, "--timeout", "0"],
    ["run", "--dataset", DATASET, "--max-experiments", "100"],
    ["status", "../../account-state"],
    ["--url", "http://user:private-token-marker@localhost", "templates"],
    ["--url", "http://localhost?secret=private-token-marker", "templates"],
    ["--request-timeout", "nan", "templates"],
    ["templates", "--unsupported-secret", "private-token-marker"],
])
def test_invalid_inputs_have_stable_errors_without_requests_or_secrets(args):
    code, output, client = invoke(args)
    assert code == 2 and output["ok"] is False
    assert client.requests == []
    assert "private-token-marker" not in json.dumps(output)


def test_wait_completion_polls_without_running_computation():
    client = FakeClient([{"id": RUN, "status": "queued"}, {"id": RUN, "status": "running"},
                         {"id": RUN, "status": "completed", "result": {"metrics": {}}}])
    code, output, client = invoke(["run", "--dataset", DATASET, "--wait"], client, sleep=lambda _: None)
    assert code == 0 and output["data"]["status"] == "completed"
    assert [method for method, _, _ in client.requests] == ["POST", "GET", "GET"]


def test_wait_timeout_leaves_job_running_and_reports_identifier():
    elapsed = [0.0]
    code, output, client = invoke(["run", "--dataset", DATASET, "--wait", "--timeout", "30"],
                                  clock=lambda: elapsed[0], sleep=lambda value: elapsed.__setitem__(0, elapsed[0] + value))
    assert code == 3 and output["error"]["run_id"] == RUN
    assert output["error"]["code"] == "wait_timeout"
    assert not any(path.endswith("/cancel") for _, path, _ in client.requests)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_failure_has_nonzero_exit_without_raw_error(status):
    code, output, _ = invoke(["run", "--dataset", DATASET, "--wait"],
                             FakeClient([{"id": RUN, "status": status, "error": "private-token-marker"}]))
    assert code == 4 and output["error"]["run_id"] == RUN
    assert "private-token-marker" not in json.dumps(output)


def test_cancel_and_status_call_matching_endpoints():
    _, _, client = invoke(["cancel", RUN])
    assert client.requests == [("POST", f"/runs/{RUN}/cancel", None)]
    _, _, client = invoke(["status", RUN])
    assert client.requests == [("GET", f"/runs/{RUN}", None)]
    _, _, client = invoke(["status"])
    assert client.requests == [("GET", "/runs", None)]


def test_export_creates_private_file_and_wont_overwrite(tmp_path):
    output_file = tmp_path / "research.json"
    expected = {"id": RUN, "status": "completed", "result": {"example": True}}
    code, output, _ = invoke(["export", RUN, "--output", str(output_file)], FakeClient([expected]))
    assert code == 0 and output["data"]["written"] is True
    assert json.loads(output_file.read_text()) == expected
    assert stat.S_IMODE(output_file.stat().st_mode) == 0o600
    code, output, _ = invoke(["export", RUN, "--output", str(output_file)], FakeClient([{"changed": True}]))
    assert code == 2 and output["error"]["code"] == "output_exists"
    assert json.loads(output_file.read_text()) == expected
    code, _, _ = invoke(["export", RUN, "--output", str(output_file), "--force"], FakeClient([{"changed": True}]))
    assert code == 0 and json.loads(output_file.read_text()) == {"changed": True}
    assert not list(tmp_path.glob(".research-export-*"))


class FakeOpener:
    def __init__(self, content=b"{}", error=None):
        self.content, self.error, self.requests = content, error, []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(self.content)


def test_real_http_client_encodes_json_and_uses_timeout():
    opener = FakeOpener(b'{"queued":true}')
    result = cli.Client(opener=opener, timeout=7).request("POST", "/runs", {"objective": "示例研究"})
    assert result == {"queued": True}
    request, timeout = opener.requests[0]
    assert request.full_url == "http://127.0.0.1:8000/api/lab/runs"
    assert request.method == "POST" and timeout == 7
    assert json.loads(request.data) == {"objective": "示例研究"}


def test_http_error_body_is_never_echoed():
    error = urllib.error.HTTPError("http://localhost", 422, "private-token-marker", {}, io.BytesIO(b"private-token-marker"))
    client = cli.Client(opener=FakeOpener(error=error))
    with pytest.raises(cli.CLIError) as raised:
        client.request("GET", "/templates")
    assert raised.value.details == {"status": 422}
    assert "private-token-marker" not in str(raised.value)


@pytest.mark.parametrize("content", [b"not json", b'{"x":NaN}', b'{"x":1e999}', b"\xff"])
def test_invalid_server_json_returns_stable_failure(content):
    with pytest.raises(cli.CLIError, match="invalid JSON"):
        cli.Client(opener=FakeOpener(content)).request("GET", "/datasets")


def test_failed_submission_retains_idempotency_key():
    client = FakeClient([cli.CLIError("connection_error", "Cannot connect")])
    code, output, _ = invoke(["run", "--dataset", DATASET, "--request-id", "repeat-safely"], client)
    assert code == 1 and output["error"]["client_request_id"] == "repeat-safely"


def test_redirect_handler_never_forwards_research_payload():
    assert cli.NoRedirect().redirect_request(None, None, 307, "redirect", {}, "https://example.invalid") is None
