import importlib.util
import json
from pathlib import Path


def pipeline(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / 'engine/scripts/portfolio_daily_update.py'
    spec = importlib.util.spec_from_file_location('isolated_daily_report', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    holdings = tmp_path / 'holdings'
    holdings.mkdir()
    (holdings / '2026-01-02.json').write_text('{"groups":{}}')
    reports = tmp_path / 'reports'
    reports.mkdir()
    module.PORTFOLIO_DIR, module.HOLDINGS_DIR, module.REPORTS_DIR = tmp_path, holdings, reports
    monkeypatch.setattr(module, 'load_config', lambda: {})
    calls = []
    monkeypatch.setattr(module, 'run_snapshot', lambda day: calls.append('snapshot') or True)
    def report(day):
        calls.append('report')
        target = reports / ('portfolio-' + day.replace('-', '') + '.md')
        target.write_text('Synthetic test report')
        return target
    monkeypatch.setattr(module, 'run_report', report)
    return module, calls


def test_report_resume_does_not_repeat_completed_push(tmp_path, monkeypatch):
    module, calls = pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'send_feishu_report', lambda *args: calls.append('push') or True)
    assert module.run_pipeline('2026-01-02')
    assert module.run_pipeline('2026-01-02', resume=True)
    assert calls == ['snapshot', 'report', 'push']


def test_uncertain_delivery_is_not_repeated_and_changed_holdings_block_resume(tmp_path, monkeypatch):
    module, calls = pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'send_feishu_report', lambda *args: calls.append('push') or False)
    assert not module.run_pipeline('2026-01-02')
    assert not module.run_pipeline('2026-01-02', resume=True)
    assert calls == ['snapshot', 'report', 'push']
    (module.HOLDINGS_DIR / '2026-01-02.json').write_text('{"groups":{"Changed":{}}}')
    assert not module.run_pipeline('2026-01-02', resume=True)
    assert calls == ['snapshot', 'report', 'push']
