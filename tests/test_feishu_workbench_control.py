"""Process ownership tests use fixtures; no production service is started."""
import json
import os
from pathlib import Path
import signal
import socket
import sys
from types import SimpleNamespace

import pytest

from feishu_workbench import control


def record_for(config_path, pid=12345):
    argv = [sys.executable, '-m', 'feishu_workbench', 'serve', '--config', str(config_path)]
    return {'pid': pid, 'start': '100', 'argv': ('\0'.join(argv) + '\0').encode().hex(), 'boot': 'fixture-boot'}


@pytest.fixture
def managed(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    config_path = (tmp_path / 'config.json').resolve()
    config = {'state_dir': str(state), 'port': 18200}
    monkeypatch.setattr(control, 'load_config', lambda _path: config)
    monkeypatch.setattr(control.time, 'sleep', lambda _seconds: None)
    record = record_for(config_path)
    record_path = state / 'process.json'
    record_path.write_text(json.dumps(record))
    record_path.chmod(0o600)
    monkeypatch.setattr(control, 'identity', lambda pid: record if pid == record['pid'] else None)
    monkeypatch.setattr(control, 'owns_listener', lambda _pid, _port: True)
    monkeypatch.setattr(control, 'read_json', lambda *_args, **_kwargs: {'ok': True, 'commands': {}})
    return SimpleNamespace(state=state, config_path=config_path, config=config, record=record, record_path=record_path)


def invoke(command, fixture):
    return control.main([command, '--config', str(fixture.config_path)])


def test_identity_handles_process_names_with_parentheses_and_rejects_zombies(tmp_path, monkeypatch):
    monkeypatch.setattr(control, 'PROC_ROOT', tmp_path)
    process = tmp_path / '123'
    process.mkdir()
    boot = tmp_path / 'sys/kernel/random/boot_id'
    boot.parent.mkdir(parents=True)
    boot.write_text('fixture-boot\n')
    fields = ['S'] + ['0'] * 18 + ['92837'] + ['0'] * 3
    (process / 'stat').write_text('123 (name with ) spaces) ' + ' '.join(fields))
    (process / 'cmdline').write_bytes(b'python\0-m\0test\0')
    assert control.identity(123) == {
        'pid': 123, 'start': '92837', 'argv': b'python\0-m\0test\0'.hex(), 'boot': 'fixture-boot',
    }
    fields[0] = 'Z'
    (process / 'stat').write_text('123 (finished) ' + ' '.join(fields))
    assert control.identity(123) is None
    assert control.identity(124) is None
    assert control.identity('../123') is None
    assert control.identity(True) is None


def test_listener_must_be_loopback_and_owned_by_the_recorded_process(tmp_path, monkeypatch):
    monkeypatch.setattr(control, 'PROC_ROOT', tmp_path)
    process = tmp_path / '123'
    (process / 'fd').mkdir(parents=True)
    (process / 'net').mkdir()
    (process / 'fd/4').symlink_to('socket:[777]')
    tcp = process / 'net/tcp'
    tcp.write_text('header\n0: 0100007F:4718 00000000:0000 0A 0 0 0 1000 0 777\n')
    assert control.owns_listener(123, 18200)
    assert not control.owns_listener(124, 18200)
    assert not control.owns_listener(123, 18201)
    tcp.write_text('header\n0: 0100007F:4718 00000000:0000 0A 0 0 0 1000 0 888\n')
    assert not control.owns_listener(123, 18200)
    tcp.write_text('header\n0: 00000000:4718 00000000:0000 0A 0 0 0 1000 0 777\n')
    assert not control.owns_listener(123, 18200)


def test_repeated_start_does_not_spawn_another_process(managed, monkeypatch, capsys):
    monkeypatch.setattr(control.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Duplicate process'))
    assert invoke('start', managed) == 0
    assert invoke('start', managed) == 0
    assert capsys.readouterr().out == 'status: running\nstatus: running\n'


def test_reused_pid_or_different_configuration_is_never_stopped(managed, monkeypatch):
    monkeypatch.setattr(control.os, 'pidfd_open', lambda _pid: pytest.fail('Unowned PID'), raising=False)
    newer = {**managed.record, 'start': '200'}
    monkeypatch.setattr(control, 'identity', lambda _pid: newer)
    assert invoke('stop', managed) == 0
    assert invoke('status', managed) == 1
    other = record_for(managed.config_path.with_name('other.json'))
    managed.record_path.write_text(json.dumps(other))
    monkeypatch.setattr(control, 'identity', lambda _pid: other)
    assert invoke('stop', managed) == 0


def test_corrupt_record_does_not_kill_or_claim_any_process(managed, monkeypatch):
    managed.record_path.write_text('{')
    monkeypatch.setattr(control.os, 'pidfd_open', lambda _pid: pytest.fail('Unowned PID'), raising=False)
    assert invoke('status', managed) == 1
    assert invoke('stop', managed) == 0


def test_occupied_port_prevents_start_without_touching_its_process(managed, monkeypatch):
    managed.record_path.unlink()
    monkeypatch.setattr(control.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Port is occupied'))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        managed.config['port'] = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match='端口已被占用'):
            invoke('start', managed)
        assert listener.getsockname()[1] == managed.config['port']


def test_foreign_health_endpoint_cannot_report_startup_success(managed, monkeypatch, capsys):
    managed.record_path.unlink()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        managed.config['port'] = reservation.getsockname()[1]
    monkeypatch.setattr(control.subprocess, 'Popen', lambda *_args, **_kwargs: SimpleNamespace(pid=managed.record['pid']))
    monkeypatch.setattr(control, 'owns_listener', lambda _pid, _port: False)
    monkeypatch.setattr(control, 'read_json', lambda *_args, **_kwargs: pytest.fail('Foreign endpoint must not be queried'))
    with pytest.raises(RuntimeError, match='未通过启动检查'):
        invoke('start', managed)
    assert 'running' not in capsys.readouterr().out


def test_start_records_identity_privately_and_checks_its_listener(managed, monkeypatch):
    managed.record_path.unlink()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        managed.config['port'] = reservation.getsockname()[1]
    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=managed.record['pid'])

    monkeypatch.setattr(control.subprocess, 'Popen', spawn)
    assert invoke('start', managed) == 0
    assert len(calls) == 1
    assert calls[0][0][1:5] == ['-m', 'feishu_workbench', 'serve', '--config']
    assert calls[0][1]['start_new_session'] is True
    assert json.loads(managed.record_path.read_text()) == managed.record
    assert managed.record_path.stat().st_mode & 0o077 == 0
    assert (managed.state / 'service.log').stat().st_mode & 0o077 == 0


def test_stop_refuses_running_commands_and_unverifiable_health(managed, monkeypatch):
    monkeypatch.setattr(control.os, 'pidfd_open', lambda _pid: pytest.fail('Operation may be running'), raising=False)
    for response in [
        {'ok': True, 'commands': {'running': 1}},
        {'ok': False, 'commands': {}},
        {'ok': True},
        {'ok': True, 'commands': {'running': '0'}},
    ]:
        monkeypatch.setattr(control, 'read_json', lambda *_args, **_kwargs: response)
        with pytest.raises(RuntimeError):
            invoke('stop', managed)


def test_stop_uses_pidfd_and_rechecks_identity_before_signalling(managed, monkeypatch):
    calls = []
    live = True
    monkeypatch.setattr(control, 'identity', lambda _pid: managed.record if live else None)
    monkeypatch.setattr(control.os, 'pidfd_open', lambda _pid: os.open(os.devnull, os.O_RDONLY), raising=False)

    def send(descriptor, requested_signal):
        nonlocal live
        assert os.fstat(descriptor)
        calls.append(requested_signal)
        live = False

    monkeypatch.setattr(control.signal, 'pidfd_send_signal', send, raising=False)
    assert invoke('stop', managed) == 0
    assert calls == [signal.SIGTERM]


def test_stop_does_not_signal_a_pid_reused_during_pidfd_open(managed, monkeypatch):
    replacement = False
    monkeypatch.setattr(control, 'identity', lambda _pid: {**managed.record, 'start': '200'} if replacement else managed.record)

    def pidfd(_pid):
        nonlocal replacement
        replacement = True
        return os.open(os.devnull, os.O_RDONLY)

    monkeypatch.setattr(control.os, 'pidfd_open', pidfd, raising=False)
    monkeypatch.setattr(control.signal, 'pidfd_send_signal', lambda *_args: pytest.fail('Reused PID'), raising=False)
    assert invoke('stop', managed) == 0


def test_stop_timeout_reports_stopping_without_force_kill(managed, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(control.os, 'pidfd_open', lambda _pid: os.open(os.devnull, os.O_RDONLY), raising=False)
    monkeypatch.setattr(control.signal, 'pidfd_send_signal', lambda _fd, sig: calls.append(sig), raising=False)
    assert invoke('stop', managed) == 1
    assert calls == [signal.SIGTERM]
    assert capsys.readouterr().out == 'status: stopping\n'


def test_unready_existing_process_does_not_spawn_a_duplicate(managed, monkeypatch, capsys):
    monkeypatch.setattr(control, 'owns_listener', lambda _pid, _port: False)
    monkeypatch.setattr(control.subprocess, 'Popen', lambda *_args, **_kwargs: pytest.fail('Duplicate process'))
    assert invoke('status', managed) == 1
    assert capsys.readouterr().out == 'status: not-ready\n'
    with pytest.raises(RuntimeError, match='尚未就绪'):
        invoke('start', managed)


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux pidfd API')
def test_ctypes_fallback_checks_only_this_test_process_without_sending_a_signal(monkeypatch):
    monkeypatch.delattr(control.os, 'pidfd_open', raising=False)
    monkeypatch.delattr(control.signal, 'pidfd_send_signal', raising=False)
    descriptor = control.open_pidfd(os.getpid())
    try:
        # Signal 0 checks existence/permission and delivers no signal.
        control.signal_pidfd(descriptor, 0)
    finally:
        os.close(descriptor)


def test_missing_pidfd_support_refuses_an_unsafe_fallback(monkeypatch):
    monkeypatch.delattr(control.os, 'pidfd_open', raising=False)
    monkeypatch.delattr(control.signal, 'pidfd_send_signal', raising=False)
    monkeypatch.setattr(control.ctypes, 'CDLL', lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(control.os, 'kill', lambda *_args: pytest.fail('Unsafe PID signal'))
    with pytest.raises(RuntimeError, match='pidfd'):
        control.open_pidfd(123)
    with pytest.raises(RuntimeError, match='pidfd'):
        control.signal_pidfd(123, signal.SIGTERM)
