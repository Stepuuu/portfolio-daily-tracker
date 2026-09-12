"""Authenticated card commands, durable execution, and existing QR adapters."""
import asyncio
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from .cards import CardContext, build_card
from .clients import DeliveryError
from .daily import DailyWorkflow, ACTIONS as DAILY_ACTIONS
from .store import Rejected, Store, encode


_KEEP_RETRYING = object()

ACTIONS = ('home', 'daily.open', 'daily.preview', 'daily.confirm', 'assets',
           'research.open', 'research.preview', 'research.start', 'research.list',
           'research.cancel', 'research.refresh', 'reports')
ACTIONS += ('daily.report.retry', 'research.select', 'daily.choose') + DAILY_ACTIONS


def toast(content, kind='info'):
    return {'toast': {'type': kind, 'content': content}}


def normalize(envelope):
    kind = envelope.get('event_type')
    raw = envelope.get('event', {})
    if not isinstance(raw, dict):
        raise Rejected('无效的操作。')
    body = raw.get('event', raw)
    header = raw.get('header', raw)
    result = {'kind': kind, 'id': header.get('event_id', ''), 'body': body}
    if kind == 'card.action.trigger':
        action = body.get('action', {})
        context = body.get('context', {})
        name = action.get('name', '')
        value = action.get('value', {})
        if not isinstance(value, dict):
            value = {}
        result.update(actor=body.get('operator', {}).get('open_id'),
                      chat=context.get('open_chat_id'), message=context.get('open_message_id'),
                      nonce=name[4:] if name.startswith('qrw_') else value.get('nonce'),
                      fields=action.get('form_value') or {})
        if not isinstance(result['fields'], dict) or len(result['fields']) > 20:
            raise Rejected('表单内容无效。')
        if any(not isinstance(v, str) or len(v) > 2000 for v in result['fields'].values()):
            raise Rejected('请检查表单字段。')
    elif kind == 'im.message.receive_v1':
        message = body.get('message', {})
        if message.get('chat_type') != 'p2p' or message.get('message_type') != 'text':
            raise Rejected('请在机器人私聊中打开工作台。')
        content = message.get('content', '{}')
        content = json.loads(content) if isinstance(content, str) else content
        if content.get('text', '').strip().lower() not in {'工作台', 'qr工作台', '/workbench'}:
            raise Rejected('不支持的工作台命令。')
        result.update(actor=body.get('sender', {}).get('sender_id', {}).get('open_id'),
                      chat=message.get('chat_id'), id=result['id'] or message.get('message_id'))
    elif kind == 'application.bot.menu_v6':
        if body.get('event_key') not in {'qrw_home', 'qrw_daily', 'qrw_assets', 'qrw_research'}:
            raise Rejected('未知菜单。')
        result.update(actor=body.get('operator', {}).get('operator_id', {}).get('open_id'), chat='',
                      action={'qrw_daily': 'daily.open', 'qrw_assets': 'assets', 'qrw_research': 'research.open'}.get(body['event_key'], 'home'))
    elif kind == 'im.chat.access_event.bot_p2p_chat_entered_v1':
        result.update(actor=body.get('operator_id', {}).get('open_id'), chat=body.get('chat_id'))
    else:
        raise Rejected('不支持的事件。')
    return result


class Workbench:
    def __init__(self, config, store, portfolio, research, delivery):
        self.config, self.store = config, store
        self.portfolio, self.research, self.delivery = portfolio, research, delivery
        self.allowed = set(config.get('allowed_users', []))
        if not self.allowed or '*' in self.allowed:
            raise ValueError('必须配置明确的工作台用户白名单。')
        self.stopping = False
        self.tasks = []
        self.render_locks = {}
        self.daily = DailyWorkflow(self)

    def authorize(self, actor):
        if not isinstance(actor, str) or actor not in self.allowed:
            raise Rejected('你没有此工作台的操作权限。')

    def accept(self, envelope):
        if envelope.get('account_id') != self.config.get('account_id', 'qr'):
            raise Rejected('机器人账户不匹配。')
        event = normalize(envelope)
        self.authorize(event.get('actor'))
        if event['kind'] == 'card.action.trigger':
            if not all(isinstance(event.get(key), str) and event[key] for key in ('chat', 'message', 'nonce')):
                raise Rejected('卡片上下文不完整。')
            accepted = self.store.accept(event['actor'], event['chat'], event['message'], event['nonce'], event['fields'])
            return toast('正在处理…' if accepted else '这项操作已经接收，请勿重复提交。')
        # Entering a conversation is a navigation event, not an invitation to spam it.
        if event['kind'] == 'im.chat.access_event.bot_p2p_chat_entered_v1':
            with self.store.db() as db:
                if db.execute('SELECT 1 FROM sessions WHERE actor=? AND chat=? AND message!=?',
                              (event['actor'], event['chat'], '')).fetchone():
                    return {}
        key = event.get('id')
        if not key:
            # Menu/enter events should include event_id. Without one, include the
            # event timestamp to avoid treating distinct clicks as the same action.
            key = hashlib.sha256(encode(event['body']).encode()).hexdigest()
        self.store.open(event['actor'], event.get('chat', ''), event.get('action', 'home'), 'event_' + key)
        return {}

    def open(self, actor, panel='home', request_id=None):
        self.authorize(actor)
        action = {'home': 'home', 'daily': 'daily.open', 'assets': 'assets', 'research': 'research.open'}.get(panel)
        if not action:
            raise Rejected('未知工作台页面。')
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', request_id)):
            raise Rejected('无效的请求标识。')
        return self.store.open(actor, action=action, request_id='open_' + actor + '_' + request_id if request_id else None)

    def render(self, command, view, model):
        lock = self.render_locks.setdefault(command['session'], threading.Lock())
        with lock:
            key = command.get('id', f"observe_{command['session']}_{command.get('expected_revision')}") + ':' + view
            session = self.store.prepare_render(key, command['session'], view, model, ACTIONS, command.get('expected_revision'))
            if session is None:
                return None
            card = build_card(session['view'], session['model'], CardContext(session['id'], session['revision'], session['nonces']))
            if session['message']:
                self.delivery.patch(session['message'], card)
            else:
                sent = self.delivery.send(session['actor'], session['chat'], card, command['id'])
                session['message'], session['chat'] = sent['message_id'], sent['chat_id']
            self.store.delivered(key, session['message'], session['chat'])
            return session

    def daily_form(self):
        overview = self.portfolio.overview()
        accounts = overview.get('accounts', overview.get('groups', []))
        if isinstance(accounts, dict):
            accounts = [{'label': name, 'value': name} for name in accounts]
        accounts = [{'label': v, 'value': v} if isinstance(v, str) else v for v in accounts]
        return {'accounts': accounts, 'positions': overview.get('positions', []),
                'date': datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                'account': accounts[0]['value'] if accounts else '', 'operation': 'no_change',
                'currency': 'CNY', 'fee': '0',
                'notice': ''}

    def research_form(self):
        datasets = self.research.get('/api/lab/datasets')
        templates = self.research.get('/api/lab/templates')
        connections = self.research.get('/api/lab/connections')
        def options(values):
            return [{'label': v.get('name', v.get('title', v['id'])), 'value': v['id']} for v in values]
        return {'datasets': options(datasets), 'templates': options(templates), 'connections': options(connections),
                'dataset_id': datasets[0]['id'] if datasets else '',
                'template_id': templates[0]['id'] if templates else '', 'mode': 'manual', 'max_trials': '3',
                'goal': '研究股票特征是否具有样本外预测能力',
                'notice': ''}

    def research_preview(self, fields):
        from core.lab_models import RunRequest
        config = self.research_form()
        for key, options in [('dataset_id', 'datasets'), ('template_id', 'templates')]:
            if fields.get(key) not in {v['value'] for v in config[options]}:
                raise Rejected('所选研究数据或模板已失效，请重新选择。')
        mode = fields.get('mode', 'manual')
        connection = fields.get('connection_id') if mode == 'agent' else None
        if mode == 'agent' and connection not in {v['value'] for v in config['connections']}:
            raise Rejected('请选择可用的模型连接。')
        try:
            candidate = RunRequest(mode=mode, objective=fields.get('goal', ''), dataset_id=fields['dataset_id'],
                                   template_id=fields['template_id'], connection_id=connection,
                                   max_experiments=int(fields.get('max_trials', 3))).model_dump(mode='json')
        except (ValueError, TypeError):
            raise Rejected('请检查研究问题、模式和实验次数（1–6 次）。') from None
        names = {v['value']: v['label'] for group in ('datasets', 'templates', 'connections') for v in config[group]}
        changes = [f"数据：{names[candidate['dataset_id']]}", f"方法：{names[candidate['template_id']]}",
                   f"模式：{'Agent 自动研究' if mode == 'agent' else '手动实验'}",
                   f"实验上限：{candidate['max_experiments']} 次；最长 10 分钟"]
        if connection:
            changes.append(f'模型连接：{names[connection]}。研究上下文会发送给此模型服务。')
        return {'summary': candidate['objective'], 'changes': changes, 'mode': mode, '_request': candidate}

    @staticmethod
    def run_model(run):
        result = run.get('result') or {}
        model = {'status': run.get('status', ''), 'stage': {'queued': '等待开始', 'planning': '研究方案', 'experiment': '实验计算',
                          'evaluated': '实验完成', 'frozen': '方案已确定', 'reporting': '结果评价',
                          'reported': '结果已保存', 'completed': '已完成', 'failed': '未完成',
                          'cancelled': '已取消'}.get(run.get('stage'), '处理中'),
                'goal': run.get('request', {}).get('objective', run.get('objective', '研究任务')),
                'completed': run.get('progress', {}).get('experiments_completed', 0),
                'max_trials': run.get('request', {}).get('max_experiments', 3),
                'can_cancel': run.get('status') in {'queued', 'running'},
                'summary': result.get('summary', result.get('interpretation', '研究结果已保存，可继续查看或运行下一项实验。')),
                '_run_id': run['id'], 'reference': run['id']}
        from core.lab_service import safe_message
        if run.get('error'):
            model['error'] = safe_message(run['error'])
        import math
        model['metrics'] = []
        for section, prefix in [('validation', '验证集'), ('test', '测试集')]:
            metrics = result.get('metrics', {}).get(section, {})
            for key, label in [('mae', '平均绝对误差'), ('baseline_mae', '基线误差'), ('improvement_pct', '较基线改善')]:
                value = metrics.get(key, metrics.get('model_mae') if key == 'mae' else None)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    model['metrics'].append({'label': prefix + ' · ' + label,
                                             'value': f'{value:.4f}' + ('%' if key.endswith('_pct') else '')})
        model['warnings'] = result.get('warnings', result.get('limitations', []))
        model['lessons'] = result.get('lessons', [])
        curve = result.get('equity', [])
        if len(curve) > 90:
            model['equity'] = [curve[round(i * (len(curve) - 1) / 89)] for i in range(90)]
            model['warnings'] = list(model['warnings']) + ['卡片曲线显示 90 个抽样点；完整数据保存在研究结果中。']
        else:
            model['equity'] = curve
        review = result.get('agent_review') or {}
        if isinstance(review, dict) and isinstance(review.get('summary'), str):
            model['summary'] = safe_message(review['summary'])
        elif model['lessons']:
            model['summary'] = ' '.join(str(v) for v in model['lessons'][:2])
        if not isinstance(model['summary'], str):
            model['summary'] = encode(model['summary'])
        return model

    def execute(self, command):
        action, payload = command['action'], command['payload']
        fields, previous = payload.get('fields', {}), payload.get('model', {})
        if action == 'home':
            return self.render(command, 'home', {'title': 'QR 工作台', 'subtitle': '组合记录与股票研究',
                                               'notice': ''})
        if action == 'daily.open' or action in DAILY_ACTIONS:
            return self.daily.execute(command)
        if action == 'daily.choose':
            form = self.daily_form()
            if set(fields) - {'date', 'account', 'operation'}:
                raise Rejected('请重新选择更新事项。')
            if fields.get('account') not in {a['value'] for a in form['accounts']} or fields.get('operation') not in {'no_change', 'buy', 'sell', 'set_cash', 'set_fund'}:
                raise Rejected('请选择有效账户和更新事项。')
            from .portfolio import _day
            selection = {**fields, 'date': _day(fields.get('date'))}
            if selection['operation'] == 'no_change':
                preview = self.portfolio.preview({'operation': 'no_change', 'date': selection['date']})
                return self.render(command, 'daily_preview', {**preview, '_preview': preview})
            form.update(selection)
            form['_selection'] = selection
            if selection['operation'] == 'set_fund':
                form['currency'] = 'CNY'
            return self.render(command, 'daily_form', form)
        if action == 'assets':
            return self.render(command, 'assets', self.portfolio.overview())
        if action == 'reports':
            model = self.portfolio.reports()
            actor = self.store.session(command['session'])['actor']
            recent = self.store.recent_reports(actor)
            model['items'] = list(model.get('items', []))
            for report in recent:
                label = {'completed': '已完成', 'running': '处理中', 'queued': '等待处理', 'failed': '未完成'}[report['status']]
                model['items'].insert(0, {'title': '日报更新 · ' + label, 'summary': report['error'] or '资产确认与日报更新分别记录，可安全重试日报。'})
            failed = next((r for r in recent if r['status'] == 'failed'), None)
            model['can_retry_report'] = bool(failed)
            if failed:
                model['_report_command'] = failed['id']
            return self.render(command, 'reports', model)
        if action == 'daily.report.retry':
            actor = self.store.session(command['session'])['actor']
            self.store.retry_report(actor, previous.get('_report_command', ''))
            return self.render(command, 'daily_receipt', {'summary': '日报已重新排队，资产记录不会再次修改。'})
        if action == 'daily.preview':
            fields = dict(fields)
            selection = previous.get('_selection')
            if selection:
                if set(fields) & {'date', 'account', 'operation'}:
                    raise Rejected('请通过上一步修改账户、日期或更新事项。')
                fields = {**fields, **selection}
                if selection['operation'] == 'set_fund':
                    fields['currency'] = 'CNY'
            operation = fields.get('operation')
            if 'symbol' in fields:
                fields['ticker'] = fields.pop('symbol')
            if operation == 'no_change':
                # These controls have defaults even when no transaction is selected.
                for key in ('account', 'currency'):
                    fields.pop(key, None)
            if operation in {'no_change', 'set_cash', 'set_fund'} and fields.get('fee') in {'', '0', None}:
                fields.pop('fee', None)
            preview = self.portfolio.preview(fields)
            return self.render(command, 'daily_preview', {**preview, '_preview': preview})
        if action == 'daily.confirm':
            if payload.get('view') != 'daily_preview' or not previous.get('_preview'):
                raise Rejected('请先重新预览资产变化。')
            receipt = self.portfolio.confirm(previous['_preview'], command['id'])
            self.store.enqueue_report(command['session'], receipt, command['id'])
            return self.render(command, 'daily_receipt', {**receipt, 'notice': '资产记录已确认。日报正在更新，可从“日报”查看处理状态。'})
        if action == 'daily.report':
            return self.portfolio.run_report(payload['receipt'])
        if action == 'research.open':
            return self.render(command, 'research_form', self.research_form())
        if action == 'research.preview':
            return self.render(command, 'research_preview', self.research_preview(fields))
        if action == 'research.start':
            if payload.get('view') != 'research_preview' or not previous.get('_request'):
                raise Rejected('请先确认研究方案。')
            request = {**previous['_request'], 'client_request_id': 'feishu_' + command['id']}
            run = self.research.post('/api/lab/runs', request)
            return self.render(command, 'research_status', self.run_model(run))
        if action == 'research.list':
            runs = self.research.get('/api/lab/runs')
            if not runs:
                return self.render(command, 'research_form', self.research_form())
            items = [{'label': f"{r.get('request', {}).get('objective', '研究任务')[:70]} · {r.get('status', '')}",
                      'value': r['id']} for r in runs[:20]]
            return self.render(command, 'research_list', {'runs': items, 'run_id': items[0]['value']})
        if action == 'research.select':
            identifier = fields.get('run_id', '')
            if identifier not in {r['value'] for r in previous.get('runs', [])}:
                raise Rejected('请选择列表中的研究任务。')
            run = self.research.get('/api/lab/runs/' + identifier)
            view = 'research_result' if run.get('status') == 'completed' else 'research_status'
            return self.render(command, view, self.run_model(run))
        if action in {'research.refresh', 'research.cancel'}:
            identifier = previous.get('_run_id', '')
            if not re.fullmatch(r'run_[a-f0-9]{32}', identifier):
                raise Rejected('请重新打开研究任务。')
            path = '/api/lab/runs/' + identifier
            run = self.research.post(path + '/cancel') if action == 'research.cancel' else self.research.get(path)
            view = 'research_result' if run.get('status') == 'completed' else 'research_status'
            return self.render(command, view, self.run_model(run))
        raise Rejected('未知操作，请重新打开工作台。')

    async def start(self):
        self.store.recover()
        self.stopping = False
        self.tasks = [asyncio.create_task(self.worker()), asyncio.create_task(self.worker(reports=True)),
                      asyncio.create_task(self.observe())]

    async def stop(self):
        self.stopping = True
        # Let in-flight adapter operations finish; cancelling to_thread would
        # leave a financial write running after the worker appeared stopped.
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def database(self, function, *args, stop_result=_KEEP_RETRYING, **kwargs):
        # Retry only the database operation after transient contention; never
        # rerun an already completed financial or network side effect here.
        while True:
            try:
                if self.stopping and stop_result is not _KEEP_RETRYING:
                    return stop_result
                return await asyncio.to_thread(function, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not any(word in str(exc).lower() for word in ('locked', 'busy')):
                    raise
                await asyncio.sleep(.2)

    async def worker(self, reports=False):
        while not self.stopping:
            command = await self.database(self.store.claim, reports=reports, stop_result=None)
            if not command:
                await asyncio.sleep(.2)
                continue
            try:
                await asyncio.to_thread(self.execute, command)
            except (DeliveryError, sqlite3.OperationalError):
                if command['attempts'] < 2:
                    await self.database(self.store.retry, command)
                    await asyncio.sleep(2)
                    continue
                await self.database(self.store.finish, command, '服务暂时不可用；已确认的资产变更不会重复执行。')
            except Exception as exc:
                # Only deliberate user-facing errors are exposed. SDK/model/file
                # errors can contain paths, secrets or account internals.
                from .portfolio import PortfolioError
                message = str(exc) if isinstance(exc, (Rejected, PortfolioError)) else '操作未完成，请返回工作台核对状态。'
                if not reports:
                    try:
                        await asyncio.to_thread(self.render, command, 'error', {'message': message})
                    except Exception:
                        pass
                await self.database(self.store.finish, command, message)
            else:
                await self.database(self.store.finish, command)

    async def observe(self):
        while not self.stopping:
            for session in await self.database(self.store.observed_sessions, stop_result=[]):
                identifier = session['model'].get('_run_id', '')
                if not re.fullmatch(r'run_[a-f0-9]{32}', identifier):
                    continue
                try:
                    run = await asyncio.to_thread(self.research.get, '/api/lab/runs/' + identifier)
                    model = self.run_model(run)
                    if model == session['model']:
                        continue
                    view = 'research_result' if run.get('status') == 'completed' else 'research_status'
                    await asyncio.to_thread(self.render, {'session': session['id'], 'expected_revision': session['revision']}, view, model)
                except Exception:
                    # A transient outage retains the last valid card and its
                    # explicit refresh button instead of presenting a success.
                    pass
            for _ in range(25):
                if self.stopping:
                    break
                await asyncio.sleep(.2)
