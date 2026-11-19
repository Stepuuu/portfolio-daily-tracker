"""In the read-only demo nobody checks the alert rules, and the rule view says so.

The demo never starts the alert monitor (``core.alerts.monitor``), so an enabled rule there is not being
checked: its ``market`` block says ``checked_now: false`` with the skip code ``demo`` and the fixed reason
演示模式不检查提醒 (the page shows a skipped rule as 暂停检查 with the backend's reason), on the alerts page,
in a plan's alerts and in what the assistant's ``list_alert_rules`` reads. The numbers are still shown.
"""
import asyncio
import sys

import pytest

from core.alerts import view
from tests.test_demo_seed import client, demo  # noqa: F401  (fixtures)

needs_backend = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
pytestmark = needs_backend


def not_checked(market):
    return (market['checked_now'], market['skip_code'], market['skip_reason']) == (False, 'demo', view.DEMO_REASON)


def test_every_rule_on_the_alerts_page_says_the_demo_does_not_check_it(demo, client):
    assert view.DEMO_REASON == '演示模式不检查提醒'
    rules = client.get('/api/alerts/rules').json()['items']
    enabled = [rule for rule in rules if rule['enabled']]
    assert enabled and all(not_checked(rule['market']) for rule in enabled)
    assert all(rule['market']['price'] is not None for rule in enabled), 'the numbers are still shown'
    assert client.get('/api/alerts/rules/3').json()['market']['skip_code'] == 'demo'


def test_a_plans_alerts_and_the_assistant_say_the_same(demo, client):
    active = client.get('/api/plans/2').json()
    assert active['alerts'] and all(not_checked(rule['market']) for rule in active['alerts'] if rule.get('market'))
    from core.agent_tools import alert_tools
    [listing, _propose] = alert_tools.alert_tools(None)
    rules = asyncio.run(listing.function())['rules']
    assert rules and all(rule['market']['skip_code'] == 'demo' for rule in rules if rule['enabled'])


def test_outside_the_demo_the_view_does_not_claim_it(tmp_path, monkeypatch):
    from core.market.runtime import set_market_runtime
    from tests.fakes import FakeClock, ScriptedSource, fake_runtime, quote
    from core.alerts import rules
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    clock = FakeClock('2026-11-17 10:00')
    set_market_runtime(fake_runtime(clock, ScriptedSource(
        {'SHA:600001': quote('SHA:600001', '10', prev_close='9.9', at=clock.now())})))
    try:
        rule = rules.create({'ticker': 'SHA:600001', 'kind': 'stop_loss', 'threshold': '9'})
        [item] = view.with_market([rule], fake_runtime(clock, ScriptedSource(
            {'SHA:600001': quote('SHA:600001', '10', prev_close='9.9', at=clock.now())})), None)
    finally:
        set_market_runtime(None)
    assert item['market']['skip_code'] != 'demo'
