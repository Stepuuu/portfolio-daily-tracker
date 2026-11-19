"""``make demo`` never deletes anything but what the demo generator wrote.

The reviewer's case: a directory once made by ``create_demo.py`` was then used in full mode, and a brokerage
account was confirmed into its ledger through the API (125 SHA:600036). ``make demo`` (``--refresh``) took
it for an older demo, deleted it and wrote the demo again: the 125 shares were gone. Before round 4 (40868ac)
``make demo`` never touched an existing directory, so they were still there.

A refresh rebuilds a directory only when its content is provably the generator's for the options and day
its ``demo.json`` records (v3.2.1's demo, without one, had no ledger and no desk): no file the generator does
not write, no ledger event or preview and no desk row (watchlist, plans, rules, proposals, ...) that the
generator does not write for them. Anything else is refused (exit status 2) and left exactly as it was. The
rebuild is made beside the directory and swapped in, so an interrupted run never leaves it half deleted.
Fictional data only, apart from the user's own entry the test makes.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'engine' / 'scripts' / 'create_demo.py'
DAY = '2026-11-17'


def fingerprint(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(directory).rglob('*')) if path.is_file()}


def refresh(directory, *flags):
    return subprocess.run([sys.executable, str(SCRIPT), '--output', str(directory), '--refresh', '--today', DAY,
                           *flags], capture_output=True, text=True, timeout=300)


def held(directory):
    from core.ledger import confirmed_state
    state = confirmed_state(directory, timeout=10, as_of=DAY) or {}
    return {ticker: account['positions'][ticker]['quantity'] for account in state.get('accounts', {}).values()
            for ticker in account['positions']}


def older_format(directory):
    """Mark the demo in ``directory`` as made by an older version, so a refresh wants to rebuild it."""
    marker = json.loads((directory / 'demo.json').read_text())
    (directory / 'demo.json').write_text(json.dumps({**marker, 'format': marker['format'] - 1}))


def confirm(directory, events):
    from core.ledger import Ledger
    ledger = Ledger(directory / 'ledger.sqlite3')
    return ledger.confirm(ledger.propose(events)['id'])


def refused_and_untouched(root, portfolio, *flags):
    before = fingerprint(root)
    result = refresh(portfolio, *flags)
    assert result.returncode == 2, result.stdout + result.stderr
    assert str(portfolio) in result.stderr and '未做任何改动' in result.stderr
    assert fingerprint(root) == before, 'nothing in or beside the directory changed'
    return result


@pytest.fixture
def root(tmp_path):
    return tmp_path / '.demo'


def test_an_account_confirmed_in_a_former_demo_directory_is_never_deleted(root):
    """The reviewer's case; the baseline left the directory alone, so the 125 shares stay."""
    portfolio = root / 'portfolio'
    assert refresh(portfolio).returncode == 0                       # a demo without a ledger
    confirm(portfolio, [{'kind': 'opening', 'date': '2026-11-16', 'account': '券商账户', 'contributed_cny': '5000',
                         'cash_balances': {'CNY': '500'},
                         'positions': [{'ticker': 'SHA:600036', 'quantity': '125', 'cost_price': '36'}]}])
    refused_and_untouched(root, portfolio, '--ledger')
    assert held(portfolio) == {'SHA:600036': '125'}


def test_one_extra_confirmed_event_in_a_marked_demo_blocks_the_rebuild(root):
    portfolio = root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    older_format(portfolio)
    confirm(portfolio, [{'kind': 'deposit', 'date': '2026-11-16', 'account': '长期账户', 'currency': 'CNY',
                         'amount': '1000', 'note': '我自己记的入金'}])
    refused_and_untouched(root, portfolio, '--ledger')


def test_one_row_the_user_added_to_the_desk_blocks_the_rebuild(root, monkeypatch):
    portfolio = root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    older_format(portfolio)
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    from core import watchlist
    watchlist.add('SHA:600036', '我自己加的', name='招商银行')
    refused_and_untouched(root, portfolio, '--ledger')


def test_a_file_the_generator_does_not_write_blocks_the_rebuild(root):
    portfolio = root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    older_format(portfolio)
    (portfolio / 'history.csv').write_text('date,total_value\n2026-11-16,100\n')
    refused_and_untouched(root, portfolio, '--ledger')


def test_an_untouched_marked_demo_of_another_format_is_rebuilt(root):
    """Both ways: what round 4 added still works for a directory that is the generator's alone."""
    portfolio = root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    older_format(portfolio)
    result = refresh(portfolio, '--ledger')
    assert result.returncode == 0, result.stderr
    assert '重新生成' in result.stdout and json.loads((portfolio / 'demo.json').read_text())['format'] == 2
    assert {'SHA:DEMO', 'NASDAQ:DEMO'} <= set(held(portfolio))
    assert sorted(path.name for path in root.iterdir()) == ['portfolio'], 'no staging directory is left'


def test_an_interrupted_rebuild_leaves_the_directory_as_it_was(root, monkeypatch):
    import create_demo
    portfolio = root / 'portfolio'
    assert refresh(portfolio, '--ledger').returncode == 0
    older_format(portfolio)
    before = fingerprint(portfolio)
    seed = create_demo.seed_dashboard_demo

    def interrupted(destination, *args, **options):
        # The new demo, made in or beside the directory, is interrupted half made; a demo made elsewhere (to
        # compare the directory with) is not.
        if Path(destination).resolve().parent == root.resolve():
            raise KeyboardInterrupt
        return seed(destination, *args, **options)
    monkeypatch.setattr(create_demo, 'seed_dashboard_demo', interrupted)
    with pytest.raises(KeyboardInterrupt):
        create_demo.refresh_demo(portfolio, ledger=True, today=create_demo.date.fromisoformat(DAY))
    assert fingerprint(portfolio) == before
    assert sorted(path.name for path in root.iterdir()) == ['portfolio']


@pytest.mark.parametrize('dated', [DAY, '2026-08-28'])
def test_cash_v321s_own_set_cash_recorded_in_its_demo_is_never_deleted(root, dated):
    """The reviewer's round-6 case: in a v3.2.1 demo, v3.2.1's ``portfolio_manager.py set-cash`` recorded ¥37,500
    (a new dated holdings file, or with ``--date`` the last one changed). At 40868ac it survived; it is not
    the generator's, so the refresh refuses and leaves it."""
    from tests.test_demo_refresh import v321_demo
    portfolio = v321_demo(root / 'portfolio')
    holdings = json.loads((portfolio / 'holdings' / '2026-08-28.json').read_text())
    account = holdings['groups']['长期账户']
    account['cash_balances']['CNY'] = account['cash'] = 37500           # v3.2.1's set_cash_balance
    holdings.update(date=dated, updated_at=f'{DAY}T21:00:00')
    (portfolio / 'holdings' / f'{dated}.json').write_text(json.dumps(holdings, ensure_ascii=False, indent=2))
    refused_and_untouched(root, portfolio, '--ledger')
    assert json.loads((portfolio / 'holdings' / f'{dated}.json').read_text())['groups']['长期账户']['cash'] == 37500


def test_a_holdings_file_the_generator_did_not_write_blocks_the_rebuild_of_a_marked_demo(root):
    """The same for a demo with a marker, made without a ledger: a holdings file of the demo's own accounts and
    tickers that the generator did not write for its day."""
    portfolio = root / 'portfolio'
    assert refresh(portfolio).returncode == 0
    latest = sorted((portfolio / 'holdings').glob('*.json'))[-1]
    holdings = json.loads(latest.read_text())
    holdings['groups']['长期账户']['cash'] = holdings['groups']['长期账户']['cash_balances']['CNY'] = 37500
    (portfolio / 'holdings' / f'{DAY}.json').write_text(json.dumps({**holdings, 'date': DAY}, ensure_ascii=False))
    refused_and_untouched(root, portfolio, '--ledger')


# How a demo of this version can be made: without the dashboard (the engine-only image, which seeds nothing and
# counts the seeded history in weekdays instead of mainland trading days), without a ledger, with one, and with
# only its opening balance.
MADE = {'engine only': dict(ledger=False), 'no ledger': dict(ledger=False),
        'ledger': dict(ledger=True, history=True), 'opening only': dict(ledger=True, history=False)}


def made(monkeypatch, directory, how, day):
    """``create_demo`` of this version as ``how`` says, for ``day``; returns its ``refresh_demo`` the same way."""
    import create_demo
    with monkeypatch.context() as patch:
        if how == 'engine only':
            patch.setitem(sys.modules, 'core.demo_seed', None)      # the image has no dashboard package
        return create_demo.create_demo(directory, today=create_demo.date.fromisoformat(day), **MADE[how])


def refreshed(monkeypatch, directory, how):
    import create_demo
    with monkeypatch.context() as patch:
        if how == 'engine only':
            patch.setitem(sys.modules, 'core.demo_seed', None)
        return create_demo.refresh_demo(directory, today=create_demo.date.fromisoformat(DAY), **MADE[how])


@pytest.mark.parametrize('day', ['2026-10-15', DAY])     # National Day: trading days and weekdays part ways
@pytest.mark.parametrize('made_as', list(MADE))
@pytest.mark.parametrize('refreshed_as', list(MADE))
def test_an_untouched_demo_of_this_version_is_recognised_however_it_was_made(root, monkeypatch, day, made_as,
                                                                            refreshed_as):
    """cr_v7b B2: an engine-only demo made for 2026-10-15, refreshed with the dashboard and ``--ledger``, was
    rebuilt at 5db322a; round 6 refused it (exit 2) for the generator's own holdings/2026-09-21.json. Every way
    of making it, refreshed every way, is kept (same options) or rebuilt. Only a ledger cannot be verified
    without the dashboard: such a refresh is refused, as before."""
    import create_demo
    portfolio = root / 'portfolio'
    made(monkeypatch, portfolio, made_as, day)
    before = fingerprint(portfolio)
    if MADE[made_as]['ledger'] and refreshed_as == 'engine only':
        with pytest.raises(create_demo.NotOnlyTheDemo):
            refreshed(monkeypatch, portfolio, refreshed_as)
        assert fingerprint(portfolio) == before
        return
    same = MADE[made_as] == MADE[refreshed_as] or {made_as, refreshed_as} == {'engine only', 'no ledger'}
    assert refreshed(monkeypatch, portfolio, refreshed_as) == ('kept' if same else 'rebuilt')
    assert sorted(path.name for path in root.iterdir()) == ['portfolio']
