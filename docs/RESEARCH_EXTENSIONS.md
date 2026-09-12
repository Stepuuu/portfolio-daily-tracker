# Research extension interfaces

The workbench separates immutable data, deterministic calculation, model
adapters and durable execution. A framework can consume HTTP/MCP tools without
replacing the application's accounting or job store.

The design takes cues from [Qlib workflows](https://qlib.readthedocs.io/en/latest/component/workflow.html),
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[AutoGen termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html)
and [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
These are architectural references; the project does not require those frameworks
or claim that their runtimes are embedded.

## Add a method

A trusted installed Python module exports `register()`. Select modules using
`TRACKER_LAB_PLUGINS=your_package.extension`. Both the Web worker and disposable
calculation process load the same module. The browser cannot install plugins or
provide source code. Plugins execute trusted administrator code, not sandboxed
untrusted code.

```python
from backtesting.research import register_template

def register():
    register_template("custom_risk", {
        "name": "Custom risk study",
        "description": "Evaluate a separately defined risk hypothesis.",
        "target": "custom_risk_target",
        "defaults": {"bins": 4},
        "parameters": {
            "bins": {"type": "integer", "minimum": 2, "maximum": 12,
                     "default": 4, "description": "Number of bins"}
        },
    }, runner=run_custom_risk)

def run_custom_risk(frames, spec):
    # frames: copied dict[symbol, daily OHLCV DataFrame]
    # Implement the study; use training/validation only for selection.
    # Return actual computed, finite JSON, including validation MAE.
    raise NotImplementedError("Implement and validate your method before enabling it")
```

This is an interface sketch, not a ready-made research method. A runner returns:

```text
metrics.validation.mae           finite, nonnegative selection error
metrics.test                    final holdout measurements
metrics.baseline                baseline information
equity[]                        date, strategy, benchmark
manifest                        input hash, method version, actual configuration
warnings[] / lessons[]          assumptions and explanations
results                         per-symbol evidence and predictions (required; may be empty)
```

Method-defined parameters are validated against registry metadata. The UI renders
scalar/enum controls and a JSON editor for more complex values. Templates with a
different `target` cannot compete in the same autonomous run. Missing or invalid
validation evidence cannot produce a selected successful result. Method authors
remain responsible for the statistical meaning of their metric and split.

`run_experiment(frames, spec)` is the common pure calculation entry point.
`ResearchConfig` and `run_research(frame, config)` also expose the built-in
single-symbol implementation to Python. The engine does not download data or
access a portfolio as part of this interface.

## Add a model adapter

```python
from providers.llm.research import ProviderAdapter, register_research_provider

def register():
    register_research_provider("custom_model", ProviderAdapter(
        factory=make_provider,
        label="Custom model service",
        transport="api",
        structured_output=True,
        tools=False,
    ))
```

`make_provider(connection)` returns `core.llm.base.LLMProvider`. Implement `chat`
and `chat_stream`, returning `LLMResponse`. A structured research call makes one
provider call; do not add invisible retries that evade the worker's call budget.
Support cancellation, bound responses/timeouts and return usage when available.
Resolve secret values only on the server and never include them in exceptions.

Adapter metadata is available through capabilities. Model IDs and endpoints are
configuration rather than branches inside the scheduler. CLI adapters advertise
native tools as disabled: the application still executes validated research
plans through its registry.

## HTTP integration

```python
import requests

base = "http://127.0.0.1:8000/api/lab"
datasets = requests.get(f"{base}/datasets", timeout=10).json()
response = requests.post(f"{base}/runs", timeout=10, json={
    "mode": "manual",
    "objective": "Compare volatility prediction against persistence",
    "dataset_id": datasets[0]["id"],
    "template_id": "volatility",
    "params": {"horizon": 5, "lookback": 20},
    "client_request_id": "my-study-001",
})
response.raise_for_status()
run = response.json()
```

Reuse a `client_request_id` only for the same request; a different request with
the same key returns 409. Poll `GET /runs/{id}` or use the CLI wait mode. Terminal
states are `completed`, `failed` and `cancelled`. A successful HTTP submission
means queued, not that a research conclusion has been established.

`POST /runs/{id}/retry` creates a new linked task; `POST /runs/{id}/cancel` prevents
future stage commits. Exports are `GET /runs/{id}/export`. See the running
application's `/docs` for import, model connection and schedule schemas.

## Validation requirements for contributions

Use synthetic fixtures. Verify causal features, label maturity, fixed split
boundaries, independent holdout, a meaningful baseline, costs and reproducible
outputs. Test negative/inconclusive results, malformed model output, cancellation
and restart. Include no real credentials, personal machine paths, holdings,
downloaded market datasets, work logs or private research exports in commits.
