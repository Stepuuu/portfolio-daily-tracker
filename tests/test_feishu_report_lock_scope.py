"""The report pipeline keeps the portfolio lock except while delivering.

These tests run the adapter's real child process against the real
``run_pipeline`` state machine. Only the snapshot, the report text and the
network delivery are replaced by synthetic, file-controlled stand-ins.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import hashlib
import json
from pathlib import Path
import time

import pytest

from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError


ENGINE = Path(__file__).parents[1] / 'engine' / 'scripts'

# Loaded by the adapter's child process in place of the engine module. The real
# source is executed in this module's namespace, so ``run_pipeline`` calls the
# stand-ins below exactly as it would call the real stages.
SHIM = '''
import sys
import time
from pathlib import Path

ENGINE = Path({engine!r})
sys.path.insert(0, str(ENGINE))
exec(compile((ENGINE / 'portfolio_daily_update.py').read_text(encoding='utf-8'),
             str(ENGINE / 'portfolio_daily_update.py'), 'exec'))

ROOT = Path(__file__).resolve().parents[1]
PORTFOLIO_DIR = ROOT / 'portfolio'
HOLDINGS_DIR = PORTFOLIO_DIR / 'holdings'
REPORTS_DIR = ROOT / 'reports'
CONTROL = ROOT / 'control'


def _stage(name):
    with open(CONTROL / 'stages', 'a') as stream:
        stream.write(name + chr(10))
    (CONTROL / (name + '-started')).write_text('')
    if (CONTROL / ('hold-' + name)).exists():
        deadline = time.time() + 60
        while not (CONTROL / ('release-' + name)).exists():
            if time.time() > deadline:
                return False
            time.sleep(.02)
    return not (CONTROL / ('fail-' + name)).exists()


def load_config():
    return {{}}


def run_snapshot(day):
    return _stage('snapshot')


def run_report(day):
    REPORTS_DIR.mkdir(exist_ok=True)
    target = REPORTS_DIR / ('portfolio-' + day.replace('-', '') + '.md')
    target.write_text('Synthetic test report')
    return target


def send_feishu_report(day, text, config):
    return _stage('delivery')
'''


@pytest.fixture
def adapter(tmp_path):
    project = tmp_path / 'project'
    holdings = project / 'portfolio' / 'holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    (project / 'control').mkdir()
    (project / 'scripts' / 'portfolio_daily_update.py').write_text(SHIM.format(engine=str(ENGINE)))
    data = {'date': '2026-01-02', 'groups': {'Example': {
        'cash': 1000, 'cash_balances': {'CNY': 1000, 'USD': 200}, 'fund': 50,
        'positions': [{'ticker': 'NASDAQ:EXAMPLE', 'name': 'Example', 'quantity': 1.5, 'cost_price': 10}],
    }}}
    (holdings / '2026-01-02.json').write_text(json.dumps(data))
    return PortfolioAdapter(project, tmp_path / 'state')


def trade(**overrides):
    return {'operation': 'buy', 'account': 'Example', 'date': '2026-01-02',
            'ticker': 'NASDAQ:EXAMPLE', 'quantity': '0.125', 'price': '12', 'fee': '0.01',
            'currency': 'USD', **overrides}


def control(adapter, name):
    return adapter.project_dir / 'control' / name


def checkpoint(adapter):
    return adapter.portfolio_dir / 'pipeline-state' / '2026-01-02.json'


def stages(adapter):
    path = control(adapter, 'stages')
    return path.read_text().split() if path.exists() else []


def wait_for(path, pending, seconds=30):
    """Wait for a stage marker; surface an early pipeline failure instead of hanging."""
    deadline = time.time() + seconds
    while not path.exists():
        if pending.done():
            pending.result()
            raise AssertionError('pipeline finished before reaching ' + path.name)
        assert time.time() < deadline, 'pipeline never reached ' + path.name
        time.sleep(.02)


def test_confirmation_completes_while_report_delivery_is_in_progress(adapter):
    receipt = adapter.confirm(adapter.preview(trade()), 'first_update')
    reported = hashlib.sha256((adapter.holdings_dir / '2026-01-02.json').read_bytes()).hexdigest()
    control(adapter, 'hold-delivery').write_text('')
    with ThreadPoolExecutor(max_workers=2) as pool:
        report = pool.submit(adapter.run_report, receipt)
        try:
            wait_for(control(adapter, 'delivery-started'), report)
            # The push is claimed durably before the lock is released for it.
            assert json.loads(checkpoint(adapter).read_text())['push'] == 'uncertain'
            confirmation = pool.submit(
                lambda: adapter.confirm(adapter.preview(trade(quantity='0.25')), 'second_update'))
            try:
                second = confirmation.result(timeout=10)
            except FutureTimeout:
                pytest.fail('a confirmation waited for report delivery to finish')
            assert second['operation_id'] == 'second_update'
            assert not report.done()
        finally:
            control(adapter, 'release-delivery').write_text('')
        assert report.result(timeout=60)['report_status'] == 'complete'
    saved = json.loads((adapter.holdings_dir / '2026-01-02.json').read_text())
    assert saved['groups']['Example']['positions'][0]['quantity'] == 1.875
    state = json.loads(checkpoint(adapter).read_text())
    assert state['holdings_hash'] == reported and state['push'] == 'complete'
    assert stages(adapter) == ['snapshot', 'delivery']
    # The delivered report is final; the newer update needs its own report.
    assert adapter.run_report(receipt)['report_status'] == 'complete'
    assert stages(adapter) == ['snapshot', 'delivery']


def test_confirmation_still_waits_for_the_snapshot_stage(adapter):
    receipt = adapter.confirm(adapter.preview(trade()), 'first_update')
    preview = adapter.preview(trade(quantity='0.25'))
    control(adapter, 'hold-snapshot').write_text('')
    with ThreadPoolExecutor(max_workers=2) as pool:
        report = pool.submit(adapter.run_report, receipt)
        try:
            wait_for(control(adapter, 'snapshot-started'), report)
            confirmation = pool.submit(adapter.confirm, preview, 'second_update')
            with pytest.raises(FutureTimeout):
                confirmation.result(timeout=1)
        finally:
            control(adapter, 'release-snapshot').write_text('')
        assert report.result(timeout=60)['report_status'] == 'complete'
        assert confirmation.result(timeout=30)['operation_id'] == 'second_update'


def test_delivery_result_is_recorded_only_after_the_lock_is_held_again(adapter):
    receipt = adapter.confirm(adapter.preview(trade()), 'first_update')
    control(adapter, 'hold-delivery').write_text('')
    with ThreadPoolExecutor(max_workers=1) as pool:
        report = pool.submit(adapter.run_report, receipt)
        try:
            wait_for(control(adapter, 'delivery-started'), report)
            with adapter._lock():
                control(adapter, 'release-delivery').write_text('')
                with pytest.raises(FutureTimeout):
                    report.result(timeout=1)
                assert json.loads(checkpoint(adapter).read_text())['push'] == 'uncertain'
        finally:
            control(adapter, 'release-delivery').write_text('')
        assert report.result(timeout=60)['report_status'] == 'complete'
    assert json.loads(checkpoint(adapter).read_text())['push'] == 'complete'


def test_checkpoint_replaced_during_delivery_is_not_overwritten(adapter):
    receipt = adapter.confirm(adapter.preview(trade()), 'first_update')
    control(adapter, 'hold-delivery').write_text('')
    foreign = {'holdings_hash': 'f' * 64, 'snapshot': True, 'report': True, 'push': 'complete', 'complete': True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        report = pool.submit(adapter.run_report, receipt)
        try:
            wait_for(control(adapter, 'delivery-started'), report)
            # Another locked pipeline, for other holdings, recorded its own state.
            with adapter._lock():
                checkpoint(adapter).write_text(json.dumps(foreign))
        finally:
            control(adapter, 'release-delivery').write_text('')
        with pytest.raises(PortfolioError, match='日报尚未完成'):
            report.result(timeout=60)
    assert json.loads(checkpoint(adapter).read_text()) == foreign
    with pytest.raises(PortfolioError):
        adapter.run_report(receipt)
    assert stages(adapter) == ['snapshot', 'delivery']


def test_failed_delivery_is_not_repeated_by_a_retry(adapter):
    receipt = adapter.confirm(adapter.preview(trade()), 'first_update')
    control(adapter, 'fail-delivery').write_text('')
    with pytest.raises(PortfolioError, match='日报尚未完成'):
        adapter.run_report(receipt)
    control(adapter, 'fail-delivery').unlink()
    with pytest.raises(PortfolioError, match='日报尚未完成'):
        adapter.run_report(receipt)
    assert stages(adapter) == ['snapshot', 'delivery']
    assert json.loads(checkpoint(adapter).read_text())['push'] == 'uncertain'
