"""Pure Card 2.0 views for the portfolio and research workbench.

No API calls, credential access, account reads or state changes occur here.
The controller owns message/session state, authorization, nonce consumption,
preview revisions and business validation. A form button's ``qrw_<nonce>`` name
is its complete routing key; ordinary buttons carry an opaque session reference.

Model contract (all displayed numbers may be Decimal-preserving strings):
* Shared optional fields: title, subtitle, notice.
* Options: accounts/positions/templates/datasets/connections are label/value
  mappings. Positions may additionally contain quantity, market_value and pnl.
* daily_select: date/account/accounts/operation; submits only the three choices.
* daily_form: saved date/account/operation, optional accounts for account labels;
  transaction fields symbol/quantity/price/currency/fee, cash amount/currency,
  fund amount (CNY). The controller merges saved choices with submitted fields.
* daily_preview, daily_receipt, research_preview: summary, changes: list[str],
  reference; research_preview may also use the research_form fields.
* assets: total, cash, change, as_of, positions; accounts (or groups) contains
  label/value, cash_balances: mapping[currency, amount], fund: CNY valuation.
  Missing balances are not inferred and different currencies are not summed.
* research_form: goal/template_id/dataset_id/mode/connection_id/max_trials.
* research_list: runs: list[label/value], optional run_id; the controller orders
  runs newest first. At most 20 are offered, with an explicit notice if limited.
* research_status: status, stage, goal, completed, max_trials, error,
  events: list[str], can_cancel (default false).
* research_result: summary, metrics: list[label/value], lessons, warnings,
  equity: list[date/strategy/benchmark]; both equity series are optional.
* reports: items: list[title/summary/date]. error: message, detail.

Critical previews are never silently truncated. If a model exceeds the local
28 KB card budget, the controller must show an error or request a smaller batch.
This budget is an application limit, not a claim about a platform API limit.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
import math
import re
from typing import Any


MAX_CARD_BYTES = 28_000
ACTIONS = frozenset({
    "daily.unchanged", "daily.batch", "daily.add", "daily.item.choose", "daily.item.save",
    "daily.item.edit", "daily.item.remove", "daily.discard", "daily.date", "daily.date.save",
    "daily.batch.preview", "daily.batch.confirm", "daily.commit.retry",
    "home", "daily.open", "daily.choose", "daily.preview", "daily.confirm", "assets",
    "research.open", "research.preview", "research.start", "research.list",
    "research.select", "research.cancel", "research.refresh", "reports", "daily.report.retry",
})


@dataclass(frozen=True)
class CardContext:
    session_id: str
    revision: int
    nonces: Mapping[str, str]


def _string(value: Any) -> str:
    if value is None:
        return ""
    if (isinstance(value, float) and not math.isfinite(value)) or (isinstance(value, Decimal) and not value.is_finite()):
        raise ValueError("Card numbers must be finite")
    if isinstance(value, (str, int, float, Decimal)) and not isinstance(value, bool):
        return str(value)
    raise ValueError("Card text must be a string or number")


def _escape(value: Any) -> str:
    """Render untrusted text without links, mentions or formatting injection."""
    text = _string(value).replace("&", "&amp;")
    for char in "<>*~[]()#:_`\\":
        text = text.replace(char, f"&#{ord(char)};")
    return text


def _plain(value: Any) -> dict:
    return {"tag": "plain_text", "content": _string(value)}


def _markdown(content: str, *, caption: bool = False) -> dict:
    result = {"tag": "markdown", "content": content}
    if caption:
        result["text_size"] = "notation"
    return result


def _list(model: Mapping, key: str) -> list:
    value = model.get(key, [])
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{key} must be a list")
    return list(value)


def _nonce(context: CardContext, action: str) -> str:
    value = context.nonces.get(action)
    if action not in ACTIONS or not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError(f"Missing or invalid nonce for {action}")
    return value


def _button(context: CardContext, action: str, label: str, *, primary: bool = False,
            disabled: bool = False, confirm: bool = False) -> dict:
    result = {
        "tag": "button", "text": _plain(label),
        "type": "primary_filled" if primary else "default",
        "behaviors": [{"type": "callback", "value": {
            "qr_workbench": 1, "action": action, "session_id": context.session_id,
            "revision": context.revision, "nonce": _nonce(context, action),
        }}],
    }
    if disabled:
        result["disabled"] = True
    if confirm:
        result["confirm"] = {
            "title": _plain("确认更新"),
            "text": _plain("保存资产变更，并按已配置渠道推送日报。"),
        }
    return result


def _buttons(*buttons: dict) -> dict:
    return {
        "tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "8px",
        "columns": [{"tag": "column", "width": "auto", "elements": [button]} for button in buttons],
    }


def _submit(context: CardContext, action: str, label: str, *, disabled: bool = False, primary: bool = True) -> dict:
    return {
        "tag": "button", "name": "qrw_" + _nonce(context, action),
        "text": _plain(label), "type": "primary_filled" if primary else "default", "width": "fill",
        "form_action_type": "submit", "disabled": disabled,
    }


def _input(name: str, label: str, value: Any = "", *, required: bool = False,
           multiline: bool = False, placeholder: str = "") -> dict:
    result = {
        "tag": "input", "name": name, "label": _plain(label),
        "default_value": _string(value), "required": required, "width": "fill",
        "input_type": "multiline_text" if multiline else "text", "max_length": 1000,
    }
    if multiline:
        result["rows"] = 3
    if placeholder:
        result["placeholder"] = _plain(placeholder)
    return result


def _select(name: str, options: Sequence, initial: Any = "", *, required: bool = False,
            placeholder: str = "请选择") -> dict:
    values = []
    seen = set()
    for option in options:
        if not isinstance(option, Mapping) or "label" not in option or "value" not in option:
            raise ValueError(f"Invalid option for {name}")
        value = _string(option["value"])
        if not value or value in seen:
            raise ValueError(f"Empty or duplicate option value for {name}")
        seen.add(value)
        values.append({"text": _plain(option["label"]), "value": value})
    result = {
        "tag": "select_static", "name": name, "options": values,
        "required": required, "placeholder": _plain(placeholder), "width": "fill",
    }
    if not values:
        result["disabled"] = True
    if _string(initial) in seen:
        result["initial_option"] = _string(initial)
    return result


def _field(label: str, component: dict) -> list[dict]:
    return [_markdown(f"**{_escape(label)}**"), component]


def _highlight(title: str, content: Any, *, color: str = "blue") -> dict:
    return {
        "tag": "column_set", "flex_mode": "none",
        "columns": [{"tag": "column", "width": "weighted", "weight": 1,
                     "background_style": f"{color}-50", "padding": "12px", "vertical_spacing": "4px",
                     "elements": [_markdown(f"**{_escape(title)}**"), _markdown(_escape(content))]}],
    }


def _fold(title: str, elements: list[dict]) -> dict:
    return {"tag": "collapsible_panel", "expanded": False,
            "header": {"title": _plain(title)}, "elements": elements}


def _preview_text(model: Mapping) -> list[dict]:
    elements = []
    if model.get("summary"):
        elements.append(_markdown(_escape(model["summary"])))
    changes = _list(model, "changes")
    if changes:
        elements.append(_markdown("\n".join(f"{i}. {_escape(value)}" for i, value in enumerate(changes, 1))))
    if model.get("reference"):
        elements.append(_markdown("记录编号：" + _escape(model["reference"]), caption=True))
    return elements


def _home(model: Mapping, context: CardContext) -> list[dict]:
    return [
        _highlight("操作", model.get("summary", "每日更新 · 资产 · 研究")),
        _buttons(_button(context, "daily.open", "每日更新", primary=True),
                 _button(context, "assets", "资产"), _button(context, "research.open", "研究任务")),
        _buttons(_button(context, "reports", "日报")),
    ]


_OPERATIONS = {
    "no_change": "今日无变动", "buy": "买入", "sell": "卖出",
    "set_cash": "现金对账", "set_fund": "基金估值",
}


def _daily_select(model: Mapping, context: CardContext) -> list[dict]:
    accounts = _list(model, "accounts")
    picker = {"tag": "date_picker", "name": "date", "required": True, "width": "fill",
              "placeholder": _plain("选择记账日期")}
    if model.get("date"):
        picker["initial_date"] = date.fromisoformat(_string(model["date"])).isoformat()
    elements = _field("记账日期", picker)
    elements += _field("账户", _select("account", accounts, model.get("account", ""), required=True,
                                      placeholder="选择账户"))
    operations = [{"value": value, "label": label} for value, label in _OPERATIONS.items()]
    elements += _field("操作", _select("operation", operations, model.get("operation", "no_change"), required=True))
    elements.append(_submit(context, "daily.choose", "继续", disabled=not accounts))
    return [
        _highlight("更新事项", "选择日期、账户和操作。"),
        {"tag": "form", "name": "daily_select_form", "elements": elements},
        _buttons(_button(context, "home", "返回主页")),
    ]


def _daily_form(model: Mapping, context: CardContext) -> list[dict]:
    operation = model.get("operation")
    if operation not in {"buy", "sell", "set_cash", "set_fund"}:
        raise ValueError("Choose a supported daily operation before entering details")
    account = _string(model.get("account", ""))
    account_label = next((_string(item["label"]) for item in _list(model, "accounts")
                          if item.get("value") == account), account)
    summary = " · ".join(filter(None, [_string(model.get("date", "")), account_label, _OPERATIONS[operation]]))
    elements = []
    if operation in {"buy", "sell"}:
        elements += [
            _input("symbol", "证券代码", model.get("symbol", ""), required=True,
                   placeholder="NASDAQ:AAPL / SHA:600000"),
            _input("quantity", "数量（股）", model.get("quantity", ""), required=True),
            _input("price", "成交单价（所选币种）", model.get("price", ""), required=True),
        ]
    else:
        label = "核对后现金余额" if operation == "set_cash" else "基金合计估值（CNY）"
        elements.append(_input("amount", label, model.get("amount", ""), required=True))
    if operation != "set_fund":
        elements += _field("币种", _select(
            "currency", [{"label": unit, "value": unit} for unit in ("CNY", "HKD", "USD")],
            model.get("currency", "CNY"), required=True))
    if operation in {"buy", "sell"}:
        elements.append(_input("fee", "手续费（所选币种，默认 0）", model.get("fee", "0")))
    elements.append(_submit(context, "daily.preview", "预览更新"))
    return [
        _highlight("本次更新", summary),
        {"tag": "form", "name": "daily_form", "elements": elements},
        _buttons(_button(context, "daily.open", "返回修改"), _button(context, "home", "返回主页")),
    ]


def _daily_preview(model: Mapping, context: CardContext) -> list[dict]:
    details = _preview_text(model)
    if not details:
        raise ValueError("A daily preview requires complete changes or a summary")
    return [
        _highlight("变更预览", "确认后保存变更，并按已配置渠道推送日报。数据变化时需重新预览。"),
        {"tag": "column_set", "flex_mode": "none", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": details}]},
        _buttons(_button(context, "daily.confirm", "确认更新", primary=True, confirm=True),
                 _button(context, "daily.open", "返回修改"), _button(context, "home", "取消")),
    ]


def _daily_receipt(model: Mapping, context: CardContext) -> list[dict]:
    return [
        _highlight("已保存", model.get("summary", "日报处理状态可在“日报”查看。"), color="green"),
        _fold("记录详情", _preview_text(model)),
        _buttons(_button(context, "assets", "查看资产", primary=True),
                 _button(context, "reports", "查看日报"), _button(context, "home", "返回主页")),
    ]


def _assets(model: Mapping, context: CardContext) -> list[dict]:
    balance_rows = []
    for account in _list(model, "accounts" if "accounts" in model else "groups"):
        if not isinstance(account, Mapping):
            raise ValueError("Asset accounts must be mappings")
        label = _string(account.get("label", account.get("value", "")))
        balances = account.get("cash_balances", {})
        if not isinstance(balances, Mapping):
            raise ValueError("Account cash_balances must be a mapping")
        for currency, amount in balances.items():
            balance_rows.append({"account": label, "kind": "现金 · " + _string(currency),
                                 "amount": _string(amount)})
        if "fund" in account:
            balance_rows.append({"account": label, "kind": "基金估值 · CNY",
                                 "amount": _string(account["fund"])})
    position_rows = []
    for position in _list(model, "positions"):
        position_rows.append({
            "security": _string(position.get("label", position.get("value", ""))),
            "quantity": _string(position.get("quantity", "—")),
            "value": _string(position.get("market_value", "—")),
        })
    amounts = "\n".join(["## " + _escape(model.get("total", "暂无可用估值")),
                           "现金：" + _escape(model.get("cash", "—")),
                           "变化：" + _escape(model.get("change", "—"))])
    metric = _highlight("总资产", "")
    metric["columns"][0]["elements"] = [
        _markdown("**总资产**"), _markdown(amounts),
        _markdown("记录时间：" + _escape(model.get("as_of", "未提供")), caption=True),
    ]
    elements = [metric]
    if balance_rows:
        metric["columns"][0]["elements"].append(_markdown(
            "余额按账户及原币列示，不跨币种相加；基金为 CNY 合计估值。", caption=True))
        elements.append({"tag": "table", "page_size": 5, "freeze_first_column": True,
                         "columns": [
                             {"name": "account", "display_name": "账户", "data_type": "text"},
                             {"name": "kind", "display_name": "类别 / 币种", "data_type": "text"},
                             {"name": "amount", "display_name": "余额 / 估值", "data_type": "text"}],
                         "rows": balance_rows})
    if position_rows:
        elements.append({"tag": "table", "page_size": 5, "freeze_first_column": True,
                         "columns": [
                             {"name": "security", "display_name": "资产", "data_type": "text"},
                             {"name": "quantity", "display_name": "数量", "data_type": "text"},
                             {"name": "value", "display_name": "市值", "data_type": "text"}],
                         "rows": position_rows})
    else:
        elements.append(_markdown("暂无资产明细。"))
    elements.append(_buttons(_button(context, "assets", "刷新资产", primary=True),
                             _button(context, "daily.open", "记录变更"), _button(context, "home", "返回主页")))
    return elements


def _research_form(model: Mapping, context: CardContext) -> list[dict]:
    templates, datasets = _list(model, "templates"), _list(model, "datasets")
    elements = [_input("goal", "研究问题", model.get("goal", ""), required=True, multiline=True)]
    elements += _field("研究模板", _select("template_id", templates, model.get("template_id", ""), required=True))
    elements += _field("研究数据", _select("dataset_id", datasets, model.get("dataset_id", ""), required=True))
    elements += _field("运行模式", _select("mode", [
        {"label": "手动实验", "value": "manual"}, {"label": "Agent 自动研究", "value": "agent"}],
        model.get("mode", "manual"), required=True))
    elements += _field("模型连接（仅 Agent 模式需要）", _select(
        "connection_id", _list(model, "connections"), model.get("connection_id", ""), placeholder="选择已有连接"))
    elements += [_input("max_trials", "最大实验次数（Agent：1–6）", model.get("max_trials", "3"), required=True),
                 _submit(context, "research.preview", "预览配置", disabled=not templates or not datasets)]
    return [
        _highlight("数据与模型", "使用已导入的数据。手动实验不调用模型；Agent 会向所选模型发送研究上下文，并消耗模型额度。"),
        {"tag": "form", "name": "research_form", "elements": elements},
        _buttons(_button(context, "research.list", "研究记录"), _button(context, "home", "返回主页")),
    ]


def _research_list(model: Mapping, context: CardContext) -> list[dict]:
    runs = _list(model, "runs")
    shown = runs[:20]
    summary = ("选择任务查看进度或结果。" if shown else "暂无研究记录。")
    if len(runs) > len(shown):
        summary += f" 当前仅显示前 20 项，共收到 {len(runs)} 项记录。"
    return [
        _highlight("任务列表", summary),
        {"tag": "form", "name": "research_list_form", "elements": [
            *_field("研究任务（最多 20 项）", _select(
                "run_id", shown, model.get("run_id", ""), required=True, placeholder="选择已有研究任务")),
            _submit(context, "research.select", "查看所选研究", disabled=not shown),
        ]},
        _buttons(_button(context, "research.open", "新建研究"),
                 _button(context, "research.list", "刷新列表"), _button(context, "home", "返回主页")),
    ]


def _research_preview(model: Mapping, context: CardContext) -> list[dict]:
    details = _preview_text(model)
    fields = [("问题", "goal"), ("模板", "template_id"), ("数据版本", "dataset_id"),
              ("模式", "mode"), ("模型连接", "connection_id"), ("实验次数上限", "max_trials")]
    for label, key in fields:
        if model.get(key) is not None:
            value = {"manual": "手动实验", "agent": "Agent 自动研究"}.get(_string(model[key]), model[key])
            details.append(_markdown(f"**{label}**：{_escape(value)}"))
    if not details:
        raise ValueError("A research preview requires a configuration")
    if model.get("mode") == "agent":
        explanation = "所选模型会收到研究问题、数据元信息和实验结果，并消耗模型额度。"
    elif model.get("mode") == "manual":
        explanation = "手动实验，不调用模型。"
    else:
        explanation = "核对运行模式和模型连接。远程模型会接收研究上下文。"
    return [
        _highlight("运行模式", explanation),
        {"tag": "column_set", "flex_mode": "none", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": details}]},
        _buttons(_button(context, "research.start", "启动研究", primary=True),
                 _button(context, "research.open", "返回修改"), _button(context, "home", "取消")),
    ]


_STATUS = {"queued": "等待运行", "running": "运行中", "completed": "已完成",
           "failed": "失败", "cancelled": "已取消", "cancelling": "正在取消"}


def _research_status(model: Mapping, context: CardContext) -> list[dict]:
    status = _string(model.get("status", "queued"))
    progress = f"{_STATUS.get(status, status)} · {_string(model.get('stage', ''))}"
    progress += f"\n已完成 {_string(model.get('completed', '0'))} / {_string(model.get('max_trials', '—'))} 个实验"
    elements = [_highlight("进度", progress), _markdown(_escape(model.get("goal", "")))]
    notes = [_markdown(_escape(value)) for value in _list(model, "events")]
    if model.get("error"):
        elements.append(_highlight("错误", model["error"], color="red"))
    if notes:
        elements.append(_fold("任务记录", notes))
    buttons = [_button(context, "research.refresh", "刷新进度", primary=True)]
    if model.get("can_cancel") is True and status in {"queued", "running", "cancelling"}:
        buttons.append(_button(context, "research.cancel", "取消研究", disabled=status == "cancelling"))
    buttons.append(_button(context, "home", "返回主页"))
    elements.append(_buttons(*buttons))
    return elements


def _research_result(model: Mapping, context: CardContext) -> list[dict]:
    elements = [_highlight("结果摘要", model.get("summary", "暂无摘要。"))]
    metrics = _list(model, "metrics")
    if metrics:
        columns = []
        for index, metric in enumerate(metrics[:3]):
            content = ("## " if index == 0 else "**") + _escape(metric["value"]) + ("" if index == 0 else "**")
            columns.append({"tag": "column", "width": "weighted", "weight": 1,
                            "background_style": "grey-50", "padding": "8px", "elements": [
                                _markdown(content), _markdown(_escape(metric["label"]), caption=True)]})
        elements.append({"tag": "column_set", "flex_mode": "none", "columns": columns})
    equity = _list(model, "equity")
    values = []
    for point in equity:
        for key, label in (("strategy", "研究组合"), ("benchmark", "基准")):
            if point.get(key) is not None:
                values.append({"date": _string(point["date"]), "value": float(point[key]), "series": label})
    if values:
        elements.append({"tag": "chart", "color_theme": "brand", "aspect_ratio": "16:9", "preview": True,
                         "chart_spec": {"type": "line", "data": {"values": values},
                                        "xField": "date", "yField": "value", "seriesField": "series"}})
    notes = [_markdown(f"**{_escape(metric['label'])}**：{_escape(metric['value'])}") for metric in metrics[3:]]
    for key, title in (("lessons", "研究说明"), ("warnings", "方法与数据限制")):
        values = _list(model, key)
        if values:
            notes.append(_markdown(f"**{title}**\n" + "\n".join("- " + _escape(value) for value in values)))
    if notes:
        elements.append(_fold("详细说明", notes))
    elements.append(_buttons(_button(context, "research.open", "新建研究", primary=True),
                             _button(context, "research.list", "研究记录"), _button(context, "home", "返回主页")))
    return elements


def _reports(model: Mapping, context: CardContext) -> list[dict]:
    details = []
    for item in _list(model, "items"):
        title = _escape(item.get("title", "日报"))
        if item.get("date"):
            title += " · " + _escape(item["date"])
        details.append(_markdown(f"**{title}**\n{_escape(item.get('summary', ''))}"))
    primary = _button(context, "daily.report.retry", "重试日报", primary=True) if model.get("can_retry_report") else _button(context, "reports", "刷新日报", primary=True)
    return [_highlight("最近日报", model.get("summary", "已保存的组合记录。")),
            _fold("日报内容", details or [_markdown("暂无日报。")]),
            _buttons(primary,
                     _button(context, "daily.open", "每日更新"), _button(context, "home", "返回主页"))]


def _error(model: Mapping, context: CardContext) -> list[dict]:
    elements = [_highlight("错误", model.get("message", "操作未完成，请返回主页查看状态。"), color="red")]
    if model.get("detail"):
        elements.append(_markdown(_escape(model["detail"])))
    elements.append(_buttons(_button(context, "home", "返回主页", primary=True)))
    return elements


_VIEWS = {
    "home": ("QR 工作台", _home),
    "daily_select": ("每日更新", _daily_select),
    "daily_form": ("每日更新", _daily_form),
    "daily_preview": ("确认更新", _daily_preview),
    "daily_receipt": ("更新记录", _daily_receipt),
    "assets": ("资产", _assets),
    "research_form": ("新建研究", _research_form),
    "research_list": ("研究记录", _research_list),
    "research_preview": ("确认研究", _research_preview),
    "research_status": ("研究任务", _research_status),
    "research_result": ("研究结果", _research_result),
    "reports": ("日报", _reports),
    "error": ("操作未完成", _error),
}



def build_card(view: str, model: Mapping[str, Any], context: CardContext) -> dict:
    """Return a complete, JSON-serializable shared Card 2.0 without side effects."""
    from .daily_cards import VIEWS
    views = {**_VIEWS, **VIEWS}
    if view not in views:
        raise ValueError("Unknown workbench card view")
    if (not isinstance(context.session_id, str) or not context.session_id
            or not isinstance(context.revision, int) or isinstance(context.revision, bool) or context.revision < 0):
        raise ValueError("Invalid card session context")
    title, builder = views[view]
    elements = builder(model, context)
    if model.get("notice"):
        elements[0]["columns"][0]["elements"].append(_markdown(_escape(model["notice"]), caption=True))
    header = {"title": _plain(model.get("title", title)),
              "template": "green" if view == "daily_receipt" else "red" if view == "error" else "blue",
              "icon": {"tag": "standard_icon", "token": "ai-common_colorful" if view.startswith("research")
                       else "approval_colorful" if view.startswith("daily") else "lark-logo_colorful"}}
    if model.get("subtitle"):
        header["subtitle"] = _plain(model["subtitle"])
    card = {"schema": "2.0", "config": {"update_multi": True, "width_mode": "default", "enable_forward": False},
            "header": header,
            "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "12px",
                     "elements": elements}}
    encoded = json.dumps(card, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    def count_tags(value: Any) -> int:
        if isinstance(value, dict):
            return int("tag" in value) + sum(count_tags(child) for child in value.values())
        return sum(count_tags(child) for child in value) if isinstance(value, list) else 0
    if count_tags(card) > 200:
        raise ValueError("Card exceeds the component budget; paginate the content")
    if len(encoded.encode("utf-8")) > MAX_CARD_BYTES:
        raise ValueError("Card exceeds the display budget; split the request instead of truncating the preview")
    return card
