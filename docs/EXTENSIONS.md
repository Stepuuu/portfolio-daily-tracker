# Extensions

An extension is a separately installed Python module that adds to the running
backend without a change to any file of the application. A private
installation is then the released application plus its own package, and it
follows each release by upgrading the application alone.

**An extension runs with all of the backend's privileges.** It can read and
change the ledger, the desk store, the settings file with its API keys and
anything else the process can reach. It is not a sandbox: install only code
you wrote or trust as much as the application itself.

## Enabling

```bash
pip install ./my_private_package            # or put its directory on PYTHONPATH
TRACKER_EXTENSIONS=my_private.publisher,my_private.radar make start
```

`TRACKER_EXTENSIONS` is a comma-separated list of importable module names,
read from the environment or the repository's `.env` when the backend starts.
The modules are imported once, in that order, while `backend/main.py` is
imported, after the built-in routes are mounted. When the variable is unset or
empty nothing is imported and the backend behaves exactly as without this
feature. Under Docker, install the package into a backend image of your own
(the shipped one contains only `dashboard/`) and add the variable to the
backend's `environment` list.

## What a module may define

Each is optional.

| Name | Shape | Used |
|---|---|---|
| `routers()` | `[(APIRouter, prefix, tags), ...]`, as in `backend/features.py`; no router with its own `lifespan`, `on_startup` or `on_shutdown` | mounted at import, after the built-in routes |
| `background_tasks()` | `[f(app), ...]`, each returning an async context manager, as in `backend/features.py` | entered at start-up after the built-in tasks, left at shutdown before them |
| `on_close_report(report)` | a function or an `async def` | once per finished close report content, see below |
| module body | `core.agent_tools.register(factory)` (chat tools, see `core/agent_tools/__init__.py`), `core.notify.register_deliverer(function)` (delivery channels, see `core/notify.py`) | at import: before the chat agent builds its tools at start-up, and before anything is published |

```python
# my_private/publisher.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter
from core import agent_tools, notify
from core.tools import Tool

router = APIRouter()


@router.get("/ping")
def ping():
    return {"ok": True}


def routers():
    return [(router, "/api/ext/publisher", ["飞书发布"])]


@asynccontextmanager
async def refresher(app):
    task = asyncio.create_task(refresh_forever())   # your own loop; it must never block the event loop
    yield
    task.cancel()


def background_tasks():
    return [refresher]


@agent_tools.register
def radar_tools(runtime):
    async def radar_watchlist() -> dict:            # read-only, and async like every tool function
        return {"items": []}
    return Tool(name="radar_watchlist", description="读取雷达关注列表", parameters=[], function=radar_watchlist)


def push(notification, timeout):                    # the deliverer contract of core/notify.py
    ...


notify.register_deliverer(push, timeout=10)


def on_close_report(report):
    send_card(report["trading_date"], report["report"], idempotency_key=report["report_sha"])
```

## The close report hook

`on_close_report(report)` is called when a trading day's close report is
finished, and again each time a report with other content is finished for the
same day: a late entry followed by `--action pipeline`, the Feishu workbench's
report or the assistant's `run_portfolio_pipeline`, whichever process made it.
The report file is in `engine/reports`, or in `TRACKER_REPORTS_DIR` when it is set.
It is read from the pipeline's checkpoint `PORTFOLIO_DIR/pipeline-state/<day>.json`
(which records the hash of the snapshot and of the report it made), not from
the close snapshot routine's run, which makes a day only once
(`core/close_reports.py`).

`report` has `trading_date`, `report` (the Markdown text), `report_sha` (its
SHA-256), `snapshot_sha`, `report_file`, `snapshot_file`, `replaces` (the
`report_sha` of the day's report you were told about before, or `None`) and
`attempt`.

- It runs in a thread of its own, one call per extension at a time, and the
  backend only looks at the pipeline's files after they are written and takes
  none of its locks: a slow, hanging or failing hook cannot break or delay the
  snapshot, the report or another extension.
- A call that raises is made again after 5 minutes, 30 minutes and 2 hours,
  then given up; each failure is listed in the app status.
- At least once: if the backend stops during a call, the call is made again
  once 15 minutes have passed since it began. Use `report_sha` as the
  idempotency key where the receiver takes one.
- The first time an extension's hook runs on a portfolio directory, the reports
  already finished then are recorded as known and not sent. Only checkpoints
  written in the last 7 days are looked at, every minute.
- What was told is kept in `desk-extensions.sqlite3` beside `desk.sqlite3`.
- It needs the engine scripts next to `dashboard/` (a source checkout); the
  Docker backend image has none, so the hook is never called there.

## Guarantees

- **A failing extension does not take the API down, and does not fail
  silently.** A module that cannot be imported, or whose `routers()`,
  `background_tasks()` or `on_close_report` cannot be used, contributes nothing:
  the tool factories and deliverers it registered are removed again. A router
  with its own `lifespan`, `on_startup` or `on_shutdown` is refused the same way
  (stage `router_lifespan`): FastAPI would run it inside the application's
  start-up, where one failure stops the whole API. Start-up work belongs in
  `background_tasks()`, which is isolated: a background task that raises or does
  not finish starting within 10 seconds of awaiting is left out and the others
  run (see "Never block the event loop" below); one that fails to stop is
  logged. Each failure
  is logged with the exception type and stack frames (never the message, which
  can carry data or credentials) and listed by `GET /api/app/status`:

  ```json
  "extensions": [
    {"name": "my_private.publisher", "status": "loaded",
     "provides": ["routers", "background_tasks", "agent_tools", "deliverers", "on_close_report"],
     "started": true, "errors": [{"stage": "on_close_report", "error": "ConnectionError",
                                  "at": "2026-11-20T07:41:02+00:00", "trading_date": "2026-11-20"}]},
    {"name": "my_private.radar", "status": "failed", "stage": "import",
     "error": "ModuleNotFoundError (feishu_sdk)"}
  ]
  ```

  The key is there only when `TRACKER_EXTENSIONS` names a module.
- **Routes are added, never replaced.** Built-in routes are mounted first and
  win a clash of method and path. Put yours under `/api/ext/<name>/`.
- **The same middleware.** Extension routes are behind the host allow-list
  (`TRACKER_ALLOWED_HOSTS`, [SECURITY.md](SECURITY.md)) and the CORS rule like
  every built-in route.
- **Ordering.** Tools registered at import are in the chat agent's tool list
  (built at start-up and again when the model settings are reloaded); a tool
  whose name is taken is skipped and logged. A deliverer registered at import
  receives every notification published afterwards.

## Modes

- **Demo mode** (`TRACKER_DEMO_MODE=1`) loads no extension at all; each is listed
  as `skipped`. The demo must never change data, and the backend cannot tell
  whether an extension's code (its GET routes included) only reads.
- **Lab-only mode** (`TRACKER_LAB_ONLY=1`) loads them, mounts their routes and
  registers their tools and deliverers, as it does the built-in ones, but starts
  no background task and calls no `on_close_report`: it starts no background
  task of its own either.

## What the author must do

- **Changes go through a confirmed proposal.** Anything that changes holdings,
  alert rules, trade plans, the watchlist, lessons or preferences must be
  proposed (`core.proposals.propose`, or a ledger preview) and applied only
  when the user confirms it on the page; a tool says that the user has to
  confirm and never claims the change was made. This is not enforced in code.
- **Protect your own routes.** The backend has no login. A route that writes
  should refuse requests from another site's pages (the `Origin` check of
  `backend/api/setup.py`), validate its input, answer with fixed messages and
  never echo an exception, a key or a token. GET routes must not write.
- **Never block the event loop.** Your routes, your background tasks (their
  start-up included) and your tool functions run on the application's event
  loop. A synchronous call that blocks there, such as an SDK call that waits for
  the network or a lock, stops the whole API, the routine scheduler (so the
  close snapshot starts late) and every extension started after yours, for as
  long as it blocks. Nothing can interrupt it: the 10-second start-up limit only
  covers code that awaits, and a blocked call never gets to an `await`. Run
  blocking work with `await asyncio.to_thread(...)` or in a thread of your own.
  Handle the errors of your own tasks once they run (only a failure to start or
  stop is seen by the backend). `on_close_report` already runs in a thread of
  its own.
- **Do not import another extension's module for what it registers.** A tool
  factory or deliverer counts as the extension whose module (or a module inside
  it) defines the function, so an extension that imports another named
  extension and then fails does not take that one's registrations with it.
  Everything else registered while your module is imported counts as yours and
  is removed if it fails. Put shared code in a module that registers nothing.
- **Do not import `backend.main`.** Your module is imported while it is being
  imported; use the `app` your task factories receive, or `request.app`.
- **Rely only on this page.** The hooks above, `agent_tools.register`,
  `notify.register_deliverer` and the `report` fields are the interface; other
  modules of the application can change in any release.
- Log identifiers and exception types, not amounts, holdings or secrets; text
  shown to the user is Chinese.

The Feishu workbench (`dashboard/feishu_workbench`) is a separate process and
does not load extensions.
