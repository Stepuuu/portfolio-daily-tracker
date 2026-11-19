"""Every number of a plan is stored exactly as entered, or refused: never rounded, underflowed or expanded."""
import pytest

from core import plans
from tests.plan_support import payload, pin

NUMBER_FIELDS = ('entry_low', 'entry_high', 'stop_price', 'target_price', 'planned_weight_pct', 'planned_quantity')
PRICE_FIELDS = ('entry_low', 'entry_high', 'stop_price', 'target_price')


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    pin(monkeypatch)
    return tmp_path


def refused(**fields):
    with pytest.raises(plans.PlanError) as caught:
        plans.create(payload(**fields))
    assert plans.list() == []
    return str(caught.value)


def test_an_underflowing_quantity_is_refused_not_stored_as_zero():
    assert '科学计数法' in refused(planned_quantity='1e-999999999')


@pytest.mark.parametrize('field', NUMBER_FIELDS)
@pytest.mark.parametrize('value, words', [
    ('1e-999999999', '科学计数法'),
    ('1e2', '科学计数法'),
    ('2.5E-1', '科学计数法'),
    ('1e999999999', '科学计数法'),
    ('10.0000000000000000000000000001', '最多保留 8 位小数'),   # Decimal's context would round it to 10
    ('0.000000001', '最多保留 8 位小数'),
    ('１０', '必须是数字'),                                      # full-width digits
    ('١٠', '必须是数字'),                                        # Arabic-Indic digits
    ('1_0', '必须是数字'),
    ('Infinity', '必须是数字'),
    (' ', None),                                                 # empty: the field is not set
])
def test_numbers_that_are_not_plain_exact_decimals_are_refused(field, value, words):
    if words is None:
        assert plans.validate(payload(**{field: value}))[field] is None
        return
    message = refused(**{field: value})
    assert words in message and plans.NUMBER_LABELS[field] in message


@pytest.mark.parametrize('field', PRICE_FIELDS)
def test_prices_stop_where_the_quote_service_stops(field):
    assert plans.validate(payload(**{field: '100000000'}))[field] == '100000000'
    assert '超出' in refused(**{field: '100000000.01'})
    assert '超出' in refused(**{field: 10 ** 9})


def test_quantities_and_weights_have_their_own_bounds():
    assert plans.validate(payload(planned_quantity='1000000000000'))['planned_quantity'] == '1000000000000'
    assert '超出' in refused(planned_quantity='1000000000000.5')
    assert '超出' in refused(planned_quantity=10 ** 400)
    assert '不超过 100' in refused(planned_weight_pct='100.00000001')


def test_numbers_are_stored_exactly_without_trailing_zeros():
    plan = plans.validate(payload(entry_low='+9.50000000', entry_high=10.25, stop_price=9, target_price='12.',
                                  planned_quantity='.5'))
    assert (plan['entry_low'], plan['entry_high'], plan['stop_price'], plan['target_price'],
            plan['planned_quantity']) == ('9.5', '10.25', '9', '12', '0.5')
    # A JSON number arrives as a float; its shortest form decides, also when Python prints it with an exponent.
    assert plans.validate(payload(planned_quantity=0.00001))['planned_quantity'] == '0.00001'
    assert '最多保留 8 位小数' in refused(planned_quantity=1e-9)
    assert '必须是数字' in refused(planned_quantity=float('nan'))


@pytest.mark.parametrize('day', ['２０２６-１２-０１', '٢٠٢٦-١٢-٠١', '2026-12-1'])
def test_a_review_date_in_other_digits_is_refused(day):
    assert '复查日期' in refused(review_on=day)
