# Security and deployment scope

Portfolio Daily Tracker is a single-user, self-hosted application for your own
machine. The API has no login and no user isolation: anyone who can reach its
port can read and change your portfolio, settings and assistant. Keep the default
loopback bindings, or put an authenticated private gateway in front of it before
allowing remote access.

What the application does protect, and what it does not, is described in
[docs/SECURITY.md](docs/SECURITY.md): the host allow-list against DNS rebinding,
read-only demo mode, write-only API keys, the untrusted-text boundary for the
assistant, and confirmation of every proposed change.

## Opening the app under another name

The backend answers only requests whose `Host` header names `localhost`,
`127.0.0.1`, `[::1]` or the Compose service `backend`; any other name receives
`400 Invalid host header`. This keeps a website from reading the local API
through a DNS name it points at your machine (DNS rebinding). It is not
authentication and does not make remote access safe. If you open the
application under another name, such as a LAN address, a reverse-proxy domain,
or `BACKEND_HOST=0.0.0.0` with the development server, list those names in
`TRACKER_ALLOWED_HOSTS` (comma separated; ports are ignored; `*.example.com`
patterns are accepted) and restart the backend. `TRACKER_ALLOWED_HOSTS=*`
turns the check off; use it only behind a gateway that validates the host.

## Code execution

Custom strategy upload executes Python with the backend process's privileges.
It is disabled by default. Only trusted local users should set
`TRACKER_ALLOW_CUSTOM_STRATEGIES=1`; this flag does not create a sandbox.
Trusted Python research plugins (`TRACKER_LAB_PLUGINS`) also run with local
process privileges, and so do extensions (`TRACKER_EXTENSIONS`, see
[docs/EXTENSIONS.md](docs/EXTENSIONS.md)).

## Data sent elsewhere

Research model profiles store environment variable names, never secret values.
Enabling an external model sends your question, the portfolio context and tool
results (or the selected research context) to that provider. Research schedules
never authorize account changes or real orders. Exports may include symbols,
amounts and questions and require review before sharing.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's security advisory mechanism
(Security › Report a vulnerability) if available. Otherwise open an issue asking
for a private reporting channel without including exploit details, keys or
account data. Never publish real portfolios.
