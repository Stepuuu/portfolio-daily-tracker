"""Create private runtime configuration without touching account credentials."""
import json
import os
from pathlib import Path
import secrets

from .clients import local_url


def initialize(project_dir, allowed_users, lab_url, account_id='qr', openclaw_config=None):
    project = Path(project_dir).resolve(strict=True)
    if not allowed_users or any(not user or user == '*' for user in allowed_users):
        raise ValueError('Provide an explicit user allowlist.')
    app = project / 'dashboard' if (project / 'dashboard/feishu_workbench').is_dir() else project
    state = app / 'data/feishu-workbench'
    if state.exists():
        raise ValueError('Workbench state already exists; existing configuration was preserved.')
    local_url(lab_url)
    config = {'project_dir': str(project), 'state_dir': str(state),
              'bridge_token_file': str(state / 'bridge.token'), 'account_id': account_id,
              'allowed_users': list(dict.fromkeys(allowed_users)), 'initial_user': allowed_users[0],
              'port': 18200, 'lab_url': lab_url}
    if openclaw_config:
        config['openclaw_config'] = str(Path(openclaw_config).resolve(strict=True))
    state.mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    for filename, content in [('bridge.token', secrets.token_urlsafe(48) + '\n'),
                              ('config.json', json.dumps(config, indent=2) + '\n')]:
        descriptor = os.open(state / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
    return state / 'config.json'
