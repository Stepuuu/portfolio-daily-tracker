# Security and deployment scope

This is a single-user, self-hosted workspace. The API does not implement user
authentication or tenant isolation. Keep the default loopback bindings, or place
it behind an authenticated private gateway before allowing remote access.

Custom strategy upload executes Python with the backend process's privileges.
It is disabled by default. Only trusted local users should set
`TRACKER_ALLOW_CUSTOM_STRATEGIES=1`; this flag does not create a sandbox.

Demo mode blocks portfolio and configuration mutations, skips AI initialization,
and allows manual research on isolated synthetic data. It is intended for
local evaluation and is not a security boundary for a public deployment.

Research model profiles store environment variable names, never secret values.
Official CLI adapters delegate authentication to the client and disable native
tools for structured research calls. Enabling an external model sends the selected
research context and computed evidence to that provider. Trusted Python research
plugins run with local process privileges; plugin registration is not a sandbox.
Research schedules never authorize account changes or real orders. Exports may
include user-selected symbols and questions and require review before sharing.

Report vulnerabilities privately through GitHub's security advisory mechanism if
available. Otherwise open an issue asking for a private reporting channel without
including exploit details, keys, or account data. Never publish real portfolios.
