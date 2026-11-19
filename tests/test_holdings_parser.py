"""Natural-language holdings updates: every documented phrasing, and the
phrasings that used to be recorded wrongly. All data here is fictional."""
import copy

import pytest

import portfolio_daily_update as daily


def holdings(first="进攻", second="稳健", duplicate=False):
    data = {"date": "2026-01-05", "groups": {
        first: {"cost_basis": 500000, "fund": 0, "cash": 50000, "positions": [
            {"name": "药明康德", "ticker": "SHA:603259", "quantity": 4000, "cost_price": 50.0},
            {"name": "三花智控", "ticker": "SHE:002050", "quantity": 1000, "cost_price": 25.0},
            {"name": "腾讯控股", "ticker": "HKG:0700", "quantity": 800, "cost_price": 350.0},
            {"name": "苹果", "ticker": "NASDAQ:AAPL", "quantity": 10, "cost_price": 150.0},
            {"name": "加科思", "ticker": "HKG:1167", "quantity": 3000, "cost_price": 5.0},
        ]},
        second: {"cost_basis": 800000, "fund": 160000, "cash": 100000, "positions": [
            {"name": "沪深300ETF", "ticker": "SHA:510300", "quantity": 5000, "cost_price": 4.0},
            {"name": "华夏基金", "ticker": "SHA:510000", "quantity": 2000, "cost_price": 1.5},
        ]},
    }}
    if duplicate:
        data["groups"][second]["positions"].append(
            {"name": "药明康德", "ticker": "SHA:603259", "quantity": 2000, "cost_price": 60.0})
    return data


def apply(text, **kwargs):
    data = holdings(**kwargs)
    changes = daily.parse_and_apply_changes(data, text)
    return data, changes


def position(data, name, group="进攻"):
    matches = [p for p in data["groups"][group]["positions"] if p["name"] == name]
    return matches[0] if matches else None


def assert_rejected(text, **kwargs):
    before = holdings(**kwargs)
    data = copy.deepcopy(before)
    changes = daily.parse_and_apply_changes(data, text)
    assert any(change["action"] == "unknown" for change in changes), changes
    assert data == before, "a rejected update must not change holdings"


# ── Documented phrasings (README, docs, docstring) ──

@pytest.mark.parametrize("text", ["持仓未变化", "不变", "no change", "今天持仓没变", "其他不变"])
def test_no_change_statements_change_nothing(text):
    data, changes = apply(text)
    assert changes == []
    assert data == holdings()


@pytest.mark.parametrize("text,group,expected", [
    ("现金变为5000", "进攻", 5000),
    ("进攻现金变为-5万", "进攻", -50000),
    ("进攻现金-50万", "进攻", -500000),
    ("稳健账户现金3300", "稳健", 3300),
    ("稳健组现金改为12万", "稳健", 120000),
    ("现金变为1,000,000", "进攻", 1000000),
])
def test_cash_is_set(text, group, expected):
    data, changes = apply(text)
    assert changes[0]["action"] == "set_cash"
    assert data["groups"][group]["cash"] == expected


@pytest.mark.parametrize("text,group,expected", [
    ("基金变为16万", "进攻", 160000),
    ("稳健基金155900", "稳健", 155900),
    ("基金变为8万", "进攻", 80000),
])
def test_fund_is_set(text, group, expected):
    data, _ = apply(text)
    assert data["groups"][group]["fund"] == expected


@pytest.mark.parametrize("text,group,expected", [
    ("成本调整为58.5万", "进攻", 585000),
    ("进攻成本585000", "进攻", 585000),
    ("进攻成本调整为620000", "进攻", 620000),
    ("成本增加4万", "进攻", 540000),
    ("稳健账户成本减少2万", "稳健", 780000),
])
def test_account_cost_basis(text, group, expected):
    data, _ = apply(text)
    assert data["groups"][group]["cost_basis"] == expected


@pytest.mark.parametrize("text", [
    "卖了500股药明康德", "药明康德减500", "卖出药明康德500股", "卖出500股药明康德",
    "药明康德卖了500股", "减持500股药明康德", "今天卖了500股药明康德", "卖了500股药明",
])
def test_sell_reduces_the_named_position_by_the_full_amount(text):
    data, changes = apply(text)
    assert position(data, "药明康德")["quantity"] == 3500
    assert len(changes) == 1
    untouched = {p["name"]: p["quantity"] for g in data["groups"].values() for p in g["positions"]}
    assert untouched["三花智控"] == 1000 and untouched["加科思"] == 3000


@pytest.mark.parametrize("text", [
    "买了1000股三花智控", "三花智控加1000", "买入三花智控1000股", "加仓1000股三花智控", "三花智控买了1000股",
])
def test_buy_increases_the_named_position_by_the_full_amount(text):
    data, _ = apply(text)
    assert position(data, "三花智控")["quantity"] == 2000
    assert position(data, "药明康德")["quantity"] == 4000


@pytest.mark.parametrize("text", ["腾讯控股 600股", "腾讯控股600股", "腾讯控股数量600", "腾讯控股股数:600"])
def test_quantity_is_set(text):
    data, changes = apply(text)
    assert changes[0]["action"] == "set_quantity"
    assert position(data, "腾讯控股")["quantity"] == 600


@pytest.mark.parametrize("text", ["清仓药明康德", "清仓 药明康德", "删除 药明康德", "药明康德清了", "药明康德全卖了"])
def test_position_is_removed(text):
    data, changes = apply(text)
    assert changes[0]["action"] == "remove_position"
    assert position(data, "药明康德") is None
    assert len(data["groups"]["进攻"]["positions"]) == 4


def test_selling_everything_removes_the_position():
    data, _ = apply("卖了1000股三花智控")
    assert position(data, "三花智控") is None


def test_new_position_with_explicit_fields():
    data, _ = apply("新增 比亚迪 ticker:SHE:002594 数量:100 成本:250 组:稳健")
    assert position(data, "比亚迪", "稳健") == {
        "name": "比亚迪", "ticker": "SHE:002594", "quantity": 100, "cost_price": 250.0}


@pytest.mark.parametrize("text,cost", [
    ("买了500股万润新能 代码是SHA:688275", 0.0),
    ("买了500股万润新能 代码是SHA:688275 成本110.2", 110.2),
    ("买了500股万润新能 代码是SHA:688275 成本:110.2", 110.2),
])
def test_buying_an_unheld_stock_with_a_ticker_adds_it_with_a_clean_name(text, cost):
    data, _ = apply(text)
    assert position(data, "万润新能") == {
        "name": "万润新能", "ticker": "SHA:688275", "quantity": 500, "cost_price": cost}


def test_buying_an_unheld_stock_into_a_named_account():
    for text in ["买了100股特斯拉 代码是NASDAQ:TSLA 成本:250.5 组:稳健", "稳健买了100股特斯拉 代码是NASDAQ:TSLA 成本:250.5"]:
        data, _ = apply(text)
        assert position(data, "特斯拉", "稳健") == {
            "name": "特斯拉", "ticker": "NASDAQ:TSLA", "quantity": 100, "cost_price": 250.5}
        assert position(data, "特斯拉", "进攻") is None
    assert_rejected("买了100股特斯拉 代码是NASDAQ:TSLA 组:不存在")


def test_several_clauses_are_all_applied():
    data, changes = apply("卖了500股药明康德，进攻现金变为-5万；其他不变")
    assert [c["action"] for c in changes] == ["set_quantity", "set_cash"]
    assert position(data, "药明康德")["quantity"] == 3500
    assert data["groups"]["进攻"]["cash"] == -50000


@pytest.mark.parametrize("text,group,expected", [("进攻现金-44.273", "进攻", -442730), ("稳健现金15.635", "稳健", 156350)])
def test_documented_wan_shorthand_for_cny_amounts_is_kept(text, group, expected):
    data, _ = apply(text)
    assert data["groups"][group]["cash"] == expected


# ── Phrasings that used to be recorded wrongly ──

def test_name_then_verb_changes_the_right_stock_by_the_right_amount():
    data, _ = apply("三花智控减500")
    assert position(data, "三花智控")["quantity"] == 500
    assert position(data, "药明康德")["quantity"] == 4000
    data, _ = apply("沪深300ETF加1000")
    assert position(data, "沪深300ETF", "稳健")["quantity"] == 6000
    assert position(data, "药明康德")["quantity"] == 4000


@pytest.mark.parametrize("text,group,field,expected", [
    ("稳健现金减少5000", "稳健", "cash", 95000),
    ("稳健组现金增加5万", "稳健", "cash", 150000),
    ("现金增加2万", "进攻", "cash", 70000),
    ("稳健基金减少1万", "稳健", "fund", 150000),
])
def test_increase_and_decrease_are_deltas_not_new_balances(text, group, field, expected):
    data, _ = apply(text)
    assert data["groups"][group][field] == expected


def test_paying_with_cash_is_a_purchase_not_a_cash_balance():
    data, _ = apply("用现金买了500股药明康德")
    assert position(data, "药明康德")["quantity"] == 4500
    assert data["groups"]["进攻"]["cash"] == 50000


def test_per_share_cost_in_a_purchase_never_changes_the_account_cost_basis():
    data, _ = apply("买了1000股三花智控 成本:35")
    assert data["groups"]["进攻"]["cost_basis"] == 500000
    assert position(data, "三花智控")["quantity"] == 2000
    assert position(data, "三花智控")["cost_price"] == 30.0


def test_average_price_in_the_next_clause_belongs_to_the_purchase():
    data, _ = apply("加了200股腾讯，均价380")
    assert position(data, "腾讯控股")["quantity"] == 1000
    assert position(data, "腾讯控股")["cost_price"] == 356.0


def test_per_share_cost_of_a_position_is_not_the_account_cost_basis():
    assert_rejected("药明康德成本价改为55.2")


@pytest.mark.parametrize("text,expected", [("现金变为3万5", 35000), ("现金5千", 5000), ("现金变为2.5万元", 25000)])
def test_chinese_units(text, expected):
    data, _ = apply(text)
    assert data["groups"]["进攻"]["cash"] == expected


def test_foreign_cash_is_never_scaled_by_the_wan_shorthand():
    data, changes = apply("美元现金变为520.75")
    assert changes[0]["currency"] == "USD"
    assert data["groups"]["进攻"]["cash_balances"] == {"CNY": 50000, "USD": 520.75}
    data, _ = apply("稳健港币现金增加300.5")
    assert data["groups"]["稳健"]["cash_balances"] == {"CNY": 100000, "HKD": 300.5}


def test_account_can_be_named_by_a_unique_part_of_its_name():
    data, _ = apply("进攻现金变为5万", first="进攻账户", second="稳健账户")
    assert data["groups"]["进攻账户"]["cash"] == 50000
    assert data["groups"]["稳健账户"]["cash"] == 100000
    data, _ = apply("稳健现金变为5万", first="进攻账户", second="稳健账户")
    assert data["groups"]["稳健账户"]["cash"] == 50000


@pytest.mark.parametrize("text", ["激进现金变为5万", "激进账户现金变为5万", "激进基金变为5万", "激进成本增加4万"])
def test_an_unknown_account_is_rejected_instead_of_using_the_first_account(text):
    assert_rejected(text)


def test_a_stock_held_in_two_accounts_needs_the_account():
    assert_rejected("卖了500股药明康德", duplicate=True)
    assert_rejected("药明康德减500", duplicate=True)
    for text in ["稳健账户卖了500股药明康德", "稳健卖了500股药明康德", "稳健账户药明康德减500"]:
        data, _ = apply(text, duplicate=True)
        assert position(data, "药明康德", "稳健")["quantity"] == 1500
        assert position(data, "药明康德", "进攻")["quantity"] == 4000


@pytest.mark.parametrize("text", [
    "买了500股SHA:688275",          # no name, ticker not held
    "卖了500股贵州茅台",            # not held
    "茅台减500",
    "卖了0股苹果",
    "不卖了500股药明康德",
    "没卖500股药明康德",
    "卖了500股",
    "药明康德",
    "现金",
    "现金变为五千",
    "现金+5万",                     # set to, or add?
    "药明康德卖500买300",
    "买了500股药明康德和三花智控",
    "卖出沪深300",                  # 300 is part of the name, not a quantity
    "卖出全部药明康德",
])
def test_unresolvable_clauses_are_rejected(text):
    assert_rejected(text)


def test_a_ticker_alone_identifies_a_held_position():
    data, _ = apply("买了500股SHA:603259")
    assert position(data, "药明康德")["quantity"] == 4500
    data, _ = apply("卖了5股AAPL")
    assert position(data, "苹果")["quantity"] == 5


def test_fractional_shares_are_not_truncated():
    data, _ = apply("卖了0.5股苹果")
    assert position(data, "苹果")["quantity"] == 9.5
    with pytest.raises(ValueError):
        apply("卖了10.5股苹果")


def test_names_that_start_with_a_verb_character_are_not_split():
    data, changes = apply("加科思4000股")
    assert changes[0]["action"] == "set_quantity"
    assert position(data, "加科思")["quantity"] == 4000
    data, _ = apply("加科思加1000")
    assert position(data, "加科思")["quantity"] == 4000
    data, _ = apply("卖了1000股加科思")
    assert position(data, "加科思")["quantity"] == 2000


def test_a_position_whose_name_contains_a_keyword_is_still_a_position():
    data, _ = apply("华夏基金加1000")
    assert position(data, "华夏基金", "稳健")["quantity"] == 3000
    assert data["groups"]["稳健"]["fund"] == 160000
    data, _ = apply("华夏基金2500股")
    assert position(data, "华夏基金", "稳健")["quantity"] == 2500
    assert data["groups"]["进攻"]["fund"] == 0


def test_an_unrecognised_clause_leaves_the_whole_update_unapplied():
    assert_rejected("卖了500股药明康德，胡言乱语")
    assert_rejected("进攻现金变为5万；激进现金变为1万")


def test_new_position_in_a_missing_account_is_rejected_not_a_crash():
    assert_rejected("新增 比亚迪 ticker:SHE:002594 数量:100 成本:250 组:不存在")


# ── Cost inference for new positions ──

def infer(text, **kwargs):
    before = holdings(**kwargs)
    data = copy.deepcopy(before)
    changes = daily.parse_and_apply_changes(data, text)
    daily._infer_missing_cost_prices(before, changes)
    return data


def test_cost_is_inferred_from_the_cash_spent_on_one_cny_position():
    data = infer("买了500股万润新能 代码是SHA:688275，进攻现金变为1万")
    assert position(data, "万润新能")["cost_price"] == 80.0


@pytest.mark.parametrize("text", [
    "买了100股特斯拉 代码是NASDAQ:TSLA，进攻现金变为1万",                                  # not a CNY security
    "买了500股万润新能 代码是SHA:688275，进攻现金变为9万",                                  # cash went up
    "买了500股万润新能 代码是SHA:688275，买了100股比亚迪 代码是SHE:002594，进攻现金变为1万",  # two purchases
])
def test_cost_is_not_inferred_when_the_cash_change_cannot_be_attributed(text):
    data = infer(text)
    new = [p for p in data["groups"]["进攻"]["positions"] if p["name"] in {"特斯拉", "万润新能", "比亚迪"}]
    assert new and all(p["cost_price"] == 0 for p in new)


def test_cost_inference_uses_the_final_cash_balance():
    data = infer("买了500股万润新能 代码是SHA:688275，进攻现金减少1万，进攻现金减少1万")
    assert data["groups"]["进攻"]["cash"] == 30000
    assert position(data, "万润新能")["cost_price"] == 40.0
    data = infer("买了500股万润新能 代码是SHA:688275，进攻现金减少1万，进攻现金增加2万")
    assert position(data, "万润新能")["cost_price"] == 0


# ── Found in review of the rewrite ──

def test_a_ticker_can_stand_in_for_the_name_in_any_clause():
    data, _ = apply("SHA:603259 3500股")
    assert position(data, "药明康德")["quantity"] == 3500
    assert position(data, "沪深300ETF", "稳健")["quantity"] == 5000
    data, _ = apply("清仓 SHA:603259")
    assert position(data, "药明康德") is None
    data, _ = apply("SHA:603259 减500")
    assert position(data, "药明康德")["quantity"] == 3500


@pytest.mark.parametrize("text", [
    "3500股",                          # no name at all
    "买了123股 代码是SHA:688275",       # no name: must not become a stock called "1"
    "300加500",
    "苹果数量0.0000004",               # more precision than a holding can carry
    "卖了0.0000001股苹果",
])
def test_digits_are_never_split_between_a_name_and_a_quantity(text):
    assert_rejected(text)


def test_an_account_before_a_structured_addition_is_used():
    data, _ = apply("稳健新增 比亚迪 ticker:SHE:002594 数量:100 成本:250")
    assert position(data, "比亚迪", "稳健")["quantity"] == 100
    assert position(data, "比亚迪", "进攻") is None
    assert_rejected("稳健新增 比亚迪 ticker:SHE:002594 数量:100 成本:250 组:进攻")
    assert_rejected("激进新增 比亚迪 ticker:SHE:002594 数量:100 成本:250")


def test_an_account_before_a_new_stock_is_used_or_reported_as_ambiguous():
    data, _ = apply("稳健账户特斯拉加100 代码是NASDAQ:TSLA")
    assert position(data, "特斯拉", "稳健")["quantity"] == 100
    assert position(data, "稳健账户特斯拉", "进攻") is None
    assert_rejected("稳健特斯拉加100 代码是NASDAQ:TSLA")


def test_an_account_named_like_a_currency_is_still_an_account():
    data, changes = apply("美元账户现金变为5000", first="进攻账户", second="美元")
    assert "currency" not in changes[0]
    assert data["groups"]["美元"]["cash"] == 5000
    assert data["groups"]["进攻账户"]["cash"] == 50000
    data, changes = apply("进攻账户美元现金变为200", first="进攻账户", second="美元")
    assert changes[0]["currency"] == "USD"
    assert data["groups"]["进攻账户"]["cash_balances"] == {"CNY": 50000, "USD": 200}


@pytest.mark.parametrize("text", [
    "现金变为5000，基金不变但苹果加5",
    "茅台不变",                        # not held: say so instead of ignoring it
    "苹果加5不变",
])
def test_a_no_change_phrase_does_not_hide_other_content(text):
    assert_rejected(text)


@pytest.mark.parametrize("text", ["沪深300ETF不变", "稳健现金不变", "药明康德和三花智控不变", "腾讯控股数量一样", "和昨天一样"])
def test_no_change_about_a_known_subject_is_accepted(text):
    data, changes = apply(text)
    assert changes == [] and data == holdings()


def test_large_whole_quantities_are_exact():
    data, _ = apply("新增 比亚迪 ticker:SHE:002594 数量:9007199254740993 成本:1")
    assert position(data, "比亚迪")["quantity"] == 9007199254740993


def with_position(group, **pos):
    data = holdings()
    data["groups"][group]["positions"].append(pos)
    return data


def reject(data, text):
    before = copy.deepcopy(data)
    changes = daily.parse_and_apply_changes(data, text)
    assert any(change["action"] == "unknown" for change in changes), changes
    assert data == before


@pytest.mark.parametrize("text", ["清仓 SHA:603259 代码是NASDAQ:AAPL", "SHA:603259 NASDAQ:AAPL 5股"])
def test_two_tickers_in_one_clause_are_rejected(text):
    assert_rejected(text)


def test_two_held_names_that_fit_different_readings_are_ambiguous():
    data = with_position("稳健", name="科思", ticker="SHE:300856", quantity=100, cost_price=20.0)
    reject(data, "加科思4000股")          # 加科思 → 4000, or buy 4000 科思?
    changes = daily.parse_and_apply_changes(data, "卖了100股加科思")
    assert changes[0]["name"] == "加科思" and position(data, "加科思")["quantity"] == 2900


def test_a_currency_word_that_is_also_an_account_is_ambiguous():
    before = holdings(first="进攻账户", second="美元")
    data = copy.deepcopy(before)
    changes = daily.parse_and_apply_changes(data, "美元现金变为5000")
    assert changes[0]["action"] == "unknown" and data == before


@pytest.mark.parametrize("text", ["苹果数量9007199254740993.5", "苹果数量9007199254740993，苹果加0.5"])
def test_quantities_a_float_cannot_hold_are_rejected(text):
    assert_rejected(text)


def test_whole_quantities_written_with_a_decimal_point_stay_exact():
    data, _ = apply("苹果数量9007199254740993.0")
    assert position(data, "苹果")["quantity"] == 9007199254740993
    data, _ = apply("苹果加0.1，苹果加0.2")
    assert position(data, "苹果")["quantity"] == 10.3


@pytest.mark.parametrize("text", ["加科思4000股，均价6", "加科思4000股 均价6", "清仓药明康德 均价60"])
def test_a_price_is_not_silently_dropped_from_a_clause_that_cannot_use_it(text):
    assert_rejected(text)


def test_a_sale_price_is_reported_as_not_recorded():
    data, changes = apply("卖了5股加科思，均价6")
    assert position(data, "加科思")["quantity"] == 2995
    assert position(data, "加科思")["cost_price"] == 5.0
    assert "未记录" in changes[0]["description"]


@pytest.mark.parametrize("text", [
    "新增 比亚迪 ticker:SHE:002594 数量:100 成本:250 苹果清仓",
    "新增 比亚迪 ticker:SHE:002594 数量:100万 成本:250",
])
def test_a_structured_addition_must_be_understood_completely(text):
    assert_rejected(text)


def test_an_account_and_a_ticker_identify_a_position():
    for text in ["稳健账户 SHA:510300 减100", "稳健账户SHA:510300减100"]:
        data, _ = apply(text)
        assert position(data, "沪深300ETF", "稳健")["quantity"] == 4900


def test_a_ticker_identifies_the_holding_when_the_name_is_not_recognised():
    data, _ = apply("买了100股沪深300ETF联接 代码是SHA:510300")
    assert position(data, "沪深300ETF", "稳健")["quantity"] == 5100
    assert position(data, "沪深300ETF联接", "进攻") is None


@pytest.mark.parametrize("text", ["清仓 苹果 代码是SHA:603259", "买了100股苹果 代码是SHA:688275", "苹果 5股 SHA:603259"])
def test_a_name_and_a_ticker_that_disagree_are_rejected(text):
    assert_rejected(text)


def test_digits_between_a_name_and_a_quantity_prefer_the_held_name():
    data = with_position("稳健", name="沪深300", ticker="SHA:000300", quantity=5000, cost_price=4.0)
    daily.parse_and_apply_changes(data, "买入沪深3005股")
    assert position(data, "沪深300", "稳健")["quantity"] == 5005
    data = with_position("稳健", name="苹果2", ticker="NASDAQ:APL2", quantity=100, cost_price=1.0)
    reject(data, "苹果2500股")           # 苹果 → 2500, or 苹果2 → 500?
    data, _ = apply("苹果2500股")
    assert position(data, "苹果")["quantity"] == 2500


def test_words_that_look_like_a_price_inside_a_name_are_the_name():
    data = holdings()
    data["groups"]["进攻"]["positions"] += [
        {"name": "均价30科技", "ticker": "SHE:300001", "quantity": 100, "cost_price": 20.0},
        {"name": "科技", "ticker": "SHE:300002", "quantity": 100, "cost_price": 20.0},
    ]
    daily.parse_and_apply_changes(data, "买了5股均价30科技")
    assert position(data, "均价30科技")["quantity"] == 105
    assert position(data, "科技") == {"name": "科技", "ticker": "SHE:300002", "quantity": 100, "cost_price": 20.0}


def test_whole_share_arithmetic_is_exact_at_any_size():
    data = holdings()
    position(data, "苹果")["quantity"] = 1000000000000000000000000000001
    daily.parse_and_apply_changes(data, "苹果加1")
    assert position(data, "苹果")["quantity"] == 1000000000000000000000000000002


def test_a_price_for_a_holding_without_a_cost_is_reported_not_dropped():
    data = holdings()
    del position(data, "苹果")["cost_price"]
    changes = daily.parse_and_apply_changes(data, "买了5股苹果，均价200")
    assert position(data, "苹果")["quantity"] == 15
    assert "成本价未更新" in changes[0]["description"]


def test_an_account_whose_name_ends_in_a_currency_is_ambiguous():
    before = holdings(first="稳健", second="稳健美元")
    data = copy.deepcopy(before)
    changes = daily.parse_and_apply_changes(data, "稳健美元现金变为5000")
    assert changes[0]["action"] == "unknown" and data == before
    daily.parse_and_apply_changes(data, "稳健美元账户现金变为5000")
    assert data["groups"]["稳健美元"]["cash"] == 5000 and data["groups"]["稳健"]["cash"] == 50000


def test_tickers_with_a_dash_or_dot_are_read_whole():
    data = with_position("稳健", name="伯克希尔", ticker="NYSE:BRK-B", quantity=10, cost_price=400.0)
    daily.parse_and_apply_changes(data, "买了5股伯克希尔 代码是NYSE:BRK-B")
    assert position(data, "伯克希尔", "稳健")["quantity"] == 15
    assert len(data["groups"]["进攻"]["positions"]) == 5
    data, _ = apply("买了5股伯克希尔 代码是NYSE:BRK-B")
    assert position(data, "伯克希尔") == {"name": "伯克希尔", "ticker": "NYSE:BRK-B", "quantity": 5, "cost_price": 0.0}


def test_a_name_that_ends_like_a_price_is_ambiguous_with_a_priced_purchase():
    data = holdings()
    data["groups"]["进攻"]["positions"] += [
        {"name": "科技均价30", "ticker": "SHE:300001", "quantity": 100, "cost_price": 20.0},
        {"name": "科技", "ticker": "SHE:300002", "quantity": 100, "cost_price": 20.0},
    ]
    reject(data, "买了5股科技均价30")
    data = with_position("进攻", name="科技", ticker="SHE:300002", quantity=100, cost_price=20.0)
    daily.parse_and_apply_changes(data, "买了5股科技均价30")
    assert position(data, "科技")["quantity"] == 105
    assert position(data, "科技")["cost_price"] == 20.4762


def test_every_split_of_adjacent_digits_is_considered():
    data = with_position("稳健", name="沪深300", ticker="SHA:000300", quantity=5000, cost_price=4.0)
    daily.parse_and_apply_changes(data, "买入沪深3000.5股")
    assert position(data, "沪深300", "稳健")["quantity"] == 5000.5
    data = with_position("稳健", name="苹果2", ticker="NASDAQ:APL2", quantity=100, cost_price=1.0)
    reject(data, "苹果20.5股")            # 苹果 → 20.5, or 苹果2 → 0.5?


def test_a_split_never_turns_a_quantity_into_a_removal():
    data = holdings()
    position(data, "苹果")["name"] = "苹果2"
    daily.parse_and_apply_changes(data, "苹果2000股")
    assert position(data, "苹果2")["quantity"] == 2000


@pytest.mark.parametrize("text", [
    "买了5股苹果 同时清仓三花智控 代码是NASDAQ:AAPL",       # a second instruction in the same clause
    "买了5股比亚迪 同时清仓三花智控 代码是SHE:002594",
])
def test_a_ticker_does_not_rescue_a_clause_that_says_something_else(text):
    assert_rejected(text)


def test_a_named_account_is_never_overridden_by_a_ticker_held_elsewhere():
    data, _ = apply("稳健账户苹果加5 代码是NASDAQ:AAPL")
    assert position(data, "苹果", "进攻")["quantity"] == 10          # the other account is untouched
    assert position(data, "苹果", "稳健")["quantity"] == 5           # a new holding in the named account


def test_fractional_arithmetic_on_a_quantity_a_float_cannot_hold_is_rejected():
    data = holdings()
    position(data, "苹果")["quantity"] = 10 ** 201 + 1
    reject(data, "苹果加0.5")
    daily.parse_and_apply_changes(data, "苹果加1")
    assert position(data, "苹果")["quantity"] == 10 ** 201 + 2


def test_an_account_name_that_is_a_prefix_plus_a_currency_is_ambiguous():
    before = holdings(first="进攻", second="稳健美元")
    data = copy.deepcopy(before)
    changes = daily.parse_and_apply_changes(data, "稳健美元现金变为5000")
    assert changes[0]["action"] == "unknown" and data == before
