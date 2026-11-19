"""Compact Card 2.0 views for daily drafts.

A model with ``ledger: True`` comes from a portfolio whose transaction ledger is active: its list offers the
items the ledger can book (``LEDGER_OPERATIONS``) and its forms ask for what they need (an empty fee is
estimated, a 港股通 trade's CNY amount, 允许融资, a 差额说明). Without it every view is the holdings-file one.
"""
from .cards import (_button, _buttons, _escape, _field, _highlight, _input,
                    _markdown, _OPERATIONS, _plain, _preview_text, _select, _submit)


# The list items of a ledger, in menu order. ``correction`` only explains why the ledger refuses it.
LEDGER_OPERATIONS = {
    'buy': '买入', 'sell': '卖出',
    'deposit': '资金转入（增加本金）', 'withdraw': '资金转出（减少本金）',
    'set_cash': '现金对账（差额记一笔调整）', 'set_fund': '基金估值',
    'set_capital': '本金更新（按差额转入转出）', 'correction': '更正数量、成本或本金',
}
LEDGER_LABELS = {**_OPERATIONS, **LEDGER_OPERATIONS}


def start(model, context):
    count = len(model.get('items', []))
    return [
        _highlight(model['today'], '持仓、现金、基金和投入本金均无变化时，可直接更新日报。'),
        _buttons(_button(context, 'daily.unchanged', '今日无变动，更新日报', primary=not count, disabled=bool(count)),
                 _button(context, 'daily.batch', f'继续填写（{count} 项）' if count else '有变动', primary=bool(count))),
        _markdown('清单内有未保存的变动。' if count else '每项变动分别选账户，支持多个产品一起更新。', caption=True),
        _buttons(_button(context, 'assets', '查看资产'), _button(context, 'home', '返回主页')),
    ]


def batch(model, context):
    entries = model.get('entries', [])
    elements = [_highlight(model['date'] + ' · 变动清单', f'已添加 {len(entries)} 项，尚未入账。' if entries else '添加买卖、资金转入转出，或核对现金、基金、本金。')]
    if entries:
        elements.append(_markdown('\n'.join(f"{n}. {_escape(e['label'])}" for n, e in enumerate(entries, 1))))
    elements += [
        _buttons(_button(context, 'daily.add', '再添加一项' if entries else '添加变动', primary=not entries,
                         disabled=len(entries) >= 30),
                 _button(context, 'daily.batch.preview', '核对全部变动', primary=bool(entries), disabled=not entries)),
        _markdown('记入交易账本：转入转出同时调整现金和累计净投入；现金对账在其他各项之后按差额记一笔调整。'
                  if model.get('ledger') else '转入转出同时调整现金和本金。现金、基金、本金核对均以填写的最终金额为准。',
                  caption=True),
    ]
    if entries:
        elements.append({'tag': 'form', 'name': 'daily_manage_form', 'elements': [
            *_field('修改或删除', _select('item_id', entries, entries[-1]['value'], required=True)),
            _submit(context, 'daily.item.edit', '修改此项', primary=False),
            _submit(context, 'daily.item.remove', '删除此项', primary=False),
        ]})
    clear = _button(context, 'daily.discard', '清空清单', disabled=not entries)
    clear['confirm'] = {'title': _plain('清空变动清单'), 'text': _plain('删除尚未保存的变动，已入账记录保持原样。')}
    elements.append(_buttons(_button(context, 'daily.date', '修改日期'), clear,
                             _button(context, 'daily.open', '返回每日更新')))
    return elements


def choose(model, context):
    if model.get('ledger'):
        operations = [{'label': label, 'value': value} for value, label in LEDGER_OPERATIONS.items()]
    else:
        operations = [{'label': label, 'value': value} for value, label in _OPERATIONS.items() if value != 'no_change']
    return [
        _highlight('添加变动', '选择这项变动所属的账户。'),
        {'tag': 'form', 'name': 'daily_item_choose_form', 'elements': [
            *_field('账户', _select('account', model['accounts'], model['account'], required=True)),
            *_field('变动类型', _select('operation', operations, model['operation'], required=True)),
            _submit(context, 'daily.item.choose', '继续'),
        ]},
        _buttons(_button(context, 'daily.batch', '返回清单')),
    ]


def _currency(model):
    return _field('币种', _select('currency', [{'label': v, 'value': v} for v in ('CNY', 'HKD', 'USD')],
                                   model.get('currency', 'CNY'), required=True))


def ledger_item(model, context):
    """One list item of a ledger portfolio: only what the ledger books from it."""
    operation = model['operation']
    editing = bool(model['_selection'].get('editing'))
    title = model['account'] + ' · ' + LEDGER_LABELS[operation]
    account = next((a for a in model.get('accounts', []) if a['value'] == model['account']), {})
    principal = (' 账本累计净投入：' + str(account['cost_basis']) + ' CNY。') if 'cost_basis' in account else ''
    controls, disabled = [], False
    if operation in {'buy', 'sell'}:
        positions = model.get('positions', [])
        options = positions + ([{'label': '新产品（填写代码）', 'value': '__new__'}] if operation == 'buy' else [])
        controls += _field('产品', _select('product', options, model.get('product', ''), required=True))
        if operation == 'buy':
            controls.append(_input('new_ticker', '新产品代码（已有产品留空）', model.get('new_ticker', ''),
                                   placeholder='NASDAQ:AAPL / SHA:600000 / HKG:00700'))
        else:
            controls += _field('卖出数量', _select('quantity_mode', [
                {'label': '填写数量', 'value': 'partial'}, {'label': '全部卖出', 'value': 'all'},
            ], model.get('quantity_mode', 'partial'), required=True))
        controls += [
            _input('quantity', '数量（股；全部卖出留空）' if operation == 'sell' else '数量（股）', model.get('quantity', ''),
                   required=operation == 'buy'),
            _input('price', '成交单价（产品对应币种；港股为港币）', model.get('price', ''), required=True),
            _input('fee', '手续费（成交币种；留空按账户费率估算；港股通填人民币）', model.get('fee', '')),
            _input('settlement_cny', '港股通人民币成交金额（不含手续费；其他留空）', model.get('settlement_cny', '')),
        ]
        if operation == 'buy':
            controls += _field('现金不足时', _select('allow_margin', [
                {'label': '拒绝（默认）', 'value': 'no'}, {'label': '允许融资（现金可为负）', 'value': 'yes'},
            ], model.get('allow_margin', 'no'), required=True))
        note = ('按成交单记入账本。手续费留空时按账户费率估算并标明估算。港股通填写人民币成交金额和人民币手续费，'
                '记为换汇和港币成交，与导入交割单相同。')
        disabled = not options
    elif operation in {'deposit', 'withdraw'}:
        label = '本次转入金额' if operation == 'deposit' else '本次转出金额'
        controls.append(_input('amount', label, model.get('amount', ''), required=True))
        controls += _currency(model)
        controls.append(_input('principal_cny', '本次折合人民币本金（外币必填，人民币留空）', model.get('principal_cny', '')))
        note = '同时调整现金和累计净投入；外币按填写的人民币本金推算汇率。' + principal
    elif operation == 'set_capital':
        controls.append(_input('amount', '目标累计净投入（人民币元）', model.get('amount', ''), required=True))
        note = ('与账本累计净投入的差额记一笔人民币资金转入或转出，现金同时变化。只改本金、不改现金的更正账本不支持，'
                '请在网页 流水 冲销后重新录入。' + principal)
    elif operation == 'set_cash':
        controls.append(_input('amount', '现在的现金余额（所选币种）', model.get('amount', ''), required=True))
        controls += _currency(model)
        controls.append(_input('reconciliation_note', '差额说明（本清单同币种有资金转入转出时必填）',
                               model.get('reconciliation_note', '')))
        note = '在清单其他各项之后与账本余额比较：多出的记一笔利息、缺少的记一笔费用，附说明，不改变累计净投入。核对时先列出差额。'
    else:
        controls.append(_input('amount', '该账户基金合计估值（元）', model.get('amount', ''), required=True))
        note = '记一笔基金估值，只更新该账户基金合计，不改变现金。场内 ETF 买卖请使用买入或卖出。'
    controls.append(_submit(context, 'daily.item.save', '保存修改' if editing else '加入清单', disabled=disabled))
    return [
        _highlight(title, note),
        {'tag': 'form', 'name': 'daily_item_form', 'elements': controls},
        _buttons(_button(context, 'daily.batch', '返回清单')),
    ]


def item(model, context):
    if model.get('ledger'):
        return ledger_item(model, context)
    operation = model['operation']
    editing = bool(model['_selection'].get('editing'))
    title = model['account'] + ' · ' + _OPERATIONS[operation]
    controls = []
    if operation in {'buy', 'sell'}:
        positions = model.get('positions', [])
        options = positions + ([{'label': '新产品（填写代码）', 'value': '__new__'}] if operation == 'buy' else [])
        controls += _field('产品', _select('product', options, model.get('product', ''), required=True))
        if operation == 'buy':
            controls.append(_input('new_ticker', '新产品代码（已有产品留空）', model.get('new_ticker', ''),
                                   placeholder='NASDAQ:AAPL / SHA:600000'))
        if operation == 'sell':
            controls += _field('卖出数量', _select('quantity_mode', [
                {'label': '填写数量', 'value': 'partial'}, {'label': '全部卖出', 'value': 'all'},
            ], model.get('quantity_mode', 'partial'), required=True))
        controls += [
            _input('quantity', '数量（股；全部卖出留空）' if operation == 'sell' else '数量（股）', model.get('quantity', ''), required=operation == 'buy'),
            _input('price', '成交单价（产品对应币种）', model.get('price', ''), required=True),
            _input('fee', '手续费（同成交币种）', model.get('fee', '0')),
        ]
        note = '按已成交的数量、单价和手续费记录。'
        disabled = not options
    elif operation in {'deposit', 'withdraw', 'set_cost_basis'}:
        if operation == 'set_cost_basis':
            controls.append(_input('amount', '核对后的投入本金（人民币元）', model.get('amount', ''), required=True))
            note = '只更正账户投入本金，不改变现金或股票成本。现金已经更新过时可用此项；新转入资金请选“资金转入”。'
        else:
            label = '本次转入金额' if operation == 'deposit' else '本次转出金额'
            controls.append(_input('amount', label, model.get('amount', ''), required=True))
            controls += _field('币种', _select('currency', [{'label': v, 'value': v} for v in ('CNY', 'HKD', 'USD')], model.get('currency', 'CNY'), required=True))
            controls.append(_input('principal_cny', '本次折合人民币本金（外币必填，人民币留空）', model.get('principal_cny', '')))
            note = '填写这次转入或转出的金额，会同时调整现金和投入本金。已填现金对账时，以对账后的余额为准，不重复增减现金。'
        account = next((a for a in model.get('accounts', []) if a['value'] == model['account']), {})
        if 'cost_basis' in account:
            note += ' 已保存投入本金：' + str(account['cost_basis']) + ' CNY。'
        disabled = False
    else:
        label = '核对后现金余额（所选币种）' if operation == 'set_cash' else '该账户基金合计估值（元）'
        controls.append(_input('amount', label, model.get('amount', ''), required=True))
        if operation == 'set_cash':
            controls += _field('币种', _select('currency', [{'label': v, 'value': v} for v in ('CNY', 'HKD', 'USD')], model.get('currency', 'CNY'), required=True))
        note = '填写最终余额，会覆盖本清单买卖后计算的现金。' if operation == 'set_cash' else '只更新该账户基金合计估值，不改变现金。场内 ETF 买卖请使用买入或卖出。'
        disabled = False
    controls.append(_submit(context, 'daily.item.save', '保存修改' if editing else '加入清单', disabled=disabled))
    return [
        _highlight(title, note),
        {'tag': 'form', 'name': 'daily_item_form', 'elements': controls},
        _buttons(_button(context, 'daily.batch', '返回清单')),
    ]


def change_date(model, context):
    return [
        _highlight('记账日期', '同一清单的变动使用同一个日期。'),
        {'tag': 'form', 'name': 'daily_date_form', 'elements': [
            {'tag': 'date_picker', 'name': 'date', 'required': True, 'width': 'fill', 'initial_date': model['date']},
            _submit(context, 'daily.date.save', '保存日期'),
        ]},
        _buttons(_button(context, 'daily.batch', '返回清单')),
    ]


def preview(model, context):
    if model.get('ledger'):
        return [
            _highlight('核对账本入账', '以下是账本对这份清单的预览，尚未入账。确认后按这份预览写入交易账本（与网页“确认入账”相同），'
                                   '并更新日报；账本在此之后有变化时会重新生成预览。'),
            *_preview_text(model),
            _buttons(_button(context, 'daily.batch.confirm', '确认入账并更新日报', primary=True, confirm=True),
                     _button(context, 'daily.batch', '返回修改')),
        ]
    return [
        _highlight('核对全部变动', '请核对买卖数量及每个账户的最终现金、基金估值、投入本金。'),
        *_preview_text(model),
        _buttons(_button(context, 'daily.batch.confirm', '保存全部变动并更新日报', primary=True, confirm=True),
                 _button(context, 'daily.batch', '返回修改')),
    ]


def pending(model, context):
    return [
        _highlight('上次更新尚未处理完', '可继续处理同一次更新。清单暂时保留，买卖不会重复入账。'),
        _buttons(_button(context, 'daily.commit.retry', '继续处理', primary=True),
                 _button(context, 'daily.batch', '刷新状态'), _button(context, 'home', '返回主页')),
    ]


VIEWS = {
    'daily_start': ('每日更新', start), 'daily_batch': ('变动清单', batch),
    'daily_item_choose': ('添加变动', choose), 'daily_item_form': ('填写变动', item),
    'daily_date': ('修改日期', change_date), 'daily_batch_preview': ('确认更新', preview),
    'daily_pending': ('更新状态', pending),
}
