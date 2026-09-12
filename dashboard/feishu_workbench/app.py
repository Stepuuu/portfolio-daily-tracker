"""Loopback companion for a trusted Feishu connection, never a public webhook."""
from contextlib import asynccontextmanager
import asyncio
import fcntl
import hmac
import json
import os
from pathlib import Path
import stat

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .clients import FeishuClient, ResearchClient
from .service import Workbench, toast
from .store import Rejected, Store


def private_text(path):
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError('工作台配置与令牌文件必须仅当前用户可读写。')
        if info.st_size > 65536:
            raise ValueError('配置文件过大。')
        return stream.read()


def load_config(path):
    config = json.loads(private_text(path))
    if not isinstance(config, dict):
        raise ValueError('工作台配置无效。')
    token = private_text(config['bridge_token_file']).strip()
    if len(token) < 32:
        raise ValueError('工作台令牌长度不足。')
    config['_token'] = token
    return config


def create_app(config, controller=None):
    if controller is None:
        from .portfolio import PortfolioAdapter
        state = Path(config['state_dir'])
        controller = Workbench(config, Store(state / 'cards.sqlite3'),
                               PortfolioAdapter(Path(config['project_dir']), state),
                               ResearchClient(config.get('lab_url', 'http://127.0.0.1:8000')),
                               FeishuClient(config))

    @asynccontextmanager
    async def lifespan(app):
        lock_path = Path(config['state_dir']) / 'service.lock'
        with lock_path.open('a+b') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('已有工作台服务使用此目录。') from None
            await controller.start()
            try:
                yield
            finally:
                await controller.stop()

    app = FastAPI(title='QR Feishu Workbench', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.controller = controller

    async def authenticated(request):
        if request.client and request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
            raise HTTPException(403, '仅接受本机桥接请求。')
        expected = 'Bearer ' + config['_token']
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), expected.encode()):
            raise HTTPException(401, '工作台桥接认证失败。')
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 256 * 1024:
                raise HTTPException(413, '请求过大。')
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (ValueError, UnicodeError):
            raise HTTPException(400, '无效的请求。') from None

    @app.get('/health')
    async def health():
        return {'ok': bool(controller.tasks) and all(not task.done() for task in controller.tasks),
                'commands': controller.store.counts()}

    @app.post('/bridge/event')
    async def event(request: Request):
        payload = await authenticated(request)
        try:
            return await asyncio.to_thread(controller.accept, payload)
        except Rejected as exc:
            return toast(str(exc), 'error')
        except (ValueError, TypeError, AttributeError):
            return toast('操作数据无效，请重新打开工作台。', 'error')

    @app.post('/bridge/open')
    async def open_card(request: Request):
        payload = await authenticated(request)
        try:
            identifier = controller.open(payload.get('actor'), payload.get('panel', 'home'), payload.get('request_id'))
        except Rejected as exc:
            raise HTTPException(403, str(exc)) from None
        return {'session_id': identifier, 'status': 'queued'}

    return app
