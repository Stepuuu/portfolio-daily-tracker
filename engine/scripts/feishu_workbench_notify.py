"""Opt-in daily card reminders through the local authenticated companion."""
import json
import os
from pathlib import Path
import re
import stat
from urllib import request


def _private(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_size > 65536:
            raise ValueError('Private configuration required')
        return stream.read()


def try_daily_card(day, target, config_path=None):
    """Return false to retain the existing reminder if no private bridge is ready."""
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            return False
        root = Path(__file__).resolve().parents[1]
        if root.name == 'engine':
            root = root.parent / 'dashboard'
        path = Path(config_path) if config_path else root / 'data/feishu-workbench/config.json'
        config = json.loads(_private(path))
        owner = str(target or '').removeprefix('user:')
        if owner not in config.get('allowed_users', []) or owner != config.get('initial_user'):
            return False  # Never redirect an existing group reminder to a private user.
        port = config.get('port', 18200)
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            return False
        token = _private(config['bridge_token_file']).strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{32,}', token):
            return False
        data = json.dumps({'actor': owner, 'panel': 'daily', 'request_id': 'daily_' + day}).encode()
        req = request.Request(f'http://127.0.0.1:{port}/bridge/open', data=data,
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        http = request.build_opener(request.ProxyHandler({}), NoRedirect())
        with http.open(req, timeout=3) as response:
            result = json.loads(response.read(65536))
        return result.get('status') == 'queued' and bool(result.get('session_id'))
    except (OSError, ValueError, TypeError, KeyError):
        return False
