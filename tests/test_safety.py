import json
import asyncio
import pytest
from portfolio_accounting import atomic_json_dump, currency_from_text
from create_demo import create_demo


def test_atomic_write_preserves_previous_data_on_serialization_failure(tmp_path):
    path = tmp_path / 'holdings.json'
    atomic_json_dump({'cash': 100}, path)
    with pytest.raises(ValueError):
        atomic_json_dump({'cash': float('nan')}, path)
    assert json.loads(path.read_text()) == {'cash': 100}
    assert list(tmp_path.glob('*.tmp')) == []


def test_demo_never_overwrites_existing_directory(tmp_path):
    path = tmp_path / 'demo'
    assert create_demo(path) == 20
    with pytest.raises(FileExistsError):
        create_demo(path)
    assert all(json.loads(p.read_text())['demo'] for p in (path / 'snapshots').glob('*.json'))


@pytest.mark.parametrize('text,currency', [('现金100', 'CNY'), ('美元现金变为100', 'USD'), ('现金100港元', 'HKD')])
def test_explicit_cash_currency(text, currency):
    assert currency_from_text(text) == currency


def test_mixed_currency_clause_is_rejected():
    with pytest.raises(ValueError):
        currency_from_text('USD现金100港元')


def test_custom_strategy_upload_requires_opt_in(monkeypatch):
    from backend.api import backtest
    from fastapi import HTTPException
    monkeypatch.delenv('TRACKER_ALLOW_CUSTOM_STRATEGIES', raising=False)
    request = backtest.StrategyUploadModel(filename='example.py', code='class Strategy: pass')
    with pytest.raises(HTTPException) as error:
        asyncio.run(backtest.upload_strategy(request))
    assert error.value.status_code == 403


def test_custom_strategy_filename_cannot_escape_directory(monkeypatch):
    from backend.api import backtest
    from fastapi import HTTPException
    monkeypatch.setenv('TRACKER_ALLOW_CUSTOM_STRATEGIES', '1')
    request = backtest.StrategyUploadModel(filename='../outside.py', code='class Strategy: pass')
    with pytest.raises(HTTPException) as error:
        asyncio.run(backtest.upload_strategy(request))
    assert error.value.status_code == 422


def test_env_key_is_not_saved_with_settings(tmp_path, monkeypatch):
    from config.settings import Config
    path = tmp_path / 'config.json'
    monkeypatch.setenv('TRACKER_CONFIG_FILE', str(path))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only-sentinel')
    config = Config()
    assert config.get_current_api_group()['api_key'] == 'test-only-sentinel'
    config.set('llm.temperature', 0.3)
    assert 'test-only-sentinel' not in path.read_text()


def test_partial_parse_does_not_save_or_publish(monkeypatch):
    import portfolio_daily_update as daily
    data = {'groups': {'Account': {'cash': 100, 'positions': []}}}
    monkeypatch.setattr(daily, 'clone_holdings', lambda day: None)
    monkeypatch.setattr(daily, 'load_holdings', lambda day: data)
    calls = []
    monkeypatch.setattr(daily, 'save_holdings', lambda *args: calls.append('save'))
    monkeypatch.setattr(daily, 'run_pipeline', lambda *args: calls.append('pipeline'))
    assert daily.action_update('2026-09-09', 'Account现金变为200，unrecognized operation') is False
    assert calls == []


def test_foreign_cash_parser_preserves_cny():
    import portfolio_daily_update as daily
    data = {'groups': {'Account': {'cash': 100, 'positions': []}}}
    changes = daily.parse_and_apply_changes(data, 'Account美元现金变为200')
    assert changes[0]['currency'] == 'USD'
    assert data['groups']['Account']['cash_balances'] == {'CNY': 100, 'USD': 200}


def test_oversell_is_rejected():
    import portfolio_daily_update as daily
    data = {'groups': {'Account': {'cash': 100, 'positions': [
        {'name': 'Example', 'ticker': 'SHA:TEST', 'quantity': 10, 'cost_price': 20}
    ]}}}
    with pytest.raises(ValueError):
        daily.parse_and_apply_changes(data, '卖出20股Example')


def test_engine_modules_use_the_same_data_directory(tmp_path):
    import os
    import sys
    import subprocess
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / 'engine/scripts'
    environment = {**os.environ, 'PORTFOLIO_DIR': str(tmp_path), 'PYTHONPATH': str(scripts)}
    subprocess.run([sys.executable, '-c',
        'import os, portfolio_snapshot, portfolio_manager, portfolio_report, portfolio_daily_update; '
        'assert all(str(m.PORTFOLIO_DIR) == os.environ["PORTFOLIO_DIR"] for m in '
        '[portfolio_snapshot, portfolio_manager, portfolio_report, portfolio_daily_update])'],
        env=environment, check=True)
