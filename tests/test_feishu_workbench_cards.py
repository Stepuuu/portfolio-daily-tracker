"""Offline structural and interaction checks; no card is sent to Feishu."""
from decimal import Decimal
import json

import pytest

from feishu_workbench.cards import ACTIONS, CardContext, MAX_CARD_BYTES, build_card


@pytest.fixture
def context():
    return CardContext("session_example", 7, {action: "n_" + action.replace(".", "_") for action in ACTIONS})


@pytest.fixture
def models():
    return {
        "home": {"summary": "今天可以记录组合或检查研究结果。"},
        "daily_select": {"accounts": [{"label": "虚构账户", "value": "account_example"}],
                         "account": "account_example", "date": "2026-09-14", "operation": "no_change"},
        "daily_form": {"accounts": [{"label": "虚构账户", "value": "account_example"}],
                       "account": "account_example", "date": "2026-09-14", "operation": "buy",
                       "quantity": Decimal("0.1250"), "price": Decimal("12.3400"), "fee": Decimal("0.0100")},
        "daily_preview": {"summary": "虚构交易：买入 0.125 股", "changes": [
            "现金 USD 100.0000 → 98.4475", "持有数量 1.0000 → 1.1250", "手续费 USD 0.0100"]},
        "daily_receipt": {"summary": "虚构更新已保存", "reference": "receipt_example"},
        "assets": {"total": "CNY 12,345.67", "cash": "USD 98.4475", "change": "+0.50%",
                   "accounts": [{"label": "虚构账户", "value": "account_example",
                                 "cash_balances": {"USD": "98.4475", "CNY": "1000.00"}, "fund": "250.00"}],
                   "as_of": "2026-09-14 16:00", "positions": [
                       {"label": "示例资产", "value": "EXAMPLE", "quantity": "1.1250", "market_value": "USD 13.88"}]},
        "research_form": {"goal": "合成数据上的波动特征能否改善预测？", "mode": "manual",
                          "templates": [{"label": "波动预测", "value": "volatility"}],
                          "datasets": [{"label": "合成学习数据", "value": "dataset_example"}],
                          "connections": [{"label": "示例连接", "value": "connection_example"}],
                          "template_id": "volatility", "dataset_id": "dataset_example"},
        "research_list": {"runs": [{"label": "合成波动实验 · 已完成", "value": "run_example"}],
                          "run_id": "run_example"},
        "research_preview": {"goal": "合成学习实验", "template_id": "volatility", "dataset_id": "dataset_example",
                             "mode": "agent", "connection_id": "connection_example", "max_trials": "3"},
        "research_status": {"status": "running", "stage": "验证集反馈", "goal": "合成学习实验",
                            "completed": 1, "max_trials": 3, "can_cancel": True, "events": ["完成第一轮实验"]},
        "research_result": {"summary": "合成实验仅演示流程", "notice": "合成数据，不代表市场证据。",
                            "metrics": [{"label": "测试误差", "value": "0.12"}, {"label": "基线误差", "value": "0.14"},
                                        {"label": "样本数", "value": "50"}, {"label": "验证误差", "value": "0.13"}],
                            "equity": [{"date": "2026-01-02", "strategy": 1.0, "benchmark": 1.0},
                                       {"date": "2026-01-03", "strategy": 1.01, "benchmark": 1.02}],
                            "lessons": ["与简单基线比较"], "warnings": ["分数仓位不等于实际撮合"]},
        "reports": {"items": [{"title": "虚构日报", "date": "2026-09-14", "summary": "今日无变动"}]},
        "error": {"message": "预览已过期", "detail": "请重新预览当前记录。"},
    }


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def assert_card_structure(card, context):
    assert card["schema"] == "2.0"
    assert card["config"]["update_multi"] is True
    assert card["config"]["enable_forward"] is False
    roots = card["body"]["elements"]
    assert 2 <= len(roots) <= 5
    names = [node["name"] for node in walk(card) if node.get("tag") in {
        "form", "input", "date_picker", "select_static", "button"} and "name" in node]
    assert len(names) == len(set(names))
    root_ids = {id(node) for node in roots}
    submit_names = {"qrw_" + context.nonces[action] for action in (
        "daily.choose", "daily.preview", "research.preview", "research.select")}
    primary = []
    for node in walk(card):
        tag = node.get("tag")
        assert tag not in {"action", "iframe", "webview", "script"}
        if tag in {"form", "table"}:
            assert id(node) in root_ids
        if tag == "column_set":
            assert all(column.get("tag") == "column" for column in node["columns"])
        if tag == "table":
            assert 1 <= node.get("page_size", 5) <= 10
            column_names = {column["name"] for column in node["columns"]}
            assert all(set(row) == column_names for row in node["rows"])
        if tag == "form":
            descendants = list(walk(node["elements"]))
            assert not any(child.get("tag") in {"form", "table"} for child in descendants)
            submit_buttons = [child for child in descendants if child.get("tag") == "button"]
            assert submit_buttons
            assert all(button["form_action_type"] == "submit" and "behaviors" not in button for button in submit_buttons)
        if tag == "input":
            assert node["input_type"] in {"text", "multiline_text", "password"}
            assert 1 <= node["max_length"] <= 1000
        if tag == "button":
            assert "value" not in node and "url" not in node
            if node["type"] == "primary_filled":
                primary.append(node)
            if "form_action_type" in node:
                assert node["name"] in submit_names
            else:
                behavior, = node["behaviors"]
                assert behavior["type"] == "callback"
                value = behavior["value"]
                assert set(value) == {"qr_workbench", "action", "session_id", "revision", "nonce"}
                assert value["qr_workbench"] == 1
                assert value["session_id"] == context.session_id and value["revision"] == 7
                assert value["nonce"] == context.nonces[value["action"]]
    assert len(primary) == 1
    assert len(json.dumps(card, ensure_ascii=False).encode()) <= MAX_CARD_BYTES


@pytest.mark.parametrize("view", ["home", "daily_select", "daily_form", "daily_preview", "daily_receipt", "assets", "research_form",
                                  "research_list", "research_preview", "research_status", "research_result", "reports", "error"])
def test_all_views_follow_card_2_structure_and_callback_contract(view, models, context):
    card = build_card(view, models[view], context)
    assert_card_structure(card, context)
    assert json.loads(json.dumps(card, allow_nan=False)) == card


def test_daily_selection_submits_only_date_account_and_operation(models, context):
    card = build_card("daily_select", models["daily_select"], context)
    controls = {node["name"]: node for node in walk(card)
                if node.get("tag") in {"input", "date_picker", "select_static"}}
    assert set(controls) == {"date", "account", "operation"}
    assert all(control["required"] is True for control in controls.values())
    assert controls["operation"]["initial_option"] == "no_change"
    submit = next(node for node in walk(card) if node.get("form_action_type") == "submit")
    assert submit["name"] == "qrw_" + context.nonces["daily.choose"]
    assert "behaviors" not in submit
    assert not any(node.get("action") == "daily.confirm" for node in walk(card))
    empty = build_card("daily_select", {}, context)
    assert next(node for node in walk(empty) if node.get("form_action_type") == "submit")["disabled"] is True


@pytest.mark.parametrize("operation,fields", [
    ("buy", {"symbol", "quantity", "price", "currency", "fee"}),
    ("sell", {"symbol", "quantity", "price", "currency", "fee"}),
    ("set_cash", {"amount", "currency"}),
    ("set_fund", {"amount"}),
])
def test_daily_details_only_show_relevant_fields_and_keep_choices_out_of_form(operation, fields, models, context):
    model = {**models["daily_form"], "operation": operation, "amount": Decimal("12.3400")}
    card = build_card("daily_form", model, context)
    assert_card_structure(card, context)
    controls = {node["name"]: node for node in walk(card)
                if node.get("tag") in {"input", "date_picker", "select_static"}}
    assert set(controls) == fields
    assert not set(controls).intersection({"date", "account", "operation"})
    if "amount" in controls:
        assert controls["amount"]["default_value"] == "12.3400"
        assert controls["amount"]["required"] is True
    if operation == "set_fund":
        assert "CNY" in controls["amount"]["label"]["content"]
    summary = json.dumps(card["body"]["elements"][0], ensure_ascii=False)
    assert "2026-09-14" in summary and "虚构账户" in summary
    assert any(node.get("action") == "daily.open" for node in walk(card))
    submit = next(node for node in walk(card) if node.get("form_action_type") == "submit")
    assert submit["name"] == "qrw_" + context.nonces["daily.preview"]


def test_no_change_and_unknown_operation_do_not_render_transaction_details(context):
    for operation in ("no_change", "unknown", None):
        with pytest.raises(ValueError, match="daily operation"):
            build_card("daily_form", {"operation": operation}, context)


def test_form_preserves_decimal_text_and_does_not_offer_confirmation(models, context):
    card = build_card("daily_form", models["daily_form"], context)
    inputs = {node["name"]: node for node in walk(card) if node.get("tag") == "input"}
    assert inputs["quantity"]["default_value"] == "0.1250"
    assert inputs["price"]["default_value"] == "12.3400"
    assert inputs["fee"]["default_value"] == "0.0100"
    assert inputs["symbol"]["placeholder"]["content"] == "NASDAQ:AAPL / SHA:600000"
    assert "股" in inputs["quantity"]["label"]["content"]
    assert not any(node.get("action") == "daily.confirm" for node in walk(card))


def test_confirmation_is_a_separate_page_and_preserves_every_change(models, context):
    card = build_card("daily_preview", models["daily_preview"], context)
    serialized = json.dumps(card, ensure_ascii=False)
    for value in ("98.4475", "1.1250", "0.0100"):
        assert value in serialized
    confirm = next(node for node in walk(card) if node.get("tag") == "button" and node["type"] == "primary_filled")
    assert confirm["behaviors"][0]["value"]["action"] == "daily.confirm"
    assert confirm["confirm"]["title"]["tag"] == "plain_text"
    assert "按已配置渠道推送日报" in confirm["confirm"]["text"]["content"]
    assert not any(node.get("tag") == "form" for node in walk(card))


def test_user_text_cannot_inject_mentions_or_links(context):
    value = '<at id=all></at> [secret](https://example.invalid) **fake**'
    card = build_card("daily_preview", {"summary": value, "changes": [value]}, context)
    content = "\n".join(node["content"] for node in walk(card) if node.get("tag") == "markdown")
    assert '<at id=all>' not in content
    assert '[secret](' not in content
    assert '**fake**' not in content
    assert 'secret' in content and 'example.invalid' in content


def test_missing_data_disables_preview_without_fabricating_values(context):
    card = build_card("research_form", {"goal": "方法学习"}, context)
    submit = next(node for node in walk(card) if node.get("form_action_type") == "submit")
    assert submit["disabled"] is True
    assets = build_card("assets", {"positions": [{"label": "示例资产", "value": "EXAMPLE"}]}, context)
    table = next(node for node in walk(assets) if node.get("tag") == "table")
    assert table["rows"][0]["value"] == "—"


@pytest.mark.parametrize("key", ["accounts", "groups"])
def test_assets_show_each_account_original_currency_and_cny_fund_without_rounding(key, context):
    model = {key: [
        {"label": "虚构账户甲", "value": "account_a",
         "cash_balances": {"CNY": "100.0000", "USD": Decimal("0.1250"), "HKD": "-3.10"},
         "fund": Decimal("12.3400")},
        {"label": "虚构账户乙", "value": "account_b", "cash_balances": {"USD": "22.00"}, "fund": "0"},
    ], "positions": [{"label": "示例证券", "value": "NASDAQ:EXAMPLE", "quantity": "2"}]}
    card = build_card("assets", model, context)
    assert_card_structure(card, context)
    balances, positions = [node for node in walk(card) if node.get("tag") == "table"]
    assert balances["rows"] == [
        {"account": "虚构账户甲", "kind": "现金 · CNY", "amount": "100.0000"},
        {"account": "虚构账户甲", "kind": "现金 · USD", "amount": "0.1250"},
        {"account": "虚构账户甲", "kind": "现金 · HKD", "amount": "-3.10"},
        {"account": "虚构账户甲", "kind": "基金估值 · CNY", "amount": "12.3400"},
        {"account": "虚构账户乙", "kind": "现金 · USD", "amount": "22.00"},
        {"account": "虚构账户乙", "kind": "基金估值 · CNY", "amount": "0"},
    ]
    assert positions["rows"][0]["value"] == "—"
    assert "不跨币种相加" in json.dumps(card, ensure_ascii=False)


def test_assets_do_not_infer_missing_cash_or_fund_and_reject_invalid_balance_data(context):
    card = build_card("assets", {"accounts": [{"label": "虚构账户", "value": "example"}]}, context)
    assert not any(node.get("tag") == "table" for node in walk(card))
    with pytest.raises(ValueError, match="cash_balances must be a mapping"):
        build_card("assets", {"accounts": [{"cash_balances": ["100"]}]}, context)
    with pytest.raises(ValueError, match="finite"):
        build_card("assets", {"accounts": [{"fund": Decimal("Infinity")}]}, context)


def test_research_history_uses_form_nonce_and_selected_run_id(models, context):
    card = build_card("research_list", models["research_list"], context)
    form = next(node for node in walk(card) if node.get("tag") == "form")
    selector = next(node for node in walk(form) if node.get("tag") == "select_static")
    submit = next(node for node in walk(form) if node.get("tag") == "button")
    assert selector["name"] == "run_id" and selector["required"] is True
    assert selector["initial_option"] == "run_example"
    assert selector["options"] == [{"text": {"tag": "plain_text", "content": "合成波动实验 · 已完成"},
                                     "value": "run_example"}]
    assert submit["name"] == "qrw_" + context.nonces["research.select"]
    assert submit["form_action_type"] == "submit" and "behaviors" not in submit
    assert not any(node.get("action") in {"research.start", "research.cancel"} for node in walk(card))


def test_research_history_limits_choices_to_twenty_and_does_not_select_hidden_run(context):
    runs = [{"label": f"虚构任务 {index}", "value": f"run_{index}"} for index in range(25)]
    card = build_card("research_list", {"runs": runs, "run_id": "run_24"}, context)
    assert_card_structure(card, context)
    selector = next(node for node in walk(card) if node.get("tag") == "select_static")
    assert [option["value"] for option in selector["options"]] == [f"run_{index}" for index in range(20)]
    assert "initial_option" not in selector
    assert "前 20 项，共收到 25 项记录" in json.dumps(card, ensure_ascii=False)
    assert len(runs) == 25


def test_empty_research_history_disables_selection_and_submit_but_keeps_new_research(context):
    card = build_card("research_list", {}, context)
    assert_card_structure(card, context)
    selector = next(node for node in walk(card) if node.get("tag") == "select_static")
    submit = next(node for node in walk(card) if node.get("form_action_type") == "submit")
    assert selector["options"] == [] and selector["disabled"] is True
    assert submit["disabled"] is True
    assert any(node.get("action") == "research.open" for node in walk(card))
    assert "暂无研究记录" in json.dumps(card, ensure_ascii=False)


def test_research_history_requires_unique_run_ids_and_action_nonce(context):
    with pytest.raises(ValueError, match="duplicate option"):
        build_card("research_list", {"runs": [{"label": "一", "value": "same"},
                                              {"label": "二", "value": "same"}]}, context)
    nonces = {key: value for key, value in context.nonces.items() if key != "research.select"}
    with pytest.raises(ValueError, match="research.select"):
        build_card("research_list", {}, CardContext(context.session_id, context.revision, nonces))


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_research_has_no_cancel_action(status, context):
    card = build_card("research_status", {"status": status, "can_cancel": True}, context)
    assert not any(node.get("action") == "research.cancel" for node in walk(card))


def test_research_result_keeps_secondary_metrics_and_method_notes(models, context):
    card = build_card("research_result", models["research_result"], context)
    serialized = json.dumps(card, ensure_ascii=False)
    for value in ("验证误差", "0.13", "分数仓位不等于实际撮合", "合成数据，不代表市场证据"):
        assert value in serialized
    panel = next(node for node in walk(card) if node.get("tag") == "collapsible_panel")
    assert panel["expanded"] is False


def test_agent_preview_discloses_remote_context_before_start(models, context):
    serialized = json.dumps(build_card("research_preview", models["research_preview"], context), ensure_ascii=False)
    assert "所选模型会收到研究问题" in serialized
    assert "research.start" in serialized
    unspecified = json.dumps(build_card("research_preview", {"summary": "研究配置"}, context), ensure_ascii=False)
    assert "远程模型会接收研究上下文" in unspecified
    assert "不调用模型" not in unspecified


def test_invalid_or_oversized_models_fail_closed(context):
    with pytest.raises(ValueError, match="preview requires"):
        build_card("daily_preview", {}, context)
    with pytest.raises(ValueError, match="display budget"):
        build_card("daily_preview", {"changes": ["完整变更" * 4000]}, context)
    with pytest.raises(ValueError, match="component budget"):
        build_card("reports", {"items": [{"title": "短报", "summary": "内容"}] * 210}, context)
    with pytest.raises(ValueError, match="nonce"):
        build_card("daily_preview", {"summary": "变更预览"}, CardContext("session_example", 1, {}))
    with pytest.raises(ValueError):
        build_card("research_result", {"equity": [{"date": "2026-01-01", "strategy": float("nan")}]}, context)
    with pytest.raises(ValueError, match="finite"):
        build_card("assets", {"total": Decimal("NaN")}, context)


def test_rendering_does_not_mutate_inputs(models, context):
    original = json.dumps(models["research_result"], sort_keys=True)
    build_card("research_result", models["research_result"], context)
    assert json.dumps(models["research_result"], sort_keys=True) == original
