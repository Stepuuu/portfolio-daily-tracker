"""GET /api/app/status: the mode the backend runs in, the ledger revision and the version. Read-only."""
import pytest
from fastapi.testclient import TestClient

from core.ledger import Ledger


@pytest.fixture
def client(tmp_path, monkeypatch):
    import backend.main as main
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_LAB_ONLY'):
        monkeypatch.delenv(name, raising=False)
    # No lifespan; the real application answers loopback Host headers only.
    return TestClient(main.app, base_url='http://127.0.0.1')


def opening(account='长期账户'):
    return {'kind': 'opening', 'date': '2026-09-01', 'account': account, 'cash_balances': {'CNY': '1000'},
            'positions': [], 'contributed_cny': '1000', 'fund_cny': '0'}


def test_full_mode_without_a_ledger_reports_it_inactive_and_creates_nothing(client, tmp_path):
    response = client.get('/api/app/status')
    assert response.status_code == 200
    data = response.json()
    assert data['mode'] == 'full'
    assert data['ledger'] == {'active': False, 'revision': 0}
    assert data['version'] == client.app.version
    assert not (tmp_path / 'ledger.sqlite3').exists()


def test_a_confirmed_ledger_is_active_with_its_revision(client, tmp_path):
    ledger = Ledger(tmp_path / 'ledger.sqlite3')
    assert client.get('/api/app/status').json()['ledger'] == {'active': False, 'revision': 0}   # empty file
    ledger.confirm(ledger.propose([opening()])['id'])
    assert client.get('/api/app/status').json()['ledger'] == {'active': True, 'revision': 1}


def test_an_unreadable_ledger_is_unknown_not_inactive(client, tmp_path):
    (tmp_path / 'ledger.sqlite3').write_bytes(b'this is not a database' * 100)
    assert client.get('/api/app/status').json()['ledger'] == {'active': None, 'revision': None}


@pytest.mark.parametrize('variables, expected', [
    ({'TRACKER_DEMO_MODE': '1'}, 'demo'),
    ({'TRACKER_LAB_ONLY': '1'}, 'lab'),
    ({'TRACKER_LAB_ONLY': '1', 'TRACKER_DEMO_MODE': '1'}, 'lab'),
    ({'TRACKER_DEMO_MODE': '0', 'TRACKER_LAB_ONLY': '0'}, 'full'),
])
def test_mode_follows_the_environment(client, monkeypatch, variables, expected):
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert client.get('/api/app/status').json()['mode'] == expected


def test_it_is_registered_once_and_only_reads(client, monkeypatch):
    from backend import features
    assert [prefix for _, prefix, _ in features.routers()].count('/api/app') == 1
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    assert client.get('/api/app/status').status_code == 200
    assert client.post('/api/app/status').status_code in (403, 405)
