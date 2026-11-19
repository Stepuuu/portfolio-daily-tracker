"""The first-run checklist (``GET /api/setup/status``) and the wizard's two flags.

Each item is ``{id, label, state, detail, action}``:

``state``
    ``done`` (nothing left to do), ``todo`` (needed for the daily desk),
    ``optional`` (the desk works without it) or ``unavailable`` (does not
    apply in this mode, or could not be checked now; ``detail`` says which).
``detail``
    one Chinese sentence about the real current state.
``action``
    ``None``, ``{label, href}`` (a page of the app) or ``{label, command}``:
    something configured outside the app, with the exact line to use
    (environment variables are read from the process environment or the
    repository's ``.env``, which ``config.settings`` loads at start-up).

Items, in order: ``opening`` (the ledger has an opening balance), ``legacy``
(a v3.2 holdings file not migrated), ``fees`` (every account's fee profile
saved once), ``quotes`` (one quote through the market runtime, bounded by
``QUOTE_PROBE_SECONDS``; scripted quotes and the demo say so instead),
``alerts`` (the alert monitor), ``routines`` (the background scheduler, the
close snapshot and the briefs with their switches and times), ``notifications``
(push channels besides the bell) and ``llm`` (the chat model; optional).

Reading never creates or changes a file: the ledger is opened read-only, the
desk store only when it exists, the configuration file only when it exists.
The flags (``setup_complete``, ``dismissed``) live in ``desk.sqlite3``, table
``setup_state``, and are written only by ``mark()``.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Dict, List, Optional

from core import desk_store

logger = logging.getLogger(__name__)

SCHEMA = 'setup_state'
MIGRATIONS = [[
    'CREATE TABLE setup_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)',
]]
desk_store.register_schema(SCHEMA, MIGRATIONS)

FLAGS = ('completed', 'dismissed')
QUOTE_PROBE_SECONDS = 4
PROBE_TICKER = 'SHA:600519'
SOURCE_NAMES = {'eastmoney': '东方财富', 'yahoo': 'Yahoo', 'fake': '脚本行情'}
_probe_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='setup-quote-probe')

DONE, TODO, OPTIONAL, UNAVAILABLE = 'done', 'todo', 'optional', 'unavailable'


def mode() -> str:
    if os.environ.get('TRACKER_LAB_ONLY') == '1':
        return 'lab'
    return 'demo' if os.environ.get('TRACKER_DEMO_MODE') == '1' else 'full'


# -- flags ------------------------------------------------------------------------

def flags() -> dict:
    """``{setup_complete, completed_at, dismissed, dismissed_at}``; never creates the store."""
    stored: Dict[str, str] = {}
    if desk_store.ready(SCHEMA, MIGRATIONS):
        with desk_store.connect() as db:
            stored = {row['key']: row['updated_at'] for row in db.execute('SELECT key, updated_at FROM setup_state')}
    return {'setup_complete': 'completed' in stored, 'completed_at': stored.get('completed'),
            'dismissed': 'dismissed' in stored, 'dismissed_at': stored.get('dismissed')}


def mark(flag: str) -> dict:
    """Set ``completed`` or ``dismissed`` (again: the time is updated). Returns ``flags()``."""
    if flag not in FLAGS:
        raise ValueError(flag)
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        db.execute('INSERT INTO setup_state(key, value, updated_at) VALUES (?, ?, ?) '
                   'ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at',
                   (flag, '1', desk_store.now()))
    return flags()


# -- items --------------------------------------------------------------------------

def item(id: str, label: str, state: str, detail: str, action: Optional[dict] = None) -> dict:
    return {'id': id, 'label': label, 'state': state, 'detail': detail, 'action': action}


def link(label: str, href: str) -> dict:
    return {'label': label, 'href': href}


def command(label: str, text: str) -> dict:
    return {'label': label, 'command': text}


class _Ledger:
    """The confirmed ledger, read once per status: ``state`` (None without one) or ``error``, the text shown
    when it cannot be read now (the ledger's own fixed reason for ``LedgerUnavailable``)."""

    def __init__(self):
        from core.ledger import LedgerUnavailable, confirmed_state
        self.state, self.error = None, None
        try:
            self.state = confirmed_state(timeout=2)
        except Exception as exc:
            logger.warning('setup status could not read the ledger: %s', type(exc).__name__)
            reason = exc.reason if isinstance(exc, LedgerUnavailable) else '请稍后重试'
            self.error = f'暂时读不到账本：{reason}。'

    @property
    def accounts(self) -> List[str]:
        return list((self.state or {}).get('accounts', {}))


def opening_item(ledger: _Ledger) -> dict:
    label = '账户与期初余额'
    if ledger.error:
        return item('opening', label, UNAVAILABLE, ledger.error)
    if ledger.accounts:
        names = '、'.join(ledger.accounts[:3]) + (f' 等 {len(ledger.accounts)} 个' if len(ledger.accounts) > 3 else '')
        return item('opening', label, DONE, f'已有期初余额：{names}（账本版本 {ledger.state["revision"]}）。',
                    link('查看账本', '/ledger'))
    return item('opening', label, TODO, '还没有期初余额：新建账户、导入券商文件或迁移旧版持仓后，今日页才会显示持仓。',
                link('设置期初', '/setup?step=account'))


def legacy_item(ledger: _Ledger) -> dict:
    from core import legacy_migration
    label = '旧版持仓文件'
    found = legacy_migration.find()
    if not found:
        return item('legacy', label, DONE, '没有发现旧版持仓文件，无需迁移。')
    files = '、'.join(f'{entry["file"]}' + (f'（共 {entry["count"]} 个日期）' if entry['count'] > 1 else '')
                     for entry in found)
    try:
        if ledger.error:
            raise RuntimeError('ledger unavailable')
        moved = legacy_migration.migrated()
        left = legacy_migration.pending(set(ledger.accounts), moved)
    except Exception as exc:
        logger.warning('setup status could not read the ledger events: %s', type(exc).__name__)
        return item('legacy', label, UNAVAILABLE, f'发现 {files}，暂时读不到账本，无法确认是否已迁移。')
    if not left:
        if moved:
            accounts = '、'.join(sorted({entry['account'] for entry in moved}))
            return item('legacy', label, DONE, f'已迁移到账本（{accounts}，期初日期 {moved[0]["date"]}）；原文件保持不变，不再读取。',
                        link('查看账本', '/ledger'))
        return item('legacy', label, DONE, f'发现 {files}，其中的账户都已在账本中，无需迁移；原文件保持不变，不再读取。')
    # Every source or account not migrated yet stays on offer, also after another one was opened or migrated.
    rest = '；'.join(entry['file'] + (f'（{"、".join(entry["accounts"])}）' if entry['accounts'] else '') for entry in left)
    if ledger.accounts:
        return item('legacy', label, OPTIONAL, f'还没有迁移：{rest}。账本已经启用，旧文件不会再被读取；需要时可以把这些账户迁移到账本。',
                    link('迁移旧版持仓', '/setup?step=account&start=legacy'))
    return item('legacy', label, TODO, f'发现 {files}，还没有迁移到账本。', link('预览迁移', '/setup?step=account&start=legacy'))


def fees_item(ledger: _Ledger) -> dict:
    from core import account_profiles
    label = '账户费率'
    if ledger.error:
        return item('fees', label, UNAVAILABLE, ledger.error)
    if not ledger.accounts:
        return item('fees', label, TODO, '建立账户后，核对它的费率：记一笔会按费率预填费用估算。',
                    link('设置费率', '/setup?step=fees'))
    saved = {profile['account'] for profile in account_profiles.list()}
    missing = [name for name in ledger.accounts if name not in saved]
    if not missing:
        return item('fees', label, DONE, f'{len(ledger.accounts)} 个账户的费率都已核对保存。', link('修改费率', '/setup?step=fees'))
    return item('fees', label, TODO, f'还没有核对：{"、".join(missing[:3])}{" 等" if len(missing) > 3 else ""}。'
                                     '未核对的账户按预设（佣金万2.5、最低 5 元）估算。',
                link('核对费率', '/setup?step=fees'))


def _quote_time(quote) -> str:
    from core.market.calendar import SHANGHAI
    moment = quote.quote_time or quote.fetched_at
    return moment.astimezone(SHANGHAI).strftime('%H:%M:%S') if moment.tzinfo else moment.strftime('%H:%M:%S')


def quotes_item(ledger: _Ledger, probe: bool) -> dict:
    from core.market.runtime import fake_requested, get_market_runtime
    label = '行情源'
    current = mode()
    if current == 'demo':
        return item('quotes', label, DONE, '演示模式使用虚构行情（fake_quotes.json），不连接真实行情源。')
    if fake_requested():
        return item('quotes', label, DONE, '使用脚本行情（TRACKER_QUOTE_SOURCE=fake）：读取 fake_quotes.json，不连接真实行情源。',
                    command('改用真实行情：去掉这一行后重启后端', 'TRACKER_QUOTE_SOURCE=fake'))
    if not probe:
        return item('quotes', label, UNAVAILABLE, '这次没有检查行情源。')
    held = [ticker for account in (ledger.state or {}).get('accounts', {}).values() for ticker in account['positions']]
    ticker = held[0] if held else PROBE_TICKER
    runtime = get_market_runtime()
    # Without the quote service's lock (QuoteService.probe): the Today page and the alert monitor never
    # wait for this check, and it never waits for them.
    future = _probe_pool.submit(runtime.quotes.probe, ticker)
    try:
        quote = future.result(timeout=QUOTE_PROBE_SECONDS)
    except FutureTimeout:
        return item('quotes', label, TODO, f'行情源在 {QUOTE_PROBE_SECONDS} 秒内没有回应。检查网络或代理后刷新这一项。',
                    command('东方财富需要走代理时', 'TRACKER_EASTMONEY_PROXY=1'))
    except Exception as exc:
        logger.warning('setup quote probe failed: %s', type(exc).__name__)
        return item('quotes', label, TODO, '行情源检查出错。请稍后刷新这一项。')
    source = '、'.join(SOURCE_NAMES.get(part, part) for part in str(quote.source).split('+'))
    if quote.usable:
        return item('quotes', label, DONE, f'{source}可用：{ticker} 的行情时间 {_quote_time(quote)}。')
    return item('quotes', label, TODO, f'行情源暂时取不到 {ticker} 的价格：{quote.reason or "没有返回价格"}。'
                                       '今日页会把缺价的持仓标为不可用。',
                command('只用 Yahoo 行情时', 'TRACKER_QUOTE_SOURCE=yahoo'))


def alerts_item() -> dict:
    from core.alerts import monitor
    from core.market.runtime import get_market_runtime
    label = '价格提醒监控'
    current = mode()
    if current == 'demo':
        return item('alerts', label, UNAVAILABLE, '演示模式不运行提醒监控。')
    if not monitor.enabled():
        return item('alerts', label, TODO, f'已关闭（{monitor.ENV_ENABLED}=0）：提醒规则不会被检查。',
                    command('打开监控：删掉这一行或改为 1，然后重启后端', f'{monitor.ENV_ENABLED}=1'))
    status = monitor.status(get_market_runtime())
    if status.get('running'):
        return item('alerts', label, DONE, '运行中：只在交易时段检查已启用的规则；后端关闭时不会提醒。', link('查看提醒', '/alerts'))
    return item('alerts', label, TODO, '没有运行：监控随后端启动，只在后端运行期间检查规则。',
                command('启动后端（make start）后自动运行', 'make start'))


def routines_item() -> dict:
    from core.routines import briefs, scheduler
    from core.routines import close_snapshot
    label = '每日例行任务'
    snapshot_on = os.environ.get(close_snapshot.DISABLE_ENV, '').strip().lower() not in {'0', 'off', 'false', 'no'}
    times = briefs.settings()
    parts = [f'收盘快照 {close_snapshot.DUE_TIME:%H:%M} {"开" if snapshot_on else "关"}']
    for kind in briefs.KINDS:
        entry = times[kind]
        parts.append(f'{briefs.TITLES[kind]} {entry["time"]} {"开" if entry["enabled"] else "关"}')
    summary = ' · '.join(parts)
    problems = [times[kind]['problem'] for kind in briefs.KINDS if times[kind].get('problem')]
    # One variable per line, as they go into .env.
    lines = '\n'.join([f'{close_snapshot.DISABLE_ENV}={"1" if snapshot_on else "0"}']
                      + [line for kind in briefs.KINDS for line in (
                          f'{times[kind]["env"]["enabled"]}={"1" if times[kind]["enabled"] else "0"}',
                          f'{times[kind]["env"]["time"]}={times[kind]["time"]}')])
    current = mode()
    if current == 'demo':
        return item('routines', label, UNAVAILABLE, f'演示模式不运行例行任务。设置：{summary}。')
    running = scheduler.current()
    if running is None or not running.alive():
        return item('routines', label, TODO, f'后台调度器没有运行：例行任务只在后端运行期间执行。设置：{summary}。',
                    command('启动后端（make start）；时间和开关写在仓库根目录的 .env', lines))
    detail = f'调度器运行中。{summary}。' + (' ' + '；'.join(problems) if problems else '')
    return item('routines', label, DONE, detail, command('修改时间或开关：写在仓库根目录的 .env，然后重启后端', lines))


def notifications_item() -> dict:
    from core import notify
    label = '通知渠道'
    channels = list(notify.delivery_stats())
    feishu = _feishu_report_configured()
    pushed = []
    if channels:
        pushed.append(f'推送渠道 {"、".join(channels)}')
    if feishu:
        pushed.append('收盘日报推送到飞书')
    if pushed:
        return item('notifications', label, DONE, f'站内通知（右上角铃铛）始终可用；另外已配置：{"；".join(pushed)}。')
    return item('notifications', label, OPTIONAL, '站内通知（右上角铃铛）始终可用；没有配置外部推送（可选，'
                                                  '飞书推送见 docs/FEISHU_WORKBENCH.md）。')


def _feishu_report_configured() -> bool:
    """Whether the portfolio's ``config.json`` names a Feishu chat for the daily report (its value is not read out)."""
    import json
    from core.ledger import portfolio_directory
    path = portfolio_directory() / 'config.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(str(data.get('feishu_chat_id') or '').strip())


def llm_item() -> dict:
    from core import llm_setup
    label = 'AI 助手（可选）'
    summary = llm_setup.summary()
    needs = '对话助手、简报的模型点评和截图识别需要它；记账、行情和提醒不需要。'
    if summary['configured']:
        return item('llm', label, DONE, f'{summary["text"]}。', link('修改模型', '/setup?step=ai'))
    return item('llm', label, OPTIONAL, f'{summary["text"]}。{needs}', link('设置模型', '/setup?step=ai'))


def _safely(build: Callable[[], dict], id: str, label: str) -> dict:
    try:
        return build()
    except Exception as exc:
        logger.warning('setup status item %s failed: %s', id, type(exc).__name__)
        return item(id, label, UNAVAILABLE, '暂时无法检查这一项，请稍后刷新。')


LAB_REASON = '研究模式（TRACKER_LAB_ONLY=1）只有研究工作台和设置，不使用账本。'
LABELS = {'opening': '账户与期初余额', 'legacy': '旧版持仓文件', 'fees': '账户费率', 'quotes': '行情源',
          'alerts': '价格提醒监控', 'routines': '每日例行任务', 'notifications': '通知渠道', 'llm': 'AI 助手（可选）'}


def items(probe_quotes: bool = True) -> List[dict]:
    if mode() == 'lab':
        out = [item(id, label, UNAVAILABLE, LAB_REASON) for id, label in LABELS.items() if id != 'llm']
        return out + [_safely(llm_item, 'llm', LABELS['llm'])]
    ledger = _Ledger()
    builders = [
        ('opening', lambda: opening_item(ledger)),
        ('legacy', lambda: legacy_item(ledger)),
        ('fees', lambda: fees_item(ledger)),
        ('quotes', lambda: quotes_item(ledger, probe_quotes)),
        ('alerts', alerts_item),
        ('routines', routines_item),
        ('notifications', notifications_item),
        ('llm', llm_item),
    ]
    return [_safely(build, id, LABELS[id]) for id, build in builders]


def status(probe_quotes: bool = True) -> dict:
    out = {'mode': mode(), 'items': items(probe_quotes)}
    try:
        out.update(flags())
    except Exception as exc:
        logger.warning('setup flags could not be read: %s', type(exc).__name__)
        out.update({'setup_complete': None, 'completed_at': None, 'dismissed': None, 'dismissed_at': None})
    opening = next(entry for entry in out['items'] if entry['id'] == 'opening')
    out['ledger_ready'] = None if opening['state'] == UNAVAILABLE and out['mode'] != 'lab' else opening['state'] == DONE
    return out
