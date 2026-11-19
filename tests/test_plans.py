"""Trade plans: validation, one active plan per ticker and account, status changes and the maximum loss."""
import re
from decimal import Decimal

import pytest

from core import desk_store, plans
from tests.plan_support import CN, HK, OTHER, active, payload, pin

CHINESE = re.compile(r'[一-鿿]')


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    pin(monkeypatch)
    return tmp_path


# -- validation ---------------------------------------------------------------

def test_a_plan_is_normalised_and_saved_as_a_draft():
    plan = plans.create(payload(ticker=' sha:600001 ', account=' Main ', entry_low='9.50', entry_high=10.5,
                                stop_price='9', target_price='12.00', planned_quantity='1000', review_on='2026-12-01',
                                thesis='  示例理由  '))
    assert (plan['ticker'], plan['account'], plan['direction'], plan['thesis']) == (CN, 'Main', 'buy', '示例理由')
    assert (plan['entry_low'], plan['entry_high'], plan['stop_price'], plan['target_price']) == ('9.5', '10.5', '9', '12')
    assert (plan['planned_quantity'], plan['planned_weight_pct'], plan['review_on']) == ('1000', None, '2026-12-01')
    assert (plan['status'], plan['status_label'], plan['source'], plan['currency']) == ('draft', '草稿', 'manual', 'CNY')
    assert plan['activated_at'] is None and plan['ended_at'] is None and plan['close_reason'] is None
    assert plans.get(plan['id']) == plan and plans.list() == [plan]


def test_validate_accepts_its_own_output():
    first = plans.validate(payload(entry_low='9.50', stop_price=9, planned_weight_pct='10.0', account=''))
    assert plans.validate(first) == first
    assert first['account'] is None and first['planned_weight_pct'] == '10'


@pytest.mark.parametrize('fields, words', [
    ({'ticker': None}, '请填写代码'),
    ({'ticker': '600001'}, '交易所:代码'),
    ({'ticker': 'IDX:000300'}, '交易所:代码'),
    ({'direction': 'sell'}, '只支持做多'),
    ({'thesis': '  '}, '买入理由'),
    ({'invalidation': None}, '判断错了'),
    ({'thesis': 'x' * 4001}, '买入理由最多 4000'),
    ({'entry_low': '11', 'entry_high': '10'}, '下限 11 不能高于上限 10'),
    ({'entry_low': '10', 'stop_price': '10'}, '止损价必须低于入场价'),
    ({'entry_high': '10', 'stop_price': '10.5'}, '止损价必须低于入场价'),
    ({'entry_high': '10', 'target_price': '10'}, '目标价必须高于入场价'),
    ({'entry_low': '10', 'target_price': '9'}, '目标价必须高于入场价'),
    ({'stop_price': '12', 'target_price': '11'}, '必须低于目标价'),
    ({'stop_price': '0'}, '止损价必须大于 0'),
    ({'target_price': '-1'}, '目标价必须大于 0'),
    ({'stop_price': 'abc'}, '止损价必须是数字'),
    ({'stop_price': True}, '止损价必须是数字'),
    ({'stop_price': 'NaN'}, '止损价必须是数字'),
    ({'stop_price': '1.123456789'}, '最多保留 8 位小数'),
    ({'planned_weight_pct': '0'}, '计划仓位比例必须大于 0'),
    ({'planned_weight_pct': '100.01'}, '不超过 100'),
    ({'planned_quantity': '-5'}, '计划股数必须大于 0'),
    ({'planned_weight_pct': '10', 'planned_quantity': '100'}, '只填一项'),
    ({'review_on': '2026-02-30'}, '复查日期'),
    ({'account': 5}, '账户名称必须是文字'),
    ({'status': 'active'}, '计划状态不能直接修改'),
    ({'max_loss_cny': '100'}, '最大亏损由入场价'),
    ({'colour': 'red'}, '不支持的字段：colour'),
])
def test_invalid_plans_are_refused_in_chinese(fields, words):
    with pytest.raises(plans.PlanError) as caught:
        plans.create(payload(**fields))
    assert CHINESE.search(str(caught.value)) and words in str(caught.value)
    assert plans.list() == []


def test_a_plan_must_be_an_object_and_numbers_at_the_limits_pass():
    with pytest.raises(plans.PlanError, match='JSON 对象'):
        plans.validate(['SHA:600001'])
    edge = plans.validate(payload(planned_weight_pct='100', stop_price='0.00000001', entry_low='0.00000002'))
    assert (edge['planned_weight_pct'], edge['stop_price']) == ('100', '0.00000001')


# -- one active plan per ticker and account -----------------------------------

def test_a_second_active_plan_for_the_same_ticker_and_account_is_refused():
    first = active(account='Main')
    second = plans.create(payload(account='Main'))
    with pytest.raises(plans.PlanConflict) as caught:
        plans.activate(second['id'])
    message = str(caught.value)
    assert f'#{first["id"]}' in message and '只能有一个启用中的计划' in message and '账户“Main”' in message
    assert plans.get(second['id'])['status'] == 'draft'
    # Another ticker, or another account, is a different plan.
    assert active(OTHER, account='Main')['status'] == 'active'
    assert active(account='Other')['status'] == 'active'


def test_a_plan_for_every_account_and_one_for_a_single_account_cannot_both_be_active():
    everywhere = active()                                  # account null: every account
    one = plans.create(payload(account='Main'))
    with pytest.raises(plans.PlanConflict, match='全部账户'):
        plans.activate(one['id'])
    plans.cancel(everywhere['id'])
    assert plans.activate(one['id'])['status'] == 'active'
    again = plans.create(payload())
    with pytest.raises(plans.PlanConflict, match=f'#{one["id"]}'):
        plans.activate(again['id'])


def test_after_closing_the_first_plan_a_new_one_can_be_activated():
    first = active(account='Main')
    plans.close(first['id'], '已离场')
    assert active(account='Main')['status'] == 'active'


def test_the_database_refuses_a_second_active_plan_even_without_the_check():
    first = active(account='Main')
    second = plans.create(payload(account='Main'))
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with desk_store.transaction() as db:
            db.execute("UPDATE trade_plans SET status='active' WHERE id=?", (second['id'],))
    assert plans.get(first['id'])['status'] == 'active' and plans.get(second['id'])['status'] == 'draft'


# -- status changes -----------------------------------------------------------

def test_draft_active_closed(monkeypatch):
    plan = plans.create(payload())
    pin(monkeypatch, '2026-11-17T01:00:00+00:00')
    activated = plans.activate(plan['id'])
    assert (activated['status'], activated['status_label'], activated['activated_at']) == (
        'active', '启用中', '2026-11-17T01:00:00+00:00')
    assert plans.activate(plan['id']) == activated, 'activating again changes nothing'
    pin(monkeypatch, '2026-11-18T07:00:00+00:00')
    closed = plans.close(plan['id'], '  已到目标价卖出 ')
    assert (closed['status'], closed['close_reason'], closed['ended_at']) == (
        'closed', '已到目标价卖出', '2026-11-18T07:00:00+00:00')
    assert plans.close(plan['id'], '再次关闭') == closed, 'closing a closed plan changes nothing'


def test_cancel_a_draft_or_an_active_plan():
    draft = plans.cancel(plans.create(payload())['id'])
    assert (draft['status'], draft['close_reason'], draft['activated_at']) == ('cancelled', None, None)
    running = plans.cancel(active(OTHER)['id'], '不想做了')
    assert (running['status'], running['status_label'], running['close_reason']) == ('cancelled', '已取消', '不想做了')
    assert plans.cancel(running['id']) == running


@pytest.mark.parametrize('status, action, words', [
    ('draft', lambda plan_id: plans.close(plan_id, '原因'), '草稿计划还没有启用'),
    ('closed', plans.activate, '已关闭，不能再启用'),
    ('closed', plans.cancel, '已关闭，不能再取消'),
    ('cancelled', plans.activate, '已取消，不能再启用'),
    ('cancelled', lambda plan_id: plans.close(plan_id, '原因'), '已取消，不能再关闭'),
    ('closed', lambda plan_id: plans.update(plan_id, {'thesis': '新理由'}), '已关闭，不能再修改'),
    ('cancelled', lambda plan_id: plans.update(plan_id, {'stop_price': '9'}), '已取消，不能再修改'),
])
def test_transitions_that_are_not_allowed(status, action, words):
    plan = plans.create(payload())
    if status != 'draft':
        plans.activate(plan['id'])
        plans.close(plan['id'], '已离场') if status == 'closed' else plans.cancel(plan['id'])
    before = plans.get(plan['id'])
    with pytest.raises(plans.PlanConflict, match=words):
        action(plan['id'])
    assert plans.get(plan['id']) == before


def test_closing_needs_a_reason():
    plan = active()
    for reason in (None, '', '   ', 5):
        with pytest.raises(plans.PlanError, match='关闭原因'):
            plans.close(plan['id'], reason)
    with pytest.raises(plans.PlanError, match='关闭原因最多 500'):
        plans.close(plan['id'], 'x' * 501)
    assert plans.get(plan['id'])['status'] == 'active'


def test_editing_a_draft_and_an_active_plan():
    draft = plans.create(payload(stop_price='9'))
    edited = plans.update(draft['id'], {'ticker': OTHER, 'account': 'Main', 'stop_price': None, 'target_price': '12'})
    assert (edited['ticker'], edited['account'], edited['stop_price'], edited['target_price']) == (OTHER, 'Main', None, '12')
    running = plans.activate(edited['id'])
    assert plans.update(running['id'], {'thesis': '新理由', 'planned_quantity': '200'})['planned_quantity'] == '200'
    assert plans.update(running['id'], {'ticker': OTHER, 'account': 'Main'})['ticker'] == OTHER, 'unchanged values pass'
    for change in ({'ticker': CN}, {'account': 'Other'}, {'account': None}):
        with pytest.raises(plans.PlanConflict, match='不能修改代码、账户或方向'):
            plans.update(running['id'], change)
    with pytest.raises(plans.PlanError, match='止损价必须低于入场价'):
        plans.update(running['id'], {'entry_low': '10', 'stop_price': '11'})
    for change, words in (({}, '没有要修改的内容'), ([1], 'JSON 对象'), ({'status': 'closed'}, '计划状态')):
        with pytest.raises(plans.PlanError, match=words):
            plans.update(running['id'], change)
    assert plans.get(running['id'])['thesis'] == '新理由'


def test_an_active_plan_keeping_its_entry_can_move_its_stop_to_breakeven_and_into_profit():
    running = active(entry_low='58', entry_high='61', stop_price='54', target_price='66', planned_quantity='1200')
    even = plans.update(running['id'], {'stop_price': '61'})                  # breakeven at the top of the entry
    assert even['stop_price'] == '61'
    assert (even['max_loss']['amount'], even['max_loss']['amount_cny'], even['max_loss']['locked_gain']) == (
        '0.00', '0.00', '0.00')
    profit = plans.update(running['id'], {'stop_price': '63'})                # into profit
    loss = profit['max_loss']
    assert (loss['amount'], loss['amount_cny'], loss['locked_gain']) == ('0.00', '0.00', '2400.00')   # (63 - 61) x 1200
    assert loss['formula'] == ('止损价 63 不低于入场价上限 61：触及止损时不亏损，按计划股数 1200 锁定盈利 '
                               '(63 − 61) × 1200 = 2400.00 CNY。不含手续费、滑点和跳空低开。')
    assert [rule['threshold'] for rule in plans.alert_rules(running['id']) if rule['kind'] == 'stop_loss'] == ['63']
    # Still refused: a stop at or above the target, and a stop above an entry the same edit moves.
    for change, words in (({'stop_price': '66'}, '止损价 66 必须低于目标价 66'),
                          ({'stop_price': '64', 'entry_high': '63'}, '止损价必须低于入场价')):
        with pytest.raises(plans.PlanError, match=words):
            plans.update(running['id'], change)
    # A draft is a plan before buying: its stop stays below the entry.
    draft = plans.create(payload(OTHER, entry_low='10', stop_price='9'))
    with pytest.raises(plans.PlanError, match='止损价必须低于入场价'):
        plans.update(draft['id'], {'stop_price': '10'})


def test_unknown_plans_and_filters():
    with pytest.raises(plans.PlanNotFound, match='不存在'):
        plans.get(1)                                       # nothing stored yet
    plan = plans.create(payload())
    for missing in (plan['id'] + 1, 0, -1, True, '1'):
        with pytest.raises(plans.PlanNotFound):
            plans.get(missing)
    with pytest.raises(plans.PlanNotFound):
        plans.activate(plan['id'] + 1)
    second = active(OTHER)
    assert [item['id'] for item in plans.list()] == [second['id'], plan['id']], 'newest first'
    assert [item['id'] for item in plans.list(status='active')] == [second['id']]
    assert [item['id'] for item in plans.list(ticker='sha:600001')] == [plan['id']]
    assert plans.list(status='all') == plans.list()
    with pytest.raises(plans.PlanError, match='未知的计划状态'):
        plans.list(status='open')
    with pytest.raises(plans.PlanError, match='交易所:代码'):
        plans.list(ticker='600001')


def test_reading_without_a_store_creates_nothing(desk):
    assert plans.list() == [] and plans.in_use() is False
    with pytest.raises(plans.PlanNotFound):
        plans.get(1)
    assert list(desk.iterdir()) == []


# -- maximum loss -------------------------------------------------------------

def test_the_maximum_loss_is_entry_minus_stop_times_quantity():
    plan = plans.create(payload(entry_low='10', entry_high='10.5', stop_price='9.8', planned_quantity='1000'))
    assert plan['max_loss_cny'] == '700.00'
    assert plan['max_loss'] == {
        'amount': '700.00', 'currency': 'CNY', 'amount_cny': '700.00', 'fx_rate': None, 'fx_status': None,
        'fx_source': None, 'fx_quote_time': None, 'estimated': False, 'estimated_reason': None,
        'entry_basis': 'entry_high', 'locked_gain': None,
        'formula': '(入场价上限 10.5 − 止损价 9.8) × 计划股数 1000 = 700.00 CNY。不含手续费、滑点和跳空低开。',
        'reason': None}
    low_only = plans.update(plan['id'], {'entry_high': None, 'planned_quantity': '333.5'})
    assert low_only['max_loss']['entry_basis'] == 'entry_low'
    assert low_only['max_loss_cny'] == '66.70'             # (10 - 9.8) x 333.5
    assert low_only['max_loss']['formula'].startswith('(入场价下限 10 − 止损价 9.8) × 计划股数 333.5 = 66.70 CNY')


@pytest.mark.parametrize('fields, words', [
    ({'stop_price': '9', 'planned_quantity': '100'}, '需要入场价'),
    ({'entry_low': '10', 'planned_quantity': '100'}, '需要止损价'),
    ({'entry_low': '10', 'stop_price': '9'}, '需要计划股数'),
    ({'entry_low': '10', 'stop_price': '9', 'planned_weight_pct': '10'}, '按仓位比例填写时不计算'),
])
def test_without_entry_stop_and_quantity_there_is_no_maximum_loss(fields, words):
    loss = plans.create(payload(**fields))['max_loss']
    assert loss['amount'] is None and loss['amount_cny'] is None and loss['formula'] is None
    assert words in loss['reason']


def test_a_b_share_plan_is_in_the_currency_the_b_share_trades_in():
    # Its prices are compared with quotes in US dollars (Shanghai) or Hong Kong dollars (Shenzhen).
    shanghai = plans.create(payload('SHA:900901', entry_high='0.55', stop_price='0.45', planned_quantity='10000'))
    assert (shanghai['currency'], shanghai['max_loss']['currency'], shanghai['max_loss_cny']) == ('USD', 'USD', None)
    assert plans.max_loss(shanghai, Decimal('7'), 'live')['amount_cny'] == '7000.00'
    assert plans.create(payload('SHE:200596', entry_high='55', stop_price='50', planned_quantity='100'))['currency'] == 'HKD'


def test_a_foreign_plan_needs_a_rate_for_its_loss_in_cny():
    plan = plans.create(payload(HK, entry_high='400', stop_price='360', planned_quantity='100'))
    loss = plan['max_loss']
    assert (loss['amount'], loss['currency'], loss['amount_cny'], plan['max_loss_cny']) == ('4000.00', 'HKD', None, None)
    assert 'HKD' in loss['reason']
    converted = plans.max_loss(plan, Decimal('0.92'), 'live')
    assert (converted['amount_cny'], converted['fx_rate'], converted['fx_status']) == ('3680.00', '0.92', 'live')
    assert '= 4000.00 HKD；× 汇率 0.92 = 3680.00 CNY' in converted['formula']


def test_every_deviation_code_has_a_chinese_label_in_the_post_market_brief():
    from core.routines import postmarket
    assert set(postmarket.DEVIATION_LABELS) == set(plans.DEVIATION_CODES)


def test_the_loss_rounds_half_away_from_zero_like_the_rest_of_the_app():
    # fees, valuation and the frontend's formatting round half away from zero; so does the plan.
    assert plans.create(payload(entry_high='2.005', stop_price='1', planned_quantity='1'))['max_loss']['amount'] == '1.01'
    assert plans.create(payload(entry_high='2.015', stop_price='1', planned_quantity='1'))['max_loss']['amount'] == '1.02'
