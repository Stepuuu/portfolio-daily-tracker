"""Daily batch workflow; draft edits never touch the portfolio or run reports."""
import copy
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from .drafts import Drafts
from .portfolio import PortfolioError, _day, _ticker, _MARKETS
from .store import Rejected


ACTIONS = ('daily.unchanged', 'daily.batch', 'daily.add', 'daily.item.choose',
           'daily.item.save', 'daily.item.edit', 'daily.item.remove', 'daily.discard',
           'daily.date', 'daily.date.save', 'daily.batch.preview', 'daily.batch.confirm',
           'daily.commit.retry')


def today():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def item_label(item):
    labels = {'buy': '买入', 'sell': '卖出', 'set_cash': '现金余额', 'set_fund': '基金估值'}
    detail = (f"{item.get('ticker', '')} · {item.get('quantity', '')} 股 × {item.get('price', '')}"
              if item['operation'] in {'buy', 'sell'} else item.get('amount', ''))
    return f"{item['account']} · {labels[item['operation']]} · {detail} {item['currency']}"


class DailyWorkflow:
    def __init__(self, workbench):
        self.w = workbench
        self.drafts = Drafts(workbench.store)

    def model(self, draft):
        overview = self.w.portfolio.overview()
        return {**draft, 'accounts': overview.get('accounts', []),
                'entries': [{'label': item_label(i), 'value': i['id']} for i in draft['items']],
                '_draft_revision': draft['revision']}

    def render_draft(self, command, draft):
        return self.w.render(command, 'daily_pending' if draft['locked_by'] else 'daily_batch', self.model(draft))

    def verify_revision(self, draft, previous):
        if draft['locked_by']:
            raise Rejected('上次更新尚未处理完，请打开变动清单查看。')
        if draft['revision'] != previous.get('_draft_revision'):
            raise Rejected('清单已在其他卡片中更新，请重新打开变动清单。')

    def positions(self, draft, account, editing=''):
        items = draft['items']
        if editing:
            index = next((i for i, item in enumerate(items) if item['id'] == editing), None)
            if index is None:
                raise Rejected('该记录已被移除，请重新打开清单。')
            items = items[:index]
        if items:
            data = self.w.portfolio.plan_batch(draft['date'], items)['after']
        else:
            data = self.w.portfolio._baseline(draft['date'])[0]
        return [{'label': f"{p.get('name') or p['ticker']} · {p['ticker']} · {p['quantity']} 股",
                 'value': p['ticker'], 'quantity': str(p['quantity'])}
                for p in data['groups'][account].get('positions', [])]

    def item_form(self, draft, selection, item=None):
        model = {**self.model(draft), **selection, '_selection': selection,
                 'fee': '0', 'currency': 'CNY', 'quantity_mode': 'partial'}
        if item:
            model.update(item)
            model['product'] = item.get('ticker', '')
        if selection['operation'] in {'buy', 'sell'}:
            model['positions'] = self.positions(draft, selection['account'], selection.get('editing', ''))
            if item and item.get('ticker') not in {p['value'] for p in model['positions']}:
                model['product'], model['new_ticker'] = '__new__', item['ticker']
            elif not item:
                model['product'] = model['positions'][0]['value'] if model['positions'] else '__new__'
        return model

    def save(self, command, actor, draft, previous, items, day=None):
        self.verify_revision(draft, previous)
        day = _day(day or draft['date'])
        if items:
            self.w.portfolio.plan_batch(day, items)
        else:
            self.w.portfolio._baseline(day)
        saved = self.drafts.save(actor, draft['revision'], command['id'], day, items)
        return self.render_draft(command, saved)

    def finish_update(self, command, actor, commit):
        if commit['status'] == 'rejected':
            raise Rejected('该次更新未入账，请回到清单重新核对。')
        try:
            receipt = self.w.portfolio.confirm(commit['preview'], commit['id'])
        except PortfolioError:
            if not self.w.portfolio.operation_started(commit['id']):
                self.drafts.release_unwritten(actor, commit['id'])
            raise
        self.w.store.enqueue_report(command['session'], receipt, commit['id'])
        self.drafts.complete(actor, commit['id'])
        # Keep the detailed figures on the preview; the receipt is a short status.
        return self.w.render(command, 'daily_receipt', {
            'summary': receipt['date'] + ' · 组合记录已保存',
            'notice': '日报正在更新，可从“日报”查看进度。', 'reference': receipt['reference']})

    def execute(self, command):
        action, payload = command['action'], command['payload']
        previous, fields = payload.get('model', {}), payload.get('fields', {})
        actor = self.w.store.session(command['session'])['actor']
        draft = self.drafts.get(actor, today())
        if action == 'daily.open' and not draft['items'] and not draft['locked_by'] and draft['date'] != today():
            draft = self.drafts.save(actor, draft['revision'], command['id'] + '_date', today(), [])
        if action in {'daily.open', 'daily.batch'}:
            if action == 'daily.batch' or draft['locked_by']:
                return self.render_draft(command, draft)
            return self.w.render(command, 'daily_start', {**self.model(draft), 'today': today()})
        if action in {'daily.item.save', 'daily.item.remove', 'daily.discard', 'daily.date.save'}:
            edited = self.drafts.edited(actor, command['id'])
            if edited:
                return self.render_draft(command, edited)
        if action == 'daily.unchanged':
            commit = self.drafts.commit(actor, command['id'])
            if not commit:
                self.verify_revision(draft, previous)
                if draft['items']:
                    raise Rejected('清单里还有未保存的变动，请先处理或清空清单。')
                if fields or previous.get('today') != today():
                    raise Rejected('日期已改变，请重新打开每日更新。')
                preview = self.w.portfolio.preview({'operation': 'no_change', 'date': today()})
                commit = self.drafts.begin(actor, draft['revision'], command['id'], preview)
            return self.finish_update(command, actor, commit)
        if action == 'daily.batch.confirm':
            commit = self.drafts.commit(actor, command['id'])
            if not commit:
                if payload.get('view') != 'daily_batch_preview' or not previous.get('_preview'):
                    raise Rejected('请先核对变动清单。')
                self.verify_revision(draft, previous)
                commit = self.drafts.begin(actor, draft['revision'], command['id'], previous['_preview'])
            return self.finish_update(command, actor, commit)
        if action == 'daily.commit.retry':
            key = previous.get('locked_by')
            commit = self.drafts.commit(actor, key) if key else None
            if not commit or (draft['locked_by'] != key and commit['status'] != 'complete'):
                raise Rejected('请重新打开变动清单查看最新状态。')
            return self.finish_update(command, actor, commit)
        self.verify_revision(draft, previous)
        if action == 'daily.add':
            if len(draft['items']) >= 30:
                raise Rejected('清单最多容纳 30 项变动，请先保存当前清单。')
            model = self.model(draft)
            if not model['accounts']:
                raise Rejected('请先在 QR 中完成组合初始化。')
            model['account'] = draft['items'][-1]['account'] if draft['items'] else model['accounts'][0]['value']
            model['operation'] = 'sell'
            return self.w.render(command, 'daily_item_choose', model)
        if action == 'daily.item.choose':
            if set(fields) != {'account', 'operation'}:
                raise Rejected('请选择账户和变动类型。')
            if fields['account'] not in {a['value'] for a in self.model(draft)['accounts']} or fields['operation'] not in {'buy', 'sell', 'set_cash', 'set_fund'}:
                raise Rejected('请选择有效的账户和变动类型。')
            return self.w.render(command, 'daily_item_form', self.item_form(draft, dict(fields)))
        if action in {'daily.item.edit', 'daily.item.remove'}:
            item = next((i for i in draft['items'] if i['id'] == fields.get('item_id')), None)
            if not item or set(fields) != {'item_id'}:
                raise Rejected('请选择清单中的一项记录。')
            if action == 'daily.item.remove':
                return self.save(command, actor, draft, previous, [i for i in draft['items'] if i['id'] != item['id']])
            selection = {'account': item['account'], 'operation': item['operation'], 'editing': item['id']}
            return self.w.render(command, 'daily_item_form', self.item_form(draft, selection, item))
        if action == 'daily.item.save':
            selection = previous.get('_selection', {})
            if payload.get('view') != 'daily_item_form' or not selection:
                raise Rejected('请先选择变动类型。')
            try:
                item = self.parse_item(draft, selection, fields, command['id'])
                items = copy.deepcopy(draft['items'])
                editing = selection.get('editing')
                if editing:
                    index = next((i for i, old in enumerate(items) if old['id'] == editing), None)
                    if index is None:
                        raise Rejected('该记录已被移除。')
                    items[index] = item
                else:
                    items.append(item)
                return self.save(command, actor, draft, previous, items)
            except (PortfolioError, Rejected) as exc:
                return self.w.render(command, 'daily_item_form', {**previous, **fields, 'notice': str(exc)})
        if action == 'daily.discard':
            return self.save(command, actor, draft, previous, [])
        if action == 'daily.date':
            return self.w.render(command, 'daily_date', self.model(draft))
        if action == 'daily.date.save':
            if set(fields) != {'date'}:
                raise Rejected('请选择记账日期。')
            return self.save(command, actor, draft, previous, draft['items'], fields['date'])
        if action == 'daily.batch.preview':
            preview = self.w.portfolio.preview_batch(draft['date'], draft['items'])
            return self.w.render(command, 'daily_batch_preview', {
                **preview, '_preview': preview, '_draft_revision': draft['revision']})
        raise Rejected('未知的每日更新操作。')

    def parse_item(self, draft, selection, fields, key):
        operation = selection['operation']
        allowed = ({'product', 'new_ticker', 'quantity', 'quantity_mode', 'price', 'fee'}
                   if operation in {'buy', 'sell'} else {'amount', 'currency'})
        if set(fields) - allowed:
            raise Rejected('表单包含不支持的字段，请重新选择变动类型。')
        result = {k: selection[k] for k in ('account', 'operation')}
        result['id'] = selection.get('editing') or hashlib.sha256(key.encode()).hexdigest()[:24]
        if operation in {'buy', 'sell'}:
            positions = self.positions(draft, selection['account'], selection.get('editing', ''))
            chosen = fields.get('product')
            if chosen == '__new__' and operation == 'buy':
                ticker = _ticker(fields.get('new_ticker'))
            else:
                if chosen not in {p['value'] for p in positions}:
                    raise Rejected('请从该账户的产品列表中选择。')
                if fields.get('new_ticker', '').strip():
                    raise Rejected('已有产品无需填写新代码；买入新产品请先选择“新产品”。')
                ticker = _ticker(chosen)
            mode = fields.get('quantity_mode', 'partial')
            if mode not in {'partial', 'all'} or (mode == 'all' and operation != 'sell'):
                raise Rejected('无效的卖出数量选项。')
            quantity = fields.get('quantity', '').strip()
            if mode == 'all':
                if quantity:
                    raise Rejected('全部卖出无需填写数量；指定数量请改选“填写数量”。')
                quantity = next(p['quantity'] for p in positions if p['value'] == ticker)
            result.update(ticker=ticker, quantity=quantity, price=fields.get('price', ''),
                          fee=fields.get('fee', '').strip() or '0', currency=_MARKETS[ticker.split(':')[0]])
        else:
            result.update(amount=fields.get('amount', ''), currency='CNY' if operation == 'set_fund' else fields.get('currency'))
        return result
