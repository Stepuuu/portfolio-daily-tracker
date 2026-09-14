"""Compact Card 2.0 views for daily drafts."""
from .cards import (_button, _buttons, _escape, _field, _highlight, _input,
                    _markdown, _OPERATIONS, _plain, _preview_text, _select, _submit)


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
        _markdown('转入转出同时调整现金和本金。现金、基金、本金核对均以填写的最终金额为准。', caption=True),
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


def item(model, context):
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
