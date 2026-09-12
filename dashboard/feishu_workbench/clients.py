"""Explicit local research calls and app-scoped Feishu message delivery."""
import json
import os
from pathlib import Path
import time
from urllib import request, error
from urllib.parse import urlsplit
import uuid


class DeliveryError(RuntimeError):
    """Transport errors have no credentials or upstream response bodies."""


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


HTTP = request.build_opener(request.ProxyHandler({}), NoRedirect())


def read_json(url, payload=None, headers=None, method=None, timeout=15):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    req = request.Request(url, data=body, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with HTTP.open(req, timeout=timeout) as response:
            data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise DeliveryError('响应过大，请缩小查询范围。')
        return json.loads(data)
    except (error.HTTPError, error.URLError, TimeoutError, OSError, ValueError):
        raise DeliveryError('服务暂时不可用，请稍后查看处理结果。') from None


def local_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1', 'localhost'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise ValueError('研究接口必须是本机 HTTP 地址。')
    return value.rstrip('/').replace('://localhost', '://127.0.0.1')


class ResearchClient:
    def __init__(self, url):
        self.url = local_url(url)

    def get(self, path):
        if not path.startswith('/api/lab/') or '..' in path:
            raise ValueError('无效的研究接口。')
        return read_json(self.url + path, timeout=10)

    def post(self, path, payload=None):
        if not path.startswith('/api/lab/') or '..' in path:
            raise ValueError('无效的研究接口。')
        return read_json(self.url + path, {} if payload is None else payload, timeout=10)


class FeishuClient:
    def __init__(self, config):
        self.config = config
        self.token = ''
        self.expires = 0

    def credentials(self):
        if self.config.get('openclaw_config'):
            raw = json.loads(Path(self.config['openclaw_config']).read_text())
            channel = raw['channels']['feishu']
            account = channel['accounts'][self.config.get('account_id', 'qr')]
            app_id, secret = account['appId'], account['appSecret']
            domain = account.get('domain', channel.get('domain', 'feishu'))
        else:
            app_id = os.environ[self.config.get('app_id_env', 'FEISHU_APP_ID')]
            secret = os.environ[self.config.get('app_secret_env', 'FEISHU_APP_SECRET')]
            domain = self.config.get('domain', 'feishu')
        domain = {'feishu': 'https://open.feishu.cn', 'lark': 'https://open.larksuite.com'}.get(domain, domain)
        if domain not in {'https://open.feishu.cn', 'https://open.larksuite.com'}:
            raise ValueError('仅支持飞书或 Lark 官方 API 域名。')
        return app_id, secret, domain

    def api(self, path, payload, method='POST'):
        app_id, secret, domain = self.credentials()
        if time.time() >= self.expires:
            auth = read_json(domain + '/open-apis/auth/v3/tenant_access_token/internal',
                             {'app_id': app_id, 'app_secret': secret})
            if auth.get('code') != 0 or not auth.get('tenant_access_token'):
                raise DeliveryError('机器人认证失败，请检查应用配置。')
            self.token = auth['tenant_access_token']
            self.expires = time.time() + max(30, auth.get('expire', 7200) - 60)
        result = read_json(domain + path, payload, {'Authorization': 'Bearer ' + self.token}, method)
        if result.get('code') != 0:
            self.expires = 0
            raise DeliveryError('飞书未接受卡片，请检查机器人权限及卡片结构。')
        return result.get('data', {})

    def send(self, actor, chat, card, operation_id):
        kind = 'chat_id' if chat else 'open_id'
        return self.api('/open-apis/im/v1/messages?receive_id_type=' + kind, {
            'receive_id': chat or actor, 'msg_type': 'interactive',
            'content': json.dumps(card, ensure_ascii=False),
            'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, 'qr-workbench:' + operation_id)),
        })

    def patch(self, message, card):
        if not message.startswith('om_') or not message.replace('_', '').isalnum():
            raise ValueError('无效的卡片消息。')
        return self.api('/open-apis/im/v1/messages/' + message,
                        {'content': json.dumps(card, ensure_ascii=False)}, 'PATCH')
