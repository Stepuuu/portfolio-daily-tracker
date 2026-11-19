"""The unauthenticated API answers only for the host names it is meant to serve."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.trusted_hosts import DEFAULT_ALLOWED_HOSTS, HostAllowList, allowed_hosts


DASHBOARD = Path(__file__).parents[1] / 'dashboard'


def client(environ=None):
    app = FastAPI()
    app.add_middleware(HostAllowList, allowed_hosts=allowed_hosts(environ or {}))

    @app.get('/health')
    async def health():
        return {'status': 'healthy'}

    return TestClient(app)


def status(test_client, host):
    return test_client.get('/health', headers={'host': host}).status_code


def test_default_list_is_loopback_and_the_compose_service():
    assert allowed_hosts({}) == list(DEFAULT_ALLOWED_HOSTS) == ['localhost', '127.0.0.1', '[::1]', 'backend']
    assert allowed_hosts({'TRACKER_ALLOWED_HOSTS': ''}) == list(DEFAULT_ALLOWED_HOSTS)
    assert 'testserver' not in DEFAULT_ALLOWED_HOSTS and '*' not in DEFAULT_ALLOWED_HOSTS


@pytest.mark.parametrize('host', ['localhost', 'localhost:3000', '127.0.0.1', '127.0.0.1:8000',
                                  '[::1]', '[::1]:8000', 'backend', 'backend:8000'])
def test_loopback_and_compose_hosts_pass(host):
    assert status(client(), host) == 200


@pytest.mark.parametrize('host', ['attacker.example', 'attacker.example:8000', '127.0.0.1.attacker.example',
                                  'localhost.attacker.example', 'backend.attacker.example', '192.0.2.10',
                                  '[2001:db8::1]', '[2001:db8::1]:8000', '[::1].attacker.example',
                                  '[::1]attacker.example', '[::1]:attacker.example', '0.0.0.0', ''])
def test_foreign_hosts_are_rejected(host):
    response = client().get('/health', headers={'host': host})
    assert response.status_code == 400
    assert 'healthy' not in response.text


@pytest.mark.parametrize('host', ['LOCALHOST', 'LocalHost:8000', 'localhost.', 'localhost.:8000', 'Backend.',
                                  '127.0.0.1.', '[::1]:8000', '[::1].'])
def test_host_matching_ignores_case_and_one_trailing_dot(host):
    expected = 400 if host == '[::1].' else 200
    assert status(client(), host) == expected


def test_case_and_trailing_dot_work_for_wildcard_entries():
    extended = client({'TRACKER_ALLOWED_HOSTS': '*.lan.example,Tracker.Example'})
    for host in ('Desk.LAN.example', 'desk.lan.example.', 'TRACKER.example.:3000'):
        assert status(extended, host) == 200, host
    for host in ('lan.example', 'desk.lan.example..', 'localhost..', 'desk.lan.example.attacker', '.localhost'):
        assert status(extended, host) == 400, host


@pytest.mark.parametrize('host', ['[::1', '[::1:8000', '[:::]', '[::1]x', '[]', '[::1%eth0]', '[1::2::3]',
                                  '[::ffff:999.1.1.1]'])
def test_malformed_bracketed_hosts_are_rejected(host):
    assert status(client({'TRACKER_ALLOWED_HOSTS': '*.lan.example'}), host) == 400


def test_other_spellings_of_an_allowed_ipv6_address_match():
    assert status(client(), '[0:0:0:0:0:0:0:1]:8000') == 200
    assert status(client(), '[::0001]') == 200


@pytest.mark.parametrize('value', ['[:::]', '[::1', '[::1%eth0]', '[]', '[1::2::3]', '::1'])
def test_invalid_ipv6_entries_are_refused_at_startup(value):
    with pytest.raises(ValueError, match='TRACKER_ALLOWED_HOSTS'):
        allowed_hosts({'TRACKER_ALLOWED_HOSTS': value})


def test_test_client_default_host_is_not_trusted_by_default():
    assert client().get('/health').status_code == 400


def test_environment_extends_the_list():
    environ = {'TRACKER_ALLOWED_HOSTS': ' Tracker.Example , 192.0.2.10:3000,[2001:db8::1]:3000,*.lan.example,,'}
    assert allowed_hosts(environ) == [*DEFAULT_ALLOWED_HOSTS, 'tracker.example', '192.0.2.10',
                                      '[2001:db8::1]', '*.lan.example']
    extended = client(environ)
    for host in ('tracker.example', 'tracker.example:3000', '192.0.2.10:8000', '[2001:db8::1]:8000',
                 'desk.lan.example', '127.0.0.1:8000', 'localhost', 'backend:8000'):
        assert status(extended, host) == 200, host
    for host in ('attacker.example', 'tracker.example.attacker.example', '192.0.2.11', '[2001:db8::2]'):
        assert status(extended, host) == 400, host


def test_wildcard_disables_the_check():
    assert allowed_hosts({'TRACKER_ALLOWED_HOSTS': 'tracker.example, *'}) == ['*']
    assert status(client({'TRACKER_ALLOWED_HOSTS': '*'}), 'attacker.example') == 200


@pytest.mark.parametrize('value', ['http://tracker.example', 'tracker.example/path', 'tracker.*',
                                   '*tracker.example', 'tracker example'])
def test_malformed_entries_are_refused_at_startup(value):
    with pytest.raises(ValueError, match='TRACKER_ALLOWED_HOSTS'):
        allowed_hosts({'TRACKER_ALLOWED_HOSTS': value})


APPLICATION = '''
import json, sys
from fastapi.testclient import TestClient
from backend.main import app
client = TestClient(app)
print(json.dumps({host: client.get('/health', headers={'host': host}).status_code for host in sys.argv[1:]}))
'''


def application(workdir, hosts, allowed=None):
    """Statuses from the real application, imported in a fresh process.

    The process runs in ``workdir`` because importing the routes creates data
    directories relative to the working directory.
    """
    env = {key: value for key, value in os.environ.items() if key != 'TRACKER_ALLOWED_HOSTS'}
    env.update(PYTHONPATH=str(DASHBOARD), TRACKER_DEMO_MODE='1',
               TRACKER_CONFIG_FILE=str(workdir / 'config.json'),
               TRACKER_LAB_DIR=str(workdir / 'research'), PORTFOLIO_DIR=str(workdir / 'portfolio'))
    if allowed is not None:
        env['TRACKER_ALLOWED_HOSTS'] = allowed
    result = subprocess.run([sys.executable, '-c', APPLICATION, *hosts], cwd=workdir, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


needs_application = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')


@needs_application
def test_application_rejects_foreign_hosts_and_serves_loopback(tmp_path):
    statuses = application(tmp_path, ['127.0.0.1:8000', 'localhost:3000', '[::1]:8000', 'backend:8000',
                                      'attacker.example', 'testserver'])
    assert statuses == {'127.0.0.1:8000': 200, 'localhost:3000': 200, '[::1]:8000': 200, 'backend:8000': 200,
                        'attacker.example': 400, 'testserver': 400}


@needs_application
def test_application_reads_the_environment_at_startup(tmp_path):
    hosts = ['tracker.example:3000', 'testserver', '127.0.0.1:8000', 'attacker.example']
    assert application(tmp_path, hosts, 'tracker.example,testserver') == {
        'tracker.example:3000': 200, 'testserver': 200, '127.0.0.1:8000': 200, 'attacker.example': 400}
    assert application(tmp_path, hosts, '*') == dict.fromkeys(hosts, 200)
