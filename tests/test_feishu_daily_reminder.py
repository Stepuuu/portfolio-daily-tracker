import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from feishu_workbench_notify import try_daily_card


def test_daily_card_targets_only_existing_private_owner_with_dedup_id(tmp_path):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, self.headers.get('Authorization'),
                         json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"queued","session_id":"example"}')
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    token = tmp_path / 'token'
    token.write_text('synthetic_test_token_' + 'a' * 40)
    token.chmod(0o600)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'port': server.server_port, 'bridge_token_file': str(token),
                                  'initial_user': 'owner', 'allowed_users': ['owner', 'other']}))
    config.chmod(0o600)
    try:
        assert not try_daily_card('2026-01-02', 'group', config)
        assert not try_daily_card('2026-01-02', 'other', config)
        assert try_daily_card('2026-01-02', 'owner', config)
        assert seen[0][0] == '/bridge/open'
        assert seen[0][2] == {'actor': 'owner', 'panel': 'daily', 'request_id': 'daily_2026-01-02'}
        token.chmod(0o644)
        assert not try_daily_card('2026-01-02', 'owner', config)
        assert len(seen) == 1
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
