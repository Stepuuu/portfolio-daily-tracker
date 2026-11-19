"""A plan's alert rules follow its status and numbers, atomically and without duplicates."""
import pytest

from core import desk_store, plans
from core.alerts import rules
from tests.plan_support import CN, OTHER, active, payload, pin


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    pin(monkeypatch)
    return tmp_path


def rules_of(plan):
    """``{kind: (threshold, enabled, account)}`` of the plan's own rules; asserts one rule per kind."""
    items = rules.list_rules(source=f'plan:{plan["id"]}')
    kinds = [item['kind'] for item in items]
    assert len(kinds) == len(set(kinds)), f'duplicate rules: {kinds}'
    return {item['kind']: (item['threshold'], item['enabled'], item['account']) for item in items}


def test_activating_creates_stop_target_and_weight_rules():
    plan = plans.create(payload(account='Main', entry_low='10', stop_price='9.50', target_price='12',
                                planned_weight_pct='15'))
    assert rules.list_rules() == [], 'a draft has no alerts'
    plans.activate(plan['id'])
    assert rules_of(plan) == {'stop_loss': ('9.5', True, 'Main'), 'target_price': ('12', True, 'Main'),
                              'position_weight_max': ('15', True, 'Main')}
    [stop] = [item for item in rules.list_rules() if item['kind'] == 'stop_loss']
    assert stop['ticker'] == CN and stop['source'] == f'plan:{plan["id"]}' and stop['note'] == f'交易计划 #{plan["id"]}'
    assert stop['state']['armed'] is True


def test_only_the_numbers_a_plan_has_become_rules():
    by_quantity = active(stop_price='9', planned_quantity='1000')
    assert rules_of(by_quantity) == {'stop_loss': ('9', True, None)}, 'no weight rule for a quantity plan'
    bare = active(OTHER)
    assert rules_of(bare) == {}


def test_editing_an_active_plan_moves_its_rules():
    plan = active(stop_price='9', target_price='12', planned_weight_pct='10')
    ids = {item['kind']: item['id'] for item in rules.list_rules()}
    plans.update(plan['id'], {'stop_price': '9.2', 'target_price': '13', 'planned_weight_pct': '12.5'})
    assert rules_of(plan) == {'stop_loss': ('9.2', True, None), 'target_price': ('13', True, None),
                              'position_weight_max': ('12.5', True, None)}
    assert {item['kind']: item['id'] for item in rules.list_rules()} == ids, 'the same rules were updated'
    # Clearing a number disables its rule; setting it again re-enables the same rule.
    plans.update(plan['id'], {'target_price': None, 'planned_weight_pct': None, 'planned_quantity': '100'})
    assert rules_of(plan) == {'stop_loss': ('9.2', True, None), 'target_price': ('13', False, None),
                              'position_weight_max': ('12.5', False, None)}
    plans.update(plan['id'], {'target_price': '14'})
    assert rules_of(plan)['target_price'] == ('14', True, None)
    assert len(rules.list_rules()) == 3


def test_editing_a_draft_touches_no_rules():
    plan = plans.create(payload(stop_price='9'))
    plans.update(plan['id'], {'stop_price': '8'})
    assert rules.list_rules() == []
    plans.activate(plan['id'])
    assert rules_of(plan) == {'stop_loss': ('8', True, None)}


@pytest.mark.parametrize('end', [lambda plan_id: plans.close(plan_id, '已离场'), plans.cancel])
def test_closing_or_cancelling_disables_the_rules_and_keeps_them(end):
    plan = active(stop_price='9', target_price='12', planned_weight_pct='10')
    other = active(OTHER, stop_price='5')
    manual = rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '8'})
    end(plan['id'])
    assert rules_of(plan) == {'stop_loss': ('9', False, None), 'target_price': ('12', False, None),
                              'position_weight_max': ('10', False, None)}
    assert rules_of(other) == {'stop_loss': ('5', True, None)}, 'another plan keeps its rules'
    assert rules.get(manual['id'])['enabled'] is True, 'a rule the user made is not touched'


def test_activating_twice_creates_no_duplicates_and_repairs_missing_rules():
    plan = active(stop_price='9', target_price='12')
    assert plans.activate(plan['id'])['status'] == 'active'
    assert rules_of(plan) == {'stop_loss': ('9', True, None), 'target_price': ('12', True, None)}
    target = next(item for item in rules.list_rules() if item['kind'] == 'target_price')
    rules.delete(target['id'])                              # removed by hand on the alerts page
    stop = next(item for item in rules.list_rules() if item['kind'] == 'stop_loss')
    rules.update(stop['id'], {'enabled': False})            # or switched off
    assert plans.alert_notes(plans.get(plan['id'])) == ['止损提醒缺失或已停用。再次点击“启用”可以修复。',
                                                         '目标价提醒缺失或已停用。再次点击“启用”可以修复。']
    plans.activate(plan['id'])
    assert rules_of(plan) == {'stop_loss': ('9', True, None), 'target_price': ('12', True, None)}
    assert plans.alert_notes(plans.get(plan['id'])) == []


def test_an_identical_rule_of_the_user_is_not_copied():
    manual = rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '9'})
    plan = active(stop_price='9.0', target_price='12')
    assert rules_of(plan) == {'target_price': ('12', True, None)}
    [note] = plans.alert_notes(plans.get(plan['id']))
    assert f'#{manual["id"]}' in note and '已启用' in note
    # Moving the stop away from the user's rule gives the plan its own rule.
    plans.update(plan['id'], {'stop_price': '8.5'})
    assert rules_of(plan)['stop_loss'] == ('8.5', True, None)
    # Moving it back onto the user's rule switches the plan's own copy off again.
    plans.update(plan['id'], {'stop_price': '9'})
    assert rules_of(plan)['stop_loss'] == ('8.5', False, None)
    assert rules.get(manual['id'])['enabled'] is True


@pytest.mark.parametrize('end', [lambda plan_id: plans.close(plan_id, '已离场'), plans.cancel])
def test_the_next_plan_at_the_same_prices_gets_working_alerts(end):
    first = active(account='Main', stop_price='9', target_price='12')
    old = {item['kind']: item['id'] for item in rules.list_rules()}
    end(first['id'])
    second = active(account='Main', stop_price='9', target_price='12.5')
    assert rules_of(second) == {'stop_loss': ('9', True, 'Main'), 'target_price': ('12.5', True, 'Main')}
    assert plans.alert_notes(plans.get(second['id'])) == []
    # The ended plan's rule at the same stop gave way; the one at another price stays as history.
    assert rules_of(first) == {'target_price': ('12', False, 'Main')}
    assert old['stop_loss'] not in [item['id'] for item in rules.list_rules()]


def test_a_rule_the_user_switched_off_is_not_deleted():
    manual = rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '9'})
    rules.update(manual['id'], {'enabled': False})
    plan = active(stop_price='9')
    assert rules_of(plan) == {} and rules.get(manual['id'])['enabled'] is False
    [note] = plans.alert_notes(plans.get(plan['id']))
    assert f'#{manual["id"]}' in note and '已停用，目前不会提醒' in note


def test_a_failure_while_creating_rules_leaves_the_plan_a_draft_with_no_rules(monkeypatch):
    plan = plans.create(payload(stop_price='9', target_price='12'))
    real = rules.create
    calls = []

    def failing(rule, source='manual'):
        calls.append(rule['kind'])
        if len(calls) == 2:
            raise RuntimeError('simulated crash after the first rule')
        return real(rule, source=source)

    monkeypatch.setattr(rules, 'create', failing)
    with pytest.raises(RuntimeError):
        plans.activate(plan['id'])
    assert calls == ['stop_loss', 'target_price']
    assert plans.get(plan['id'])['status'] == 'draft' and rules.list_rules() == [], 'nothing was committed'
    monkeypatch.setattr(rules, 'create', real)
    plans.activate(plan['id'])
    assert rules_of(plan) == {'stop_loss': ('9', True, None), 'target_price': ('12', True, None)}


def test_a_refused_rule_refuses_the_activation(monkeypatch):
    plan = plans.create(payload(stop_price='9'))
    monkeypatch.setattr(rules, 'MAX_RULES', 0)
    with pytest.raises(plans.PlanError, match='未能为计划设置提醒.*已达上限'):
        plans.activate(plan['id'])
    assert plans.get(plan['id'])['status'] == 'draft' and rules.list_rules() == []


def test_a_failed_rule_update_undoes_the_edit(monkeypatch):
    plan = active(stop_price='9')
    monkeypatch.setattr(rules, 'update', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):
        plans.update(plan['id'], {'stop_price': '8'})
    assert plans.get(plan['id'])['stop_price'] == '9' and rules_of(plan) == {'stop_loss': ('9', True, None)}


def test_a_failed_disable_keeps_the_plan_active(monkeypatch):
    plan = active(stop_price='9')
    monkeypatch.setattr(rules, 'disable_by_source', lambda source: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):
        plans.close(plan['id'], '已离场')
    assert plans.get(plan['id'])['status'] == 'active' and rules_of(plan) == {'stop_loss': ('9', True, None)}


def test_the_status_and_rules_survive_a_restart(desk, monkeypatch):
    plan = active(stop_price='9', target_price='12')
    # A new process: no cached initialisation, fresh connections, same file.
    monkeypatch.setattr(desk_store, '_initialised', {})
    assert plans.get(plan['id'])['status'] == 'active'
    assert rules_of(plan) == {'stop_loss': ('9', True, None), 'target_price': ('12', True, None)}
    plans.activate(plan['id'])
    plans.close(plan['id'], '已离场')
    monkeypatch.setattr(desk_store, '_initialised', {})
    plans.close(plan['id'], '已离场')
    assert rules_of(plan) == {'stop_loss': ('9', False, None), 'target_price': ('12', False, None)}
