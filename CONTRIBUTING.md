# Contributing

Start with `make setup`, then `make demo` to explore fictional data without an API key.
Use Python 3.10+ and Node.js 22.12+ (Node 22 LTS recommended).

Before proposing a change:

```bash
python3 -m pip install pytest
make test
cd dashboard/frontend && npm run build
```

For accounting bugs, include a **fictional** before/after example: currency, quantity,
price, cash movement, date, and expected result. Tests should run without API keys or
live quotes. Do not include your actual holdings, account exports, cookies, API keys,
or private reports in issues, screenshots, fixtures, or pull requests.

Keep one PR focused on one user-visible outcome. Explain what failed, what changes,
and how it was verified. The [roadmap](docs/ROADMAP.md) lists bounded contributions
and acceptance criteria. Bug reports and documentation fixes are equally welcome.

For the full ledger browser journey (fictional data only):

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tests/browser_smoke.py
```

The test starts temporary backend/frontend processes on available loopback ports
and stops them afterwards. It covers opening preview, confirmation, fractional
trades, duplicate CSV, reversal, journal entries and mobile layout. External AI
initialization is disabled in this test. It does not require Docker or API keys.

Run `python3 scripts/check_public_artifacts.py` after staging files. This catches
common private artifacts and identifiers without printing their contents. It is
an additional check, not a guarantee that arbitrary prose or images contain no
private information; review fixtures, screenshots and commit metadata too.
