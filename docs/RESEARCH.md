# Stock research workbench

Explore a question about stocks, run a reproducible experiment, and compare it with
a simple baseline. Manual and autonomous research use the same data, calculation,
job history, cancellation and export interfaces.

## Start without credentials or Docker

```bash
make setup
make lab
```

Open [the research workbench](http://localhost:3000/lab). Select the synthetic
dataset and a learning template, then run a manual experiment. Synthetic results
teach the workflow; they are not market evidence. Existing account records are
not required. `make demo` also allows synthetic manual experiments while keeping
portfolio changes disabled. Demo research uses a separate database.

The regular `make start` includes the workbench alongside the portfolio features.
Python 3.10+, Node.js 22.12+ and Bash 4.3+ are required. No additional database
server, task broker, Docker daemon or machine learning service is required.

## Choose your research data

- **CSV:** UTF-8 with exactly `date,symbol,open,high,low,close,volume`. Dates use
  `YYYY-MM-DD`, increasing for each symbol. Up to 10 symbols, 25,000 rows and 8 MB.
- **Local cache:** copy the selected symbols, adjustment and interval from the
  existing daily-bar cache, opening it read-only.
- **Market download:** explicitly request selected daily bars through the existing
  data providers. Availability depends on the upstream provider. No account data
  is needed for downloads.

Inputs must have finite positive OHLC prices, nonnegative volume, valid OHLC
ranges and unique increasing dates. The workbench rejects invalid raw data
instead of silently filling missing volume or rewriting price ranges.

CSV `volume` is a share count. Convert A-share lots to shares (×100) before
importing. The AKShare A-share daily download adapter converts its
[documented lot units](https://akshare.akfamily.xyz/data/stock/stock.html) at the
provider boundary. Existing caches without reliable unit metadata are not rewritten;
verify their source units or download a new dataset version. The classic backtest
broker uses share volume for participation limits; the research workbench's
fractional-allocation simulation does not use absolute volume as a capacity limit.

Each dataset stores a content checksum and a version identity covering provenance
and adjustment. Same prices with a different source are distinct versions. Runs
verify the checksum before calculation. Known adjustment metadata must match the
experiment; CSV adjustment is a user declaration. These checks cannot recover
historical revisions or point-in-time information absent from the source.

## Learning methods

| Template | Question | Target |
|---|---|---|
| Momentum | Do recent price features predict subsequent returns? | Forward close-to-close return |
| Mean reversion | Do recent deviations help predict a reversal? | Forward close-to-close return |
| Volatility | Can historical risk features improve on volatility persistence? | Forward annualized realized volatility |

Features use information available through the observation date. A NumPy Ridge
model is fitted on the training segment; feature scaling also uses training data
only. Validation MAE chooses the regularization parameter. Labels crossing a
training/validation or validation/test boundary are removed according to their
maturity dates. The final model is not refitted using the test segment.

Predictions and strategy simulation are separate outputs. The simulation changes
fractional long-only allocations at the following open and includes user-selected
turnover costs. It values final positions at the last close. It does not reproduce
whole-share/board-lot execution, price-limit queues, financing or broker margin.
The multi-symbol curve averages returns on common test dates; it is a research
comparison and excludes cross-stock rebalancing costs.

Look at baseline error, validation and test error, costs, the capital curve and
the number of observations together. Overlapping labels are dependent samples;
reported error ratios are descriptive, not significance tests. Present-day stock
selection and adjusted historical prices can introduce selection/revision bias.

## Autonomous research

Select an agent connection, enter a question and choose a dataset. The agent can:

1. Propose hypotheses and registered method configurations.
2. Run experiments using the deterministic calculation engine.
3. Review validation evidence and propose a bounded revision.
4. Freeze the selected configuration using valid validation metrics.
5. Interpret the selected final test result with experiment references.

The dataset, target, horizon, time split, market and cost assumptions stay fixed
within a run. Only validation evidence is sent during refinement. Other
candidates' test results are not included in the candidate summary. Reusing a
historical test period in later runs does not make it a new independent holdout.

Calls are counted before invoking a model, including failed calls. Each run has
an experiment limit and an elapsed-time deadline. Failed experiments remain in
the record. Invalid plans, missing numerical evidence, provider failures and
quota exhaustion produce a failed run rather than a fabricated success.

Jobs persist in SQLite with atomic claims, expiring ownership leases, stage
checkpoints and append-only events. On restart a worker continues saved stages;
an in-flight remote model request may have consumed quota even if no response was
saved. Such retries still consume the saved call budget. Elapsed time includes
process downtime. User-triggered retry creates a new linked run with a fresh
budget. Cancellation stops the compute/model process and prevents later results
from being committed. Cancelling cannot undo tokens already spent remotely.

## Connect a model

Connections live in the workbench's **Connections and schedules** tab and in
Settings. Model IDs are configurable; no model catalog is treated as permanent.

| Connection | Authentication | What runs |
|---|---|---|
| OpenAI-compatible API | Server environment variable containing your API key | Chat completions and normalized tool calls |
| Anthropic API | Server environment variable containing your API key | Anthropic messages |
| Codex CLI | Existing official ChatGPT/Codex login | `codex exec` with restricted local capabilities |
| Claude Code CLI | Existing official subscription login | `claude -p` with native tools disabled |

For an API connection, set a secret through your own deployment environment, then
enter its **environment variable name**, endpoint and model in the UI. Secret
values are not accepted in connection profiles or returned to the browser.
An explicitly selected loopback API can run without a key for local model servers.

For subscription connections, install and sign in to the official client using
its normal flow. Research adapters do not copy tokens, open credential files,
reimplement OAuth or treat subscription credentials as API keys. Leave the model
blank to use the CLI's default. Detected clients get initial connection profiles;
the connection check separately reports login readiness without a model call.

CLI flags were exercised with Codex 0.153.4 and Claude Code 2.1.251. Use current
official clients if a flag is unsupported. Login checks do not prove that a
particular model or quota is available. Official subscription eligibility and
usage limits still apply. The Claude subscription route deliberately does not
use `--bare`, which skips subscription authentication.

Official references: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive/),
[Codex authentication](https://developers.openai.com/codex/auth/),
[Claude Code programmatic use](https://code.claude.com/docs/en/headless),
[Claude Code authentication](https://code.claude.com/docs/en/authentication).

## Schedules

Save a research configuration and choose an interval, maximum number of triggers
and whether the schedule is enabled. Schedules run while the backend is running,
survive restarts and atomically enqueue each due occurrence once. After downtime,
one due occurrence is enqueued; missed intervals are not replayed in a burst.
The displayed count is the number of triggers, not successful research results.

When creating a schedule in the workbench, select **Refresh data before each run**
(“每次运行前更新数据”; `refresh_data` in the API) to update the original symbols, start
date and adjustment through the current date into a new immutable version. The
checkbox is off by default and is available only for cache/download datasets that
retain their source selection. A cache may still be stale. For CSV/synthetic data
the checkbox is disabled: import a new version and create a new schedule instead.
Existing schedules show **Fixed version** or **Refresh each run**. Schedules do not
place trades, confirm ledger events or send messages.

## External agents and extension interfaces

See [extension interfaces](RESEARCH_EXTENSIONS.md) for Python template/provider
registration, HTTP and MCP. `GET /api/lab/tools` describes the research operations.
Every client uses the same validation, budgets and job state.

MCP configuration example (run from the repository's `dashboard` directory):

```json
{
  "mcpServers": {
    "portfolio-research": {
      "command": "python",
      "args": ["-m", "core.lab_mcp", "--url", "http://127.0.0.1:8000"]
    }
  }
}
```

Configure your client's working directory or installed Python module path for
your deployment. The stdio bridge needs the backend running. It exposes research
capabilities, templates, datasets, submit/list/status/cancel; it does not expose
arbitrary HTTP routes, shell execution, credentials or ledger confirmation.

## Command line

```bash
python scripts/research.py datasets
python scripts/research.py templates
python scripts/research.py run --dataset DATASET_ID --template volatility --wait
python scripts/research.py run --dataset DATASET_ID --template volatility --agent --connection codex_cli --wait
python scripts/research.py status RUN_ID
python scripts/research.py export RUN_ID --output research-result.json
```

Use `--help` for parameter JSON, idempotency keys, cancellation and time limits.
The CLI connects to the running backend, returns JSON, and uses nonzero exit codes
for failed or cancelled jobs. Exports use owner-only permissions and do not replace
an existing file unless explicitly requested.

## Operations and privacy

- `TRACKER_LAB_DIR` selects private research storage; default is `dashboard/data/lab`.
- `TRACKER_LAB_ONLY=1` starts research without initializing the portfolio/chat services.
- `TRACKER_LAB_PLUGINS` loads explicitly installed, trusted Python extension modules.
- `/health` includes actual research worker status. Stop jobs before maintenance
  if you do not want interrupted remote calls to be retried within their budget.
- Agent mode sends the selected research question, dataset metadata and computed evidence to the chosen model provider. Manual experiments make no model calls.
- Keep SQLite, downloaded data, exports, connection configuration and logs out of
  Git. Exports contain the chosen research question, symbols and dataset: inspect
  them before sharing. The application never labels a user export automatically
  safe for publication.

The built-in job store is designed for a self-hosted single-user deployment.
SQLite supports multiple local workers using leases; distributed/multi-tenant
authentication, remote worker coordination and arbitrary generated-code sandboxing
are outside this release. Use authenticated infrastructure when exposing the
service beyond loopback. See [security scope](../SECURITY.md).
