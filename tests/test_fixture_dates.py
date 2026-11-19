"""Every demo the tests seed is made for the day their scripted clock shows, whatever day the tests run on.

A demo made for the runner's date (``create_demo.py`` without ``--today``) holds an opening balance a few
weeks before that date. Against a clock fixed at 2026-11-17 that opening is in the future from
2027-01-06 on: the ledger then has no account on the clock's day and the pages show no holding. So every
demo a browser smoke makes, and every seeded ledger a test asks ``create_demo`` for, names its day; here the smokes' seeds and the Python fixture run with the runner's clock faked to dates in 2027 and
2028 (``tests/runner_date.py``) and must still hold the demo's positions on their day. Fictional data only.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.runner_date import RUNNER_DATE_ENV, create_demo_argv, runner_datetime

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'
FUTURE = ['2027-06-01', '2028-03-01']
DAY = '2026-11-17'   # the day the smokes' scripted clocks show
needs_backend = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')


def calls(text, start):
    """The text of every call beginning with ``start``, up to its closing parenthesis."""
    found = []
    for match in re.finditer(re.escape(start), text):
        depth, index = 0, text.index('(', match.start())
        for index in range(index, len(text)):
            depth += {'(': 1, ')': -1}.get(text[index], 0)
            if depth == 0:
                break
        found.append(text[match.start():index + 1])
    return found


def test_every_browser_smoke_makes_its_demo_for_a_fixed_day():
    smokes = sorted(TESTS.glob('browser_*smoke.py'))
    assert len(smokes) >= 10
    unpinned = []
    for smoke in smokes:
        text = smoke.read_text(encoding='utf-8')
        for call in calls(text, 'subprocess.run('):
            if 'create_demo' in call and '--today' not in call:
                unpinned.append(f'{smoke.name}: {" ".join(call.split())[:120]}')
    assert unpinned == []


def test_every_seeded_ledger_in_the_tests_is_made_for_a_fixed_day():
    unpinned = []
    for test in sorted(TESTS.rglob('test_*.py')):
        for call in calls(test.read_text(encoding='utf-8'), 'create_demo('):
            if 'ledger=' in call and 'today=' not in call:
                unpinned.append(f'{test.relative_to(ROOT)}: {" ".join(call.split())}')
    assert unpinned == []


def seed(tmp_path, runner, *args):
    out = tmp_path / 'portfolio'
    subprocess.run(create_demo_argv('--output', str(out), *args, runner=runner),
                   env={**os.environ, RUNNER_DATE_ENV: runner}, check=True, stdout=subprocess.DEVNULL)
    return out


def held_on(portfolio, day):
    from core.ledger import confirmed_state
    state = confirmed_state(portfolio, timeout=10, as_of=day)
    return {ticker for account in (state or {}).get('accounts', {}).values() for ticker in account['positions']}


# The seeds of the smokes: backtest and market (opening only), setup (the whole demo), lab (no ledger).
@pytest.mark.parametrize('runner', FUTURE)
@pytest.mark.parametrize('flags', [('--ledger', '--opening-only'), ('--ledger',)])
def test_a_smokes_demo_holds_its_positions_on_its_day_whatever_the_runner_date(tmp_path, runner, flags):
    portfolio = seed(tmp_path, runner, *flags, '--today', DAY)
    assert {'SHA:DEMO', 'NASDAQ:DEMO'} <= held_on(portfolio, DAY)


@pytest.mark.parametrize('runner', FUTURE)
def test_the_research_smokes_plain_demo_is_dated_before_its_day(tmp_path, runner):
    portfolio = seed(tmp_path, runner, '--today', DAY)
    dated = sorted(path.stem for path in (portfolio / 'holdings').glob('*.json'))
    assert dated and dated[-1] < DAY


def test_without_its_day_a_demo_made_in_2027_holds_nothing_on_the_smokes_day(tmp_path):
    """The defect the pinning prevents, reproduced with the faked runner date (so the fake is effective)."""
    portfolio = seed(tmp_path, '2027-06-01', '--ledger', '--opening-only')
    assert held_on(portfolio, DAY) == set()


@needs_backend
@pytest.mark.parametrize('runner', FUTURE)
def test_the_demo_mode_fixture_shows_its_holdings_whatever_the_runner_date(tmp_path, monkeypatch, runner):
    import create_demo
    from core.market.runtime import set_market_runtime
    from tests.test_today_api import demo_client
    monkeypatch.setattr(create_demo, 'datetime', runner_datetime(runner))
    client = demo_client(tmp_path, monkeypatch, ledger=True)
    try:
        data = client.get('/api/today').json()
        assert [row['ticker'] for account in data['accounts'] for row in account['positions']] == ['SHA:DEMO', 'NASDAQ:DEMO']
    finally:
        set_market_runtime(None)
