"""Explicit model adapters for bounded research jobs.

CLI authentication stays with the installed, unmodified official application.
This module never opens credential files or starts a login flow.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
from typing import Callable
from urllib.parse import urlsplit

from core.llm.base import LLMConfig, LLMProvider, LLMResponse
from .claude import ClaudeProvider
from .openai import OpenAIProvider


class ResearchProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderAdapter:
    factory: Callable[[dict], LLMProvider]
    label: str
    transport: str
    tools: bool = True
    structured_output: bool = True
    streaming: bool = False
    cancellation: bool = True


_ADAPTERS: dict[str, ProviderAdapter] = {}


def register_research_provider(kind: str, adapter: ProviderAdapter) -> None:
    """Register a server-owned adapter; connection payloads cannot provide code."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", kind) or kind in _ADAPTERS:
        raise ValueError("Invalid or duplicate research provider kind")
    _ADAPTERS[kind] = adapter


def provider_capabilities() -> list[dict]:
    return [{"kind": kind, "label": item.label, "transport": item.transport,
             "supports": {"tools": item.tools, "structured_output": item.structured_output,
                          "streaming": item.streaming, "cancellation": item.cancellation}}
            for kind, item in _ADAPTERS.items()]


def _validate_connection(connection: dict) -> dict:
    if not isinstance(connection, dict):
        raise ValueError("Connection must be an object")
    connection = dict(connection)
    kind = connection.get("kind", connection.get("provider"))
    connection["kind"] = kind
    if kind not in _ADAPTERS:
        raise ValueError("Unknown research provider kind")
    model = connection.get("model")
    if not isinstance(model, str) or (not model.strip() and not kind.endswith("_cli")) or len(model) > 200 or any(ord(c) < 32 for c in model):
        raise ValueError("Model is required and must be a single line")
    timeout = connection.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 1800:
        raise ValueError("Provider timeout must be between 1 and 1800 seconds")
    if connection.get("api_key") or connection.get("token"):
        raise ValueError("Store an environment variable name, never a credential")
    connection["model"] = model.strip()
    connection["timeout"] = timeout
    return connection


def make_research_provider(connection: dict) -> LLMProvider:
    """Create without model calls. API keys are resolved only from server env."""
    connection = _validate_connection(connection)
    return _ADAPTERS[connection["kind"]].factory(connection)


class ResearchAPIProvider(LLMProvider):
    """Prevent upstream error bodies from becoming public job logs."""
    def __init__(self, delegate):
        super().__init__(delegate.config)
        self.delegate = delegate

    @property
    def name(self):
        return self.delegate.name

    async def chat(self, messages, **kwargs):
        try:
            return await self.delegate.chat(messages, **kwargs)
        except Exception as exc:
            raise ResearchProviderError(f"{self.name} request failed; check connection, model, and quota") from exc

    async def chat_stream(self, messages, **kwargs):
        try:
            async for item in self.delegate.chat_stream(messages, **kwargs):
                yield item
        except Exception as exc:
            raise ResearchProviderError(f"{self.name} request failed; check connection, model, and quota") from exc


def _api_provider(connection: dict, provider_class):
    base_url = connection.get("base_url") or None
    hostname = None
    if base_url:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API base URL must be HTTP(S), without credentials, query, or fragment")
        hostname = parsed.hostname.lower()
    key_env = connection.get("api_key_env") or ""
    if key_env:
        if not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key_env):
            raise ValueError("api_key_env must name a server environment variable")
        key = os.environ.get(key_env, "").strip()
        if not key:
            raise ResearchProviderError("API credential environment variable is not configured")
    elif hostname in {"localhost", "127.0.0.1", "::1"}:
        key = ""
    else:
        raise ValueError("An API credential variable is required for external services")
    delegate = provider_class(LLMConfig(api_key=key, model=connection["model"], base_url=base_url,
                                      temperature=0.2, timeout=connection["timeout"]))
    return ResearchAPIProvider(delegate)


def _cli_env() -> dict[str, str]:
    # Retain OS runtime, routing, and the official CLI's own config location.
    # API keys, injected bearer tokens, and unrelated application secrets are not inherited.
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
               "SYSTEMROOT", "WINDIR", "PATHEXT", "APPDATA", "LOCALAPPDATA", "USERPROFILE",
               "CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
               "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "LD_LIBRARY_PATH"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["NO_COLOR"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def _cli_binary(kind: str) -> str:
    name = "codex" if kind == "codex_cli" else "claude"
    binary = shutil.which(name)
    if not binary:
        raise ResearchProviderError(f"Install the official {name} CLI and sign in locally first")
    return binary


async def _stop_process(proc) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


async def _run_process(command: list[str], *, prompt: str = "", cwd: str | None = None,
                       timeout: float = 120, output_limit: int = 524288) -> tuple[int, str, str]:
    """Bounded pipe readers and process-group cancellation; never invokes a shell."""
    proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                               cwd=cwd, env=_cli_env(), start_new_session=os.name == "posix")

    async def read(stream):
        pieces, size = [], 0
        while True:
            chunk = await stream.read(16384)
            if not chunk:
                return b"".join(pieces).decode("utf-8", errors="replace")
            size += len(chunk)
            if size > output_limit:
                raise ResearchProviderError("CLI output exceeded the research limit")
            pieces.append(chunk)

    async def write():
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    tasks = [asyncio.create_task(read(proc.stdout)), asyncio.create_task(read(proc.stderr)),
             asyncio.create_task(write()), asyncio.create_task(proc.wait())]
    aggregate = asyncio.gather(*tasks)
    try:
        stdout, stderr, _, code = await asyncio.wait_for(aggregate, timeout=timeout)
        return code, stdout, stderr
    except BaseException:
        await _stop_process(proc)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(aggregate, return_exceptions=True)
        raise


async def probe_research_provider(connection: dict) -> dict:
    """Read official CLI status only; never print raw auth output or account IDs."""
    connection = _validate_connection(connection)
    kind = connection["kind"]
    if _ADAPTERS[kind].transport == "api":
        try:
            make_research_provider(connection)
            return {"available": True, "auth_mode": "api_key", "verified": False,
                    "reason": "Credential configured; no model request made"}
        except (ValueError, ResearchProviderError):
            return {"available": False, "auth_mode": "api_key", "verified": False,
                    "reason": "API connection is incomplete"}
    try:
        binary = _cli_binary(kind)
        code, out, _ = await _run_process([binary, "--version"], timeout=10, output_limit=8192)
        version = re.search(r"\d+\.\d+\.\d+", out)
        command = [binary, "login", "status"] if kind == "codex_cli" else [binary, "auth", "status", "--json"]
        auth_code, out, err = await _run_process(command, timeout=15, output_limit=32768)
        if kind == "codex_cli":
            authenticated = auth_code == 0 and "chatgpt" in (out + err).lower()
        else:
            status = json.loads(out)
            authenticated = auth_code == 0 and bool(status.get("loggedIn")) and str(status.get("authMethod", "")).lower() in {"oauth", "claude.ai"}
        return {"available": code == 0 and authenticated, "version": version.group(0) if version else None,
                "auth_mode": "subscription_cli", "verified": False,
                "reason": "Official CLI login available; no model request made" if authenticated else "Sign in using the official CLI"}
    except (OSError, ValueError, ResearchProviderError, asyncio.TimeoutError):
        return {"available": False, "auth_mode": "subscription_cli", "verified": False,
                "reason": "Official CLI is unavailable or not signed in"}


class OfficialCLIProvider(LLMProvider):
    """Model-only CLI call; research tools are executed by the application registry."""
    def __init__(self, connection: dict):
        super().__init__(LLMConfig(api_key="", model=connection["model"], timeout=connection["timeout"]))
        self.kind = connection["kind"]

    @property
    def name(self):
        return "Codex CLI" if self.kind == "codex_cli" else "Claude Code CLI"

    def _command(self, directory: Path, schema: dict | None):
        binary = _cli_binary(self.kind)
        if self.kind == "codex_cli":
            command = [binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                       "--sandbox", "read-only", "-C", str(directory), "--json",
                       "-c", 'approval_policy="never"', "-c", 'model_provider="openai"',
                       "-c", 'forced_login_method="chatgpt"', "-c", "features.shell_tool=false",
                       "-c", "features.unified_exec=false", "-c", "features.plugins=false",
                       "-c", "features.skill_search=false", "-c", "features.skip_host_skill_discovery=true",
                       "-c", 'web_search="disabled"', "-o", str(directory / "result.txt")]
            if self.config.model:
                command.extend(["-m", self.config.model])
            if schema:
                schema_path = directory / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                command.extend(["--output-schema", str(schema_path)])
            return [*command, "-"]
        command = [binary, "-p", "--safe-mode", "--tools", "", "--strict-mcp-config",
                   "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "dontAsk",
                   "--no-session-persistence", "--output-format", "json"]
        if self.config.model:
            command.extend(["--model", self.config.model])
        if schema:
            command.extend(["--json-schema", json.dumps(schema)])
        return command

    async def chat(self, messages, **kwargs):
        from .openai import _messages_for_openai
        body = json.dumps({"messages": _messages_for_openai(messages)}, ensure_ascii=False, allow_nan=False)
        schema = kwargs.get("output_schema")
        if kwargs.get("tools"):
            raise ResearchProviderError("Use a structured research plan with the application tool registry")
        if len(body.encode("utf-8")) > 262144:
            raise ResearchProviderError("Research context exceeds the model input limit")
        with tempfile.TemporaryDirectory(prefix="portfolio-research-") as temp_dir:
            directory = Path(temp_dir)
            try:
                code, stdout, stderr = await _run_process(self._command(directory, schema), prompt=body,
                                                         cwd=temp_dir, timeout=kwargs.get("timeout", self.config.timeout))
            except asyncio.TimeoutError as exc:
                raise ResearchProviderError("Official CLI timed out; the run was stopped") from exc
            if code:
                # Raw CLI output may contain local paths or account details.
                raise ResearchProviderError(f"{self.name} failed (exit {code}); check local CLI login and quota")
            usage, model = {}, self.config.model or "cli-default"
            if self.kind == "codex_cli":
                output_path = directory / "result.txt"
                if not output_path.exists() or output_path.stat().st_size > 262144:
                    raise ResearchProviderError("Codex CLI did not produce a bounded final result")
                content = output_path.read_text(encoding="utf-8")
                for line in stdout.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") in {"turn.failed", "error"}:
                        raise ResearchProviderError("Codex CLI reported a failed turn")
                    if event.get("type") == "turn.completed":
                        raw_usage = event.get("usage") or {}
                        usage = {"prompt_tokens": raw_usage.get("input_tokens", 0), "completion_tokens": raw_usage.get("output_tokens", 0)}
            else:
                try:
                    result = json.loads(stdout)
                except ValueError as exc:
                    raise ResearchProviderError("Claude Code CLI returned invalid JSON") from exc
                if not isinstance(result, dict) or result.get("is_error") or result.get("subtype", "success") != "success":
                    raise ResearchProviderError("Claude Code CLI did not complete successfully")
                structured = result.get("structured_output")
                content = json.dumps(structured, ensure_ascii=False) if structured is not None else result.get("result", "")
                raw_usage = result.get("usage") or {}
                usage = {"prompt_tokens": raw_usage.get("input_tokens", 0), "completion_tokens": raw_usage.get("output_tokens", 0)}
            if not isinstance(content, str) or not content.strip():
                raise ResearchProviderError("Official CLI returned an empty result")
            return LLMResponse(content=content.strip(), model=model, usage=usage, finish_reason="stop")

    async def chat_stream(self, messages, **kwargs):
        # An explicit non-streaming capability prevents pretending buffered output is live tokens.
        response = await self.chat(messages, **kwargs)
        yield response.content


register_research_provider("openai", ProviderAdapter(lambda c: _api_provider(c, OpenAIProvider), "OpenAI-compatible API", "api", streaming=True))
register_research_provider("anthropic", ProviderAdapter(lambda c: _api_provider(c, ClaudeProvider), "Anthropic-compatible API", "api", streaming=True))
register_research_provider("codex_cli", ProviderAdapter(OfficialCLIProvider, "Codex CLI", "cli", tools=False))
register_research_provider("claude_cli", ProviderAdapter(OfficialCLIProvider, "Claude Code CLI", "cli", tools=False))
