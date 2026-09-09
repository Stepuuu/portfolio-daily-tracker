# Security and deployment scope

This is a single-user, self-hosted workspace. The API does not implement user
authentication or tenant isolation. Keep the default loopback bindings, or place
it behind an authenticated private gateway before allowing remote access.

Custom strategy upload executes Python with the backend process's privileges.
It is disabled by default. Only trusted local users should set
`TRACKER_ALLOW_CUSTOM_STRATEGIES=1`; this flag does not create a sandbox.

Demo mode blocks mutation requests and skips AI initialization. It is intended for
local evaluation and is not a security boundary for a public deployment.

Report vulnerabilities privately through GitHub's security advisory mechanism if
available. Otherwise open an issue asking for a private reporting channel without
including exploit details, keys, or account data. Never publish real portfolios.
