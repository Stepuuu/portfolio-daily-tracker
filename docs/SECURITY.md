# Security model / 安全说明

This describes what the application protects, as implemented in 4.0.0, and what
it does not. The short version: it is a **single-user application for your own
machine**. Anyone who can reach its port can read and change everything in it.

中文摘要：这是给一个人在自己电脑上用的程序，没有登录。能访问到它端口的人就能查看和修改全部数据。
默认只监听本机地址；不要把端口直接暴露到局域网或公网。密钥只写不读；助手只能提出方案，必须由你确认；
新闻、行情源和你保存的文字以「不可信数据」交给模型。

## What is protected

**Local by default.** `start.sh` binds the backend and the web app to
`127.0.0.1` (`BACKEND_HOST`, `FRONTEND_HOST`), and `docker-compose.yml` publishes
both ports on `127.0.0.1` only. The browser API allows requests from the web
app's own origins (`http://localhost:3000`, `http://127.0.0.1:3000`).

**Host allow-list.** The API answers only requests whose `Host` header is
`localhost`, `127.0.0.1`, `[::1]` or the Compose service name `backend`; any
other name gets `400 Invalid host header`. This stops a website from reading the
local API through a DNS name it points at your machine (DNS rebinding). Add the
names you really use to `TRACKER_ALLOWED_HOSTS` (comma separated; ports are
ignored; `*.example.com` patterns are accepted). `TRACKER_ALLOWED_HOSTS=*` turns
the check off. The setup wizard's writes (model key, legacy migration, flags)
additionally refuse a request sent from a page of another site (`Origin` check).

**Every change to your money is confirmed by you.** The ledger changes only when
a preview is confirmed (确认入账); confirmation is idempotent and checks the
ledger revision the preview was made against. Alert rules, trade plans,
watchlist additions, lessons and profile changes proposed by the assistant or a
routine are stored as proposals and applied only when you confirm them in the
web app; no API endpoint creates such a proposal. An import preview
is bound to its import and refused once the import or the ledger changed.

**The model's input is fenced.** Text the application did not write itself
(headlines and announcements, provider error texts, and names, notes, plan
reasoning and briefs stored in the app) reaches the model only inside
`untrusted_text` with a fixed note saying it is data, not instructions. Numbers
in a tool call are read as exact decimals; money and quantities that arrive as
floating-point numbers are refused. Lessons the model extracts from a
conversation become proposals instead of entering its memory.

**Replies cannot leak by loading images.** Assistant replies are rendered without
loading remote images: a remote image becomes a link that shows its address and
loads nothing until clicked. Stored conversation images are served only from
the conversation image folder, by validated file name.

**Keys are write-only.** The setup API never returns, logs or echoes a model API
key; it reports only whether a key is configured and where from. The settings
file (`TRACKER_CONFIG_FILE`) is made private (mode 0600) before a key is written.
The connection test reports one of a fixed set of messages, never the provider's
error text. Research connections store the *name* of an environment variable,
never its value. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are read at use time.

**A key stays with its address.** A saved key belongs to the address (scheme,
host and port) and the provider it was saved for, and is never sent anywhere
else: saving the group with another address or provider drops the stored key
and every credential header. Saving without a typed key is accepted only at the
address the stored key belongs to, at an official host whose key is in the
server environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), or on a loopback
host; anywhere else the save is refused until a key is typed, and the form asks
for one exactly where the API would refuse. The connection test only uses what
is saved. A custom
`Authorization` header template without a key carries the group's key or is
left out. The wizard shows the saved address with its path (never user info,
query or fragment, which it refuses anyway), so do not put a key in the path.

**Errors and logs.** Messages shown to you or to the model are fixed texts, never
an exception's message or a provider's response. The alert monitor, the
notification sender and the routines log identifiers and error types, not
prices, amounts or holdings.

**Private files.** `desk.sqlite3` and the research store are created with owner-only
permissions. An uploaded broker file is parsed in memory and never stored; the
parsed session is deleted after 30 days (SQLite `secure_delete`; a copy can
remain in the write-ahead log until it is checkpointed, and in any backup). Upload
size is limited (5 MB), and a workbook is measured while it is unpacked, so a
small file cannot expand without bound.

**Reads do not write.** Page reads and the assistant's read tools never create a
file and never change the ledger or the desk store.

**Demo mode** (`TRACKER_DEMO_MODE=1`) refuses every write request (`403 Demo mode
is read-only`) except backtest and research runs on its own synthetic data,
the proposal store refuses in-process writes too, and the alert monitor and the
routines do not start. It is meant for local evaluation and is not a security
boundary for a public deployment.

## What is not protected

- **Anyone who can reach the port.** There is no login, no user isolation and no
  rate limiting. Exposing the backend or the web app on a LAN or the internet
  (for example `BACKEND_HOST=0.0.0.0`, a port forward, or a reverse proxy without
  authentication) gives full read and write access to your portfolio, your
  settings and the assistant. Put an authenticated gateway in front if you need
  remote access.
- **Other software on the same machine.** Local users, processes and browser
  extensions that can reach `127.0.0.1` or read the data directory are trusted.
  The ledger, the backups and `.env` are ordinary files.
- **Backups and exports** are complete, unencrypted copies of your data. Store
  them like the originals.
- **The model provider** receives what you send the assistant: your question, the
  Today valuation, tool results and images. Agent research sends the research
  context to the chosen provider. Exports are not automatically redacted.
- **Feishu** stores the cards and messages it delivers; see
  [FEISHU_WORKBENCH.md](FEISHU_WORKBENCH.md).
- **Custom strategy upload** (`TRACKER_ALLOW_CUSTOM_STRATEGIES=1`, off by default)
  runs Python with the backend's privileges, and so do research plugins
  (`TRACKER_LAB_PLUGINS`). Neither is a sandbox; enable them only for code you
  trust.
- **Extensions** (`TRACKER_EXTENSIONS`, none by default) run inside the backend
  with all of its privileges: they can read and change everything, and their
  routes have no protection beyond the host allow-list. Only their author keeps
  them to the propose-and-confirm rule. Demo mode loads none. See
  [EXTENSIONS.md](EXTENSIONS.md).
- **External agents** with shell or API access to your machine are outside the
  propose-and-confirm boundary.
- **Quote data** comes from public endpoints without authentication of the data;
  a wrong price from a source is shown as that source's price, with its time.

## Credential handling

Shipped defaults contain no API credentials. Set `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` in the process environment for the corresponding official
endpoint, or save a key with the setup wizard (it goes to the file named by
`TRACKER_CONFIG_FILE`; keep that file outside version control). Do not add
credentials to `dashboard/config/settings.py`, example files, tests or
documentation.

Run `python scripts/check_public_artifacts.py` before publishing. CI also runs
this check. It rejects private runtime artifacts, common token formats, machine
paths and nonempty credentials in the Python default configuration, including
custom Authorization headers without a recognizable token prefix. It prints only
file names, line numbers and categories. It is a guardrail, not a complete
secret detector or a test that an old credential has been revoked.

For an independent scan, use Gitleaks locally or GitGuardian CLI with your own
account. Scan a clean checkout or an export of tracked files; do not accidentally
upload private portfolio data, ignored configuration or local audit evidence.

### If a credential was published

1. Identify the provider, owner and applications using the credential. Replace
   it in dependent applications and revoke the exposed value at the provider.
   Check access and billing logs for unexpected use.
2. Remove the value from shipped code and examples. `.gitignore` alone does not
   remove an already tracked file.
3. Scan current files and reachable Git history separately. A clean branch does
   not prove an old commit, pull-request reference or external clone is gone.
4. Record provider-side revocation separately from code cleanup. Contact GitHub
   Support when inaccessible-to-push PR references or cached commit views still
   expose sensitive history.
5. Close the incident only after checking every affected source.

Keep incident evidence and scan reports outside version control. Do not paste
credential values, private machine paths, customer data or raw API responses into
public issues or commits.

See [GitGuardian's remediation guidance](https://docs.gitguardian.com/secrets-detection/secrets-detection-engine/leaks_remediation)
and [GitHub's sensitive-data removal guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## Reporting a vulnerability

Report it privately through GitHub's security advisory form for this repository
(Security › Report a vulnerability) if it is available. Otherwise open an issue
that asks for a private reporting channel, without exploit details, keys or
account data. Please include the version (设置 › 关于与帮助, or `GET /api/app/status`)
and whether the issue needs network access to the port or only a crafted file,
page or message. Never publish real portfolios or credentials in a report.
