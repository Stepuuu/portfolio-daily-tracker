"""Run the companion or open a private card using an existing configuration."""
import argparse
import json
import os

from .app import create_app, load_config
from .clients import read_json, local_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'serve', 'open', 'status'])
    parser.add_argument('--config', default=os.environ.get('QR_WORKBENCH_CONFIG', 'data/feishu-workbench/config.json'))
    parser.add_argument('--panel', choices=['home', 'daily', 'assets', 'research'], default='home')
    parser.add_argument('--request-id')
    parser.add_argument('--project-dir', default='.')
    parser.add_argument('--allowed-user', action='append', default=[])
    parser.add_argument('--lab-url', default='http://127.0.0.1:8000')
    parser.add_argument('--account-id', default='qr')
    parser.add_argument('--openclaw-config')
    args = parser.parse_args()
    if args.command == 'init':
        from .setup import initialize
        result = initialize(args.project_dir, args.allowed_user, args.lab_url,
                            args.account_id, args.openclaw_config)
        print('Created private workbench configuration: ' + str(result))
        return
    config = load_config(args.config)
    port = config.get('port', 18200)
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        parser.error('port must be an unprivileged TCP port')
    if args.command == 'serve':
        import uvicorn
        uvicorn.run(create_app(config), host='127.0.0.1', port=port, access_log=False)
    elif args.command == 'status':
        print(json.dumps(read_json(f'http://127.0.0.1:{port}/health'), ensure_ascii=False))
    else:
        users = config.get('allowed_users', [])
        actor = config.get('initial_user')
        if actor is None and len(users) == 1:
            actor = users[0]
        if actor not in users:
            parser.error('configure initial_user from allowed_users')
        result = read_json(f'http://127.0.0.1:{port}/bridge/open', {'actor': actor, 'panel': args.panel, 'request_id': args.request_id},
                           {'Authorization': 'Bearer ' + config['_token']})
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
