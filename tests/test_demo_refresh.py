"""``make demo`` rebuilds a demo directory left by an older version, and nothing else.

v3.2.1's ``make demo`` wrote ``.demo/portfolio`` without a ledger. After an upgrade the target skipped
creation because the directory existed, so Today showed no holdings and asked for an opening balance the
demo refuses. ``create_demo.py --refresh`` (what ``make demo`` runs) keeps a demo of this version made
with the same options, rebuilds in place a directory that is recognisably a generated demo of another
version (its ``demo.json`` marker, or v3.2.1's layout: only the demo's two accounts, invented tickers and
snapshots marked ``demo``) when it holds nothing but what the generator wrote, creates a missing one, and
refuses anything else (exit status 2) without touching it. Nothing outside the directory is touched.
Fictional data only.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'engine' / 'scripts' / 'create_demo.py'
DAY = '2026-11-17'


def fingerprint(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(directory).rglob('*')) if path.is_file()}


def v321_demo(directory):
    """What v3.2.1's ``create_demo.py --output`` wrote: holdings and snapshots of August 2026, no ledger
    (its holdings exactly; its snapshots shortened)."""
    (directory / 'holdings').mkdir(parents=True)
    (directory / 'snapshots').mkdir()
    (directory / 'config.json').write_text(json.dumps({'groups': {'长期账户': {}, '研究账户': {}}}, ensure_ascii=False))
    for offset in range(28):
        day = date(2026, 8, 3) + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        holdings = {'date': day.isoformat(), 'groups': {
            '长期账户': {'cost_basis': 81000, 'cash': 20000, 'fund': 0,
                     'cash_balances': {'CNY': 20000, 'USD': 1000, 'HKD': 5000},
                     'positions': [{'name': '示例宽基', 'ticker': 'SHA:DEMO', 'quantity': 1000, 'cost_price': 50}]},
            '研究账户': {'cost_basis': 29000, 'cash': 10000, 'fund': 5000,
                     'positions': [{'name': '示例研究标的', 'ticker': 'NASDAQ:DEMO', 'quantity': 20, 'cost_price': 100}]}}}
        (directory / 'holdings' / f'{day}.json').write_text(json.dumps(holdings, ensure_ascii=False))
        (directory / 'snapshots' / f'{day}.json').write_text(json.dumps(
            {'date': day.isoformat(), 'demo': True, 'summary': {'total_value': 100000}}))
    return directory


def refresh(directory, *flags):
    return subprocess.run([sys.executable, str(SCRIPT), '--output', str(directory), '--refresh', '--today', DAY,
                           *flags], capture_output=True, text=True)


def held(directory):
    from core.ledger import confirmed_state
    state = confirmed_state(directory, timeout=10, as_of=DAY) or {}
    return {ticker for account in state.get('accounts', {}).values() for ticker in account['positions']}


@pytest.fixture
def demo_root(tmp_path):
    """``.demo`` with the research store and a file beside the portfolio, which must survive."""
    root = tmp_path / '.demo'
    (root / 'research').mkdir(parents=True)
    (root / 'research' / 'experiment.json').write_text('{"kept": true}')
    (root / 'notes.txt').write_text('beside the demo')
    return root


def outside(root):
    return {name: digest for name, digest in fingerprint(root).items() if not name.startswith('portfolio/')}


def test_a_v321_demo_is_rebuilt_in_place_with_its_ledger(demo_root):
    portfolio = v321_demo(demo_root / 'portfolio')
    before = outside(demo_root)
    result = refresh(portfolio, '--ledger')
    assert result.returncode == 0, result.stderr
    assert {'SHA:DEMO', 'NASDAQ:DEMO'} <= held(portfolio), 'Today has the demo holdings again'
    marker = json.loads((portfolio / 'demo.json').read_text())
    assert marker['demo'] is True and marker['ledger'] is True and marker['made_for'] == DAY
    assert not (portfolio / 'holdings' / '2026-08-03.json').exists(), 'the old files are gone'
    assert outside(demo_root) == before, 'nothing outside the demo directory changed'
    assert '重新生成' in result.stdout


def test_a_demo_of_this_version_is_kept_as_it_is(demo_root):
    portfolio = demo_root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    before = fingerprint(demo_root)
    again = refresh(portfolio, '--ledger')
    assert again.returncode == 0 and fingerprint(demo_root) == before
    assert '沿用' in again.stdout


def test_a_demo_made_without_a_ledger_is_rebuilt_when_the_ledger_is_asked_for(demo_root):
    portfolio = demo_root / 'portfolio'
    assert refresh(portfolio).returncode == 0 and not (portfolio / 'ledger.sqlite3').exists()
    assert refresh(portfolio, '--ledger').returncode == 0
    assert {'SHA:DEMO', 'NASDAQ:DEMO'} <= held(portfolio)


def test_a_demo_marked_with_another_format_is_rebuilt(demo_root):
    portfolio = demo_root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    marker = json.loads((portfolio / 'demo.json').read_text())
    (portfolio / 'demo.json').write_text(json.dumps({**marker, 'format': marker['format'] - 1}))
    # Round 5: a file the generator does not write is no longer removed; it blocks the rebuild
    # (tests/test_demo_refresh_keeps_data.py).
    assert refresh(portfolio, '--ledger').returncode == 0
    assert json.loads((portfolio / 'demo.json').read_text())['format'] == marker['format']


def test_a_missing_demo_is_created(demo_root):
    result = refresh(demo_root / 'portfolio', '--ledger')
    assert result.returncode == 0 and {'SHA:DEMO'} <= held(demo_root / 'portfolio')


@pytest.mark.parametrize('damage', ['real_ticker', 'other_account', 'no_snapshots', 'unmarked_snapshot'])
def test_a_directory_that_is_not_recognisably_a_demo_is_refused_and_left_alone(demo_root, damage):
    portfolio = v321_demo(demo_root / 'portfolio')
    if damage == 'real_ticker':
        path = sorted((portfolio / 'holdings').glob('*.json'))[-1]
        holdings = json.loads(path.read_text())
        holdings['groups']['长期账户']['positions'][0]['ticker'] = 'SHA:600519'
        path.write_text(json.dumps(holdings, ensure_ascii=False))
    elif damage == 'other_account':
        (portfolio / 'config.json').write_text(json.dumps({'groups': {'我的账户': {}}}, ensure_ascii=False))
    elif damage == 'no_snapshots':
        for path in (portfolio / 'snapshots').glob('*.json'):
            path.unlink()
    else:
        path = sorted((portfolio / 'snapshots').glob('*.json'))[0]
        path.write_text(json.dumps({'date': path.stem, 'summary': {}}))
    before = fingerprint(demo_root)
    result = refresh(portfolio, '--ledger')
    assert result.returncode == 2 and '不是演示数据目录' in result.stderr
    assert fingerprint(demo_root) == before


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason='needs symbolic links')
def test_a_symbolic_link_is_refused_and_its_target_left_alone(demo_root, tmp_path):
    target = v321_demo(tmp_path / 'elsewhere')
    (demo_root / 'portfolio').symlink_to(target, target_is_directory=True)
    before = fingerprint(target)
    result = refresh(demo_root / 'portfolio', '--ledger')
    assert result.returncode == 2 and fingerprint(target) == before


def test_make_demo_refreshes_instead_of_skipping_an_existing_directory():
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    demo = makefile.split('\ndemo:', 1)[1].split('\n\n', 1)[0]
    assert '--refresh' in demo and '[ -d .demo/portfolio ]' not in demo
