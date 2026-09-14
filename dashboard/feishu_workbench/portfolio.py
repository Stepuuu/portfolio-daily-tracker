"""Explicit, previewed portfolio updates for a local messaging controller.

This adapter does not enable the transaction ledger. Financial mutations use a
shared ``portfolio/.portfolio-update.lock``; other legacy writers must hold that
same lock across their entire read/modify/write operation. A preview writes only
private proposal state, never holdings, ticker aliases, snapshots or messages.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo


_MARKETS = {"SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"}
class PortfolioError(ValueError):
    """A validated, user-facing accounting error."""


_ACTIONS = {"no_change", "buy", "sell", "set_cash", "set_fund", "deposit", "withdraw", "set_cost_basis"}
_FIELDS = {"action", "operation", "account", "date", "ticker", "quantity", "price", "fee", "currency", "amount", "name", "principal_cny"}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decimal(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or value is None or len(str(value)) > 100:
        raise PortfolioError("请填写明确的数字，金额单位为元，数量单位为股。")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PortfolioError("数字格式无效；请勿填写单位、公式或千位分隔符。") from None
    if not number.is_finite() or abs(number) > Decimal("1e18") or number.as_tuple().exponent < -12:
        raise PortfolioError("数字必须有限，且小数位不得超过 12 位。")
    if positive and number <= 0 or nonnegative and number < 0:
        raise PortfolioError("数量和成交价必须大于零；金额与费用不得为负数。")
    return number


def _text(value):
    number = _decimal(value)
    if number == 0:
        return "0"
    result = format(number, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _stored_number(value):
    """Preserve the legacy numeric JSON shape without silently rounding money."""
    number = _decimal(value)
    if number == number.to_integral_value():
        return int(number)
    result = float(number)
    if Decimal(str(result)) != number:
        raise PortfolioError("该数字超出旧版持仓文件可准确保存的精度。")
    return result


def _ticker(value):
    if not isinstance(value, str):
        raise PortfolioError("请选择含交易所前缀的证券代码。")
    value = value.strip().upper()
    match = re.fullmatch(r"(SHA|SHE|HKG|NASDAQ|NYSE):([A-Z0-9][A-Z0-9.\-]{0,19})", value)
    if not match:
        raise PortfolioError("证券代码必须明确，例如 NASDAQ:AAPL；不接受名称或模糊匹配。")
    market, code = match.groups()
    if market in {"SHA", "SHE"} and not re.fullmatch(r"\d{6}", code):
        raise PortfolioError("A 股代码需要交易所前缀和六位数字。")
    if market == "HKG":
        if not re.fullmatch(r"\d{4,5}", code):
            raise PortfolioError("港股代码需要 HKG 前缀和四至五位数字。")
        code = str(int(code)).zfill(4)
    return market + ":" + code


def _day(value=None):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    value = value or today.isoformat()
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise PortfolioError("日期必须为 YYYY-MM-DD。")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise PortfolioError("日期无效。") from None
    if parsed > today:
        raise PortfolioError("不能提前登记未来日期的组合变更。")
    return value


class PortfolioAdapter:
    def __init__(self, project_dir: Path, state_dir: Path):
        self.project_dir = Path(project_dir).resolve()
        engine = self.project_dir / "engine"
        self.root = engine if (engine / "scripts").is_dir() else self.project_dir
        self.portfolio_dir = self.root / "portfolio"
        self.holdings_dir = self.portfolio_dir / "holdings"
        self.scripts_dir = self.root / "scripts"
        self.reports_dir = self.root / "reports"
        self.lock_path = self.portfolio_dir / ".portfolio-update.lock"
        self.state_dir = Path(state_dir).resolve() / "portfolio-adapter"
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.db_path = self.state_dir / "operations.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS previews (
                    id TEXT PRIMARY KEY, expires REAL NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, preview_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL,
                    report_status TEXT NOT NULL DEFAULT 'not_started'
                );
            """)
        self.db_path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def _lock(self):
        if not self.portfolio_dir.is_dir():
            raise PortfolioError("尚未配置组合目录，请先在 QR 中完成初始化。")
        with open(self.lock_path, "a+b") as handle:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _guard_legacy(self):
        ledger = self.portfolio_dir / "ledger.sqlite3"
        if not ledger.exists():
            return
        try:
            db = sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)
            try:
                row = db.execute("SELECT revision > 0 FROM ledger_meta WHERE id=1").fetchone()
            finally:
                db.close()
        except sqlite3.Error:
            raise PortfolioError("无法确认交易账本状态，已暂停旧持仓更新。") from None
        if not row or row[0]:
            raise PortfolioError("交易账本已启用，请使用账本预览与确认入口；旧持仓不会被覆盖。")

    def _files(self):
        files = []
        for path in self.holdings_dir.glob("*.json"):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name):
                continue
            try:
                date.fromisoformat(path.stem)
            except ValueError:
                continue
            if path.is_symlink():
                raise PortfolioError("持仓文件类型不受支持，请在 QR 中检查数据目录。")
            files.append(path)
        return sorted(files)

    def _baseline(self, day=None):
        self._guard_legacy()
        files = self._files()
        if not files:
            raise PortfolioError("暂无已保存持仓，请先在 QR 中完成组合初始化。")
        latest = files[-1]
        if day and day < latest.stem:
            raise PortfolioError("已有更新日期更晚的持仓，请刷新卡片；历史更正请在 QR 中处理。")
        raw = latest.read_bytes()
        if len(raw) > 10_000_000:
            raise PortfolioError("持仓数据过大，请在 QR 中检查。")
        try:
            value = json.loads(raw)
            _encode(value)
        except (ValueError, TypeError, UnicodeError):
            raise PortfolioError("持仓文件无法读取为有效数据，请在 QR 中检查。") from None
        if not isinstance(value, dict) or not isinstance(value.get("groups"), dict) or not value["groups"]:
            raise PortfolioError("持仓文件缺少账户信息，请先完成组合初始化。")
        fingerprint = hashlib.sha256(_encode({"latest": latest.name, "sha256": hashlib.sha256(raw).hexdigest()}).encode()).hexdigest()
        return value, latest.stem, fingerprint

    @staticmethod
    def _cash(group):
        values = group.get("cash_balances")
        if values is None:
            return {"CNY": _decimal(group.get("cash", 0))}
        if not isinstance(values, dict) or set(values) - {"CNY", "HKD", "USD"}:
            raise PortfolioError("账户币种结构无效，请先在 QR 中检查。")
        return {key: _decimal(value) for key, value in values.items()}

    @staticmethod
    def _set_cash(group, balances):
        group["cash_balances"] = {unit: _stored_number(value) for unit, value in balances.items()}
        group["cash"] = _stored_number(balances.get("CNY", Decimal(0)))

    def overview(self):
        try:
            data, saved_day, _ = self._baseline()
        except ValueError:
            if not self._files() and not (self.portfolio_dir / "ledger.sqlite3").exists():
                return {"accounts": [], "groups": [], "positions": [], "date": None, "as_of": None,
                        "summary": "尚未初始化组合", "total": "暂无估值", "cash": "暂无余额", "change": "暂无变动记录"}
            raise
        accounts, positions = [], []
        for account, group in data["groups"].items():
            cash = self._cash(group)
            accounts.append({"label": account, "value": account,
                             "cash_balances": {key: _text(value) for key, value in cash.items()},
                             "fund": _text(group.get("fund", 0)),
                             **({"cost_basis": _text(group["cost_basis"])} if "cost_basis" in group else {})})
            for position in group.get("positions", []):
                ticker = _ticker(position.get("ticker"))
                positions.append({"label": str(position.get("name") or ticker), "value": ticker,
                                  "account": account, "ticker": ticker,
                                  "quantity": _text(position.get("quantity")),
                                  "cost_price": _text(position.get("cost_price", 0)),
                                  "currency": _MARKETS[ticker.split(":")[0]]})
        result = {"accounts": accounts, "groups": accounts, "positions": positions,
                "date": saved_day, "as_of": saved_day,
                "summary": f"{len(accounts)} 个账户，{len(positions)} 项持仓；以最新已保存日报为准。",
                "total": "等待日报估值", "cash": "见账户余额", "change": "暂无当日估值"}
        # Only attach market values when the snapshot describes these exact
        # positions and balances. A later trade must not inherit stale totals.
        path = self.portfolio_dir / 'snapshots' / (saved_day + '.json')
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 10_000_000:
                return result
            snapshot = json.loads(path.read_text())
            if snapshot.get('date') != saved_day or set(snapshot.get('groups', {})) != set(data['groups']):
                return result
            def accounting(group):
                return (self._cash(group), _decimal(group.get('fund', 0)),
                        _decimal(group.get('cost_basis', 0)),
                        sorted((_ticker(p['ticker']), _decimal(p['quantity']), _decimal(p.get('cost_price', 0)))
                               for p in group.get('positions', [])))
            if any(accounting(group) != accounting(snapshot['groups'][name]) for name, group in data['groups'].items()):
                return result
            summary = snapshot['summary']
            money = lambda value: f"¥{_decimal(value):,.2f}"
            result['total'] = money(summary['total_value'])
            change = summary.get('market_daily_change')
            result['change'] = money(change) if change is not None else '暂无当日变动'
            result['as_of'] = snapshot.get('generated_at', saved_day)
            result['notice'] = '人民币估值来自已保存的日报快照；打开本页不会刷新行情。'
            for position in positions:
                rows = snapshot['groups'][position['account']].get('positions', [])
                match = next((p for p in rows if _ticker(p['ticker']) == position['ticker']), None)
                if match and match.get('market_value_cny') is not None:
                    position['market_value'] = money(match['market_value_cny'])
        except (ValueError, KeyError, TypeError, OSError):
            # Holdings remain readable when an old or incomplete snapshot cannot
            # provide a trustworthy valuation.
            pass
        return result

    def _apply_change(self, after, fields, *, allow_overdraft=False):
        if not isinstance(fields, dict) or set(fields) - _FIELDS:
            raise PortfolioError("表单包含不支持的字段，请刷新卡片后重试。")
        if fields.get("action") and fields.get("operation") and fields["action"] != fields["operation"]:
            raise PortfolioError("操作类型冲突，请重新选择。")
        action = fields.get("action", fields.get("operation"))
        if action not in _ACTIONS:
            raise PortfolioError("请选择有效的变动类型。")
        changes = []
        if action == "no_change":
            if any(fields.get(key) not in (None, "") for key in _FIELDS - {"action", "operation", "date"}):
                raise PortfolioError("无变化操作不能同时包含金额、证券或账户变更。")
            changes.append("确认今日持仓、现金、基金和投入本金均无变化。")
        else:
            account = fields.get("account")
            if not isinstance(account, str) or account not in after["groups"]:
                raise PortfolioError("账户不存在，请从当前账户列表中选择。")
            group = after["groups"][account]
            balances = self._cash(group)
            unit = fields.get("currency")
            if unit not in {"CNY", "HKD", "USD"}:
                raise PortfolioError("请明确选择 CNY、HKD 或 USD 币种。")
            if action not in {'deposit', 'withdraw'} and fields.get('principal_cny') not in (None, ''):
                raise PortfolioError('只有资金转入、转出可填写折合人民币本金。')
            if action in {'deposit', 'withdraw', 'set_cost_basis'}:
                if any(fields.get(key) not in (None, '') for key in ('ticker', 'quantity', 'price', 'fee', 'name')):
                    raise PortfolioError('资金与本金变动不能同时包含证券交易字段。')
                if 'cost_basis' not in group:
                    raise PortfolioError('该账户尚未登记投入本金，请先在 QR 中初始化本金。')
                previous = _decimal(group['cost_basis'])
                if action == 'set_cost_basis':
                    if unit != 'CNY':
                        raise PortfolioError('账户投入本金以人民币登记，请填写 CNY 金额。')
                    amount = _decimal(fields.get('amount'))
                    group['cost_basis'] = _stored_number(amount)
                    changes.append(f'{account}：投入本金 {_text(previous)} → {_text(amount)} CNY；只核对本金，不改变现金或股票成本。')
                else:
                    amount = _decimal(fields.get('amount'), positive=True)
                    capital = fields.get('principal_cny')
                    if unit == 'CNY':
                        if capital not in (None, '') and _decimal(capital, positive=True) != amount:
                            raise PortfolioError('人民币转入或转出的本金必须与金额一致。')
                        capital = amount
                    else:
                        if capital in (None, ''):
                            raise PortfolioError('外币转入或转出请填写本次折合人民币本金；不会自动套用当前汇率。')
                        capital = _decimal(capital, positive=True)
                    sign = 1 if action == 'deposit' else -1
                    cash_before = balances.get(unit, Decimal(0))
                    balances[unit] = cash_before + sign * amount
                    group['cost_basis'] = _stored_number(previous + sign * capital)
                    self._set_cash(group, balances)
                    label = '转入' if action == 'deposit' else '转出'
                    changes.append(f'{account}：资金{label} {_text(amount)} {unit}，折合本金 {_text(capital)} CNY。')
                    changes.append(f'现金 {_text(cash_before)} → {_text(balances[unit])} {unit}；投入本金 {_text(previous)} → {_text(group["cost_basis"])} CNY。')
            elif action in {"set_cash", "set_fund"}:
                if any(fields.get(key) not in (None, "") for key in ("ticker", "quantity", "price", "fee", "name")):
                    raise PortfolioError("余额对账不能同时包含交易字段。")
                amount = _decimal(fields.get("amount"), nonnegative=action == "set_fund")
                if action == "set_fund":
                    if unit != "CNY":
                        raise PortfolioError("旧版基金合计估值仅支持 CNY。")
                    previous = _decimal(group.get("fund", 0))
                    group["fund"] = _stored_number(amount)
                    changes.append(f"{account}：基金合计估值 {_text(previous)} → {_text(amount)} CNY；不是申购或赎回。")
                else:
                    previous = balances.get(unit, Decimal(0))
                    balances[unit] = amount
                    self._set_cash(group, balances)
                    changes.append(f"{account}：现金 {_text(previous)} → {_text(amount)} {unit}；仅对账，不视为入金或出金。")
            else:
                if fields.get("amount") not in (None, ""):
                    raise PortfolioError("买卖请填写数量、成交价和费用，不要同时填写余额。")
                ticker = _ticker(fields.get("ticker"))
                if unit != _MARKETS[ticker.split(":")[0]]:
                    raise PortfolioError("成交币种与证券市场不一致。")
                quantity = _decimal(fields.get("quantity"), positive=True)
                price = _decimal(fields.get("price"), positive=True)
                fee = _decimal(fields.get("fee"), nonnegative=True)
                if ticker.split(":")[0] in {"SHA", "SHE", "HKG"} and quantity != quantity.to_integral_value():
                    raise PortfolioError("该市场的股票数量必须为整数股。")
                rows = group.get("positions")
                if not isinstance(rows, list):
                    raise PortfolioError("账户持仓结构无效，请在 QR 中检查。")
                matching = [row for row in rows if _ticker(row.get("ticker")) == ticker]
                if len(matching) > 1:
                    raise PortfolioError("账户内存在重复证券记录，请先在 QR 中合并核对。")
                position = matching[0] if matching else None
                old_quantity = _decimal(position.get("quantity"), nonnegative=True) if position else Decimal(0)
                old_cost = _decimal(position.get("cost_price", 0), nonnegative=True) if position else Decimal(0)
                cash_before = balances.get(unit, Decimal(0))
                if action == "sell":
                    if position is None or quantity > old_quantity:
                        raise PortfolioError("卖出数量超过当前持仓；请核对账户和证券。")
                    remaining = old_quantity - quantity
                    balances[unit] = cash_before + quantity * price - fee
                    if remaining == 0:
                        rows.remove(position)
                    else:
                        position["quantity"] = _stored_number(remaining)
                else:
                    cost = quantity * price + fee
                    if cost > cash_before and not allow_overdraft:
                        raise PortfolioError("对应币种现金不足；卡片不自动融资或换汇。")
                    new_quantity = old_quantity + quantity
                    average = (old_quantity * old_cost + cost) / new_quantity
                    # Existing accounting represents average cost at four decimal places.
                    average = average.quantize(Decimal("0.0001"))
                    if position is None:
                        name = fields.get("name") or ticker
                        if not isinstance(name, str) or len(name) > 120 or any(ord(ch) < 32 for ch in name):
                            raise PortfolioError("证券名称格式无效。")
                        position = {"ticker": ticker, "name": name}
                        rows.append(position)
                    position["quantity"] = _stored_number(new_quantity)
                    position["cost_price"] = _stored_number(average)
                    balances[unit] = cash_before - cost
                self._set_cash(group, balances)
                changes.append(f"{account}：{'买入' if action == 'buy' else '卖出'} {ticker} {_text(quantity)} 股，成交价 {_text(price)} {unit}，费用 {_text(fee)} {unit}。")
                changes.append(f"持仓数量 {_text(old_quantity)} → {_text(_decimal(position.get('quantity')) if position in rows else Decimal(0))} 股。")
                changes.append(f"对应现金 {_text(cash_before)} → {_text(balances[unit])} {unit}；同时记录交易与现金变化。")
        return changes

    def preview(self, fields: dict):
        if not isinstance(fields, dict):
            raise PortfolioError("表单内容无效。")
        day = _day(fields.get('date'))
        before, saved_day, fingerprint = self._baseline(day)
        after = copy.deepcopy(before)
        changes = self._apply_change(after, fields)
        return self._save_preview(after, day, saved_day, fingerprint, changes,
                                  fields.get('action', fields.get('operation')))

    def plan_batch(self, day, items):
        """Trades and capital flows retain their order; reconciliations are final balances."""
        day = _day(day)
        if not isinstance(items, list) or not items or len(items) > 30:
            raise PortfolioError("每次更新请添加 1–30 项变动。")
        before, saved_day, fingerprint = self._baseline(day)
        after = copy.deepcopy(before)
        reconciliations = set()
        for item in items:
            if not isinstance(item, dict) or item.get('operation') not in _ACTIONS - {'no_change'}:
                raise PortfolioError("变动清单中包含无效操作。")
            if item.get('operation') in {'set_cash', 'set_fund', 'set_cost_basis'}:
                key = (item.get('account'), item['operation'], item.get('currency'))
                if key in reconciliations:
                    raise PortfolioError("同一账户的同币种现金、基金估值或投入本金只需核对一次，请修改已有记录。")
                reconciliations.add(key)
        details = []
        ordered = [i for i in items if i['operation'] in {'buy', 'sell', 'deposit', 'withdraw'}]
        ordered += [i for i in items if i['operation'] in {'set_cash', 'set_fund', 'set_cost_basis'}]
        for item in ordered:
            fields = {key: value for key, value in item.items() if key != 'id'}
            fields['date'] = day
            try:
                # This records transactions already made; it does not place
                # orders. Negative cash remains explicit in the final preview.
                details.extend(self._apply_change(after, fields, allow_overdraft=True))
            except PortfolioError as exc:
                raise PortfolioError(f"第 {items.index(item) + 1} 项：{exc}") from None
        touched = list(dict.fromkeys(i['account'] for i in items))
        totals = []
        for account in touched:
            old, new = before['groups'][account], after['groups'][account]
            previous_cash, final_cash = self._cash(old), self._cash(new)
            lines = [f"{unit} 现金 {_text(previous_cash.get(unit, 0))} → {_text(final_cash.get(unit, 0))}"
                     for unit in sorted(set(previous_cash) | set(final_cash))]
            lines.append(f"CNY 基金估值 {_text(old.get('fund', 0))} → {_text(new.get('fund', 0))}")
            if 'cost_basis' in old or 'cost_basis' in new:
                lines.append(f"CNY 投入本金 {_text(old.get('cost_basis', 0))} → {_text(new.get('cost_basis', 0))}")
            totals.append(account + '：' + '；'.join(lines))
        untouched = [a for a in before['groups'] if a not in touched]
        changes = totals + details
        if any(value < 0 for account in touched for value in self._cash(after['groups'][account]).values()):
            changes.append('存在负现金余额，请核对是否与实际账户一致；可返回清单补充最终现金余额。')
        if untouched:
            changes.append('保持原样的账户：' + '、'.join(untouched))
        return {'after': after, 'day': day, 'saved_day': saved_day, 'fingerprint': fingerprint,
                'changes': changes, 'items': copy.deepcopy(items)}

    def preview_batch(self, day, items):
        plan = self.plan_batch(day, items)
        return self._save_preview(plan['after'], plan['day'], plan['saved_day'], plan['fingerprint'],
                                  plan['changes'], 'batch', plan['items'])

    def _save_preview(self, after, day, saved_day, fingerprint, changes, action, items=None):
        if action != "no_change" or day != saved_day:
            after["date"] = day
            after["updated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            if day != saved_day:
                after["cloned_from"] = saved_day
        after_bytes = (_encode(after) + "\n").encode()
        if action == 'no_change' and day == saved_day:
            # Preserve the existing file exactly, including its formatting.
            # A no-change update should only start the daily report pipeline.
            path = self.holdings_dir / (saved_day + '.json')
            after_bytes = path.read_bytes()
            current = hashlib.sha256(_encode({'latest': path.name, 'sha256': hashlib.sha256(after_bytes).hexdigest()}).encode()).hexdigest()
            if current != fingerprint:
                raise PortfolioError('持仓已经改变，请重新打开每日更新。')
        preview_id = uuid.uuid4().hex
        summary = f"{day} · {'组合无变化' if action == 'no_change' else '组合变更待确认'}"
        payload = {"id": preview_id, "date": day, "summary": summary, "changes": changes,
                   "fingerprint": fingerprint, "saved_day": saved_day, "after": after,
                   "after_sha256": hashlib.sha256(after_bytes).hexdigest(), "action": action, "items": items}
        with self._db() as db:
            db.execute("INSERT INTO previews VALUES (?,?,?)", (preview_id, time.time() + 1800, _encode(payload)))
        return {"id": preview_id, "summary": summary, "date": day, "changes": changes,
                "opaque_state": {"preview_id": preview_id}}

    def _write_holdings(self, day, payload):
        target = self.holdings_dir / (day + ".json")
        data = (_encode(payload) + "\n").encode()
        descriptor, temp = tempfile.mkstemp(prefix=".portfolio-", dir=self.holdings_dir)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
            directory = os.open(self.holdings_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _finish_prepared(self, operation):
        payload = json.loads(operation["payload"])
        target = self.holdings_dir / (payload["date"] + ".json")
        current_hash = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if current_hash != payload["after_sha256"]:
            _, _, fingerprint = self._baseline(payload["date"])
            if fingerprint != payload["fingerprint"]:
                raise PortfolioError("入账状态需要人工核对；未自动重复更新，请先在 QR 中检查。")
            self._write_holdings(payload["date"], payload["after"])
        with self._db() as db:
            db.execute("UPDATE operations SET status='committed' WHERE id=?", (operation["id"],))
        return json.loads(operation["receipt"])

    def operation_started(self, operation_id):
        with self._db() as db:
            return db.execute('SELECT 1 FROM operations WHERE id=?', (operation_id,)).fetchone() is not None

    def confirm(self, preview: dict, operation_id: str):
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}", operation_id):
            raise PortfolioError("确认请求标识无效，请刷新卡片。")
        preview_id = preview.get("id") if isinstance(preview, dict) else None
        if not isinstance(preview_id, str) or not re.fullmatch(r"[a-f0-9]{32}", preview_id):
            raise PortfolioError("预览已失效，请重新填写表单。")
        with self._lock():
            self._guard_legacy()
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                operation = db.execute("SELECT * FROM operations WHERE id=? OR preview_id=?", (operation_id, preview_id)).fetchall()
                if operation:
                    if len(operation) != 1 or operation[0]["preview_id"] != preview_id:
                        raise PortfolioError("确认请求已用于另一项变更，请重新预览。")
                    operation = dict(operation[0])
                else:
                    row = db.execute("SELECT * FROM previews WHERE id=?", (preview_id,)).fetchone()
                    if not row or row["expires"] < time.time():
                        raise PortfolioError("预览已过期，请刷新并重新确认。")
                    payload = json.loads(row["payload"])
                    _, _, fingerprint = self._baseline(payload["date"])
                    if fingerprint != payload["fingerprint"]:
                        raise PortfolioError("持仓在预览后发生了变化，请刷新并重新预览。")
                    receipt = {"reference": operation_id, "operation_id": operation_id,
                               "date": payload["date"], "summary": payload["summary"].replace("待确认", "已保存"),
                               "changes": payload["changes"], "report_status": "not_started"}
                    db.execute("INSERT INTO operations(id,preview_id,status,payload,receipt) VALUES (?,?,'prepared',?,?)",
                               (operation_id, preview_id, _encode(payload), _encode(receipt)))
                    operation = dict(db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone())
            if operation["status"] == "committed":
                return json.loads(operation["receipt"])
            return self._finish_prepared(operation)

    def _execute_pipeline(self, day, resume):
        # Call the report function directly: this avoids the CLI's clone path and
        # a nested acquisition of the shared financial-write lock.
        code = ("import sys; sys.path.insert(0, sys.argv[1]); "
                "import portfolio_daily_update as p; "
                "sys.exit(0 if p.run_pipeline(sys.argv[2], send_report=True, resume=sys.argv[3]=='1') else 1)")
        try:
            process = subprocess.run([sys.executable, "-c", code, str(self.scripts_dir), day, "1" if resume else "0"],
                                     cwd=self.scripts_dir, capture_output=True, timeout=900)
            return process.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def run_report(self, receipt: dict):
        reference = receipt.get("operation_id", receipt.get("reference")) if isinstance(receipt, dict) else None
        if not isinstance(reference, str):
            raise PortfolioError("缺少已确认的更新回执。")
        with self._lock():
            self._guard_legacy()
            with self._db() as db:
                row = db.execute("SELECT * FROM operations WHERE id=?", (reference,)).fetchone()
            if not row or row["status"] != "committed":
                raise PortfolioError("该更新尚未确认，不会生成或发送日报。")
            result = json.loads(row["receipt"])
            if row["report_status"] == "complete":
                return {**result, "report_status": "complete", "summary": "该次更新的日报已完成，无需重复发送。"}
            payload = json.loads(row["payload"])
            target = self.holdings_dir / (payload["date"] + ".json")
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != payload["after_sha256"]:
                raise PortfolioError("持仓已被后续更新改变，请使用最新更新的回执生成日报。")
            pipeline_file = self.portfolio_dir / "pipeline-state" / (payload["date"] + ".json")
            try:
                checkpoint = json.loads(pipeline_file.read_text()) if pipeline_file.is_file() else {}
            except (ValueError, OSError):
                raise PortfolioError("日报续跑记录无法读取，已暂停自动重发。") from None
            resume = bool(checkpoint) and checkpoint.get("holdings_hash") == payload["after_sha256"]
            if row["report_status"] in {"running", "pending"} and not resume:
                raise PortfolioError("上次日报执行状态需核查，缺少可安全续跑的记录；不会重复入账或自动重发。")
            with self._db() as db:
                db.execute("UPDATE operations SET report_status='running' WHERE id=?", (reference,))
            complete = self._execute_pipeline(payload["date"], resume)
            state = "complete" if complete else "pending"
            with self._db() as db:
                db.execute("UPDATE operations SET report_status=? WHERE id=?", (state, reference))
            if not complete:
                raise PortfolioError("组合已保存，日报尚未完成；可重试日报，不会重复买卖。")
            return {**result, "report_status": state,
                    "summary": "日报已完成。" if complete else "组合已保存，日报尚未完成；续跑不会重复买卖。"}

    def reports(self):
        items = []
        for path in sorted(self.reports_dir.glob("portfolio-*.md"), reverse=True):
            match = re.fullmatch(r"portfolio-(\d{4}-?\d{2}-?\d{2})\.md", path.name)
            if not match or path.is_symlink():
                continue
            raw_day = match[1].replace("-", "")
            day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"
            text = path.read_text(encoding="utf-8")
            # Show the report itself in private cards, with no local paths or
            # credentials from operational footers. Each card has a finite budget.
            from core.lab_service import safe_message
            text = safe_message(text)
            items.append({"date": day, "title": f"{day} 组合日报", "summary": text})
            if len(items) == 3:
                break
        return {"items": items, "summary": "最近已保存的组合日报；查看不会重新获取行情或发送通知。"}
