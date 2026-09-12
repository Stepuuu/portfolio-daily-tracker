"""Start and stop only this companion's recorded process on Linux."""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time

from .app import load_config, private_text
from .clients import read_json

PROC_ROOT = Path('/proc')


def identity(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        root = PROC_ROOT / str(pid)
        fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'start': fields[19], 'argv': (root / 'cmdline').read_bytes().hex(),
                'boot': (PROC_ROOT / 'sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, ValueError, IndexError):
        return None


def owned_process(record, config_path):
    """A matching PID snapshot must also be this module with this configuration."""
    if not isinstance(record, dict) or identity(record.get('pid')) != record:
        return False
    try:
        argv = bytes.fromhex(record['argv']).decode().rstrip('\0').split('\0')
        return (len(argv) == 6 and Path(argv[0]).resolve() == Path(sys.executable).resolve()
                and argv[1:5] == ['-m', 'feishu_workbench', 'serve', '--config']
                and Path(argv[5]).resolve() == config_path)
    except (ValueError, KeyError, UnicodeError, OSError):
        return False


def owns_listener(pid, port):
    """Bind health checks to the recorded process, not an unrelated HTTP server."""
    try:
        root = PROC_ROOT / str(pid)
        sockets = set()
        for descriptor in (root / 'fd').iterdir():
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue  # A descriptor may close while it is being inspected.
            if target.startswith('socket:[') and target.endswith(']'):
                sockets.add(target[8:-1])
        endpoint = f'0100007F:{port:04X}'
        for line in (root / 'net/tcp').read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and fields[1] == endpoint and fields[3] == '0A' and fields[9] in sockets:
                return True
    except OSError:
        pass
    return False


def health(record, config_path, port, timeout=1):
    if not owned_process(record, config_path) or not owns_listener(record['pid'], port):
        raise RuntimeError('工作台进程尚未就绪，或端口属于其它服务。')
    result = read_json(f'http://127.0.0.1:{port}/health', timeout=timeout)
    if (not isinstance(result, dict) or result.get('ok') is not True
            or not isinstance(result.get('commands'), dict)
            or any(not isinstance(v, int) or isinstance(v, bool) or v < 0
                   for v in result['commands'].values())):
        raise RuntimeError('工作台状态无法核实，请检查私有日志。')
    if not owned_process(record, config_path) or not owns_listener(record['pid'], port):
        raise RuntimeError('工作台进程已变化，请重新检查状态。')
    return result


def read_record(path):
    try:
        record = json.loads(private_text(path))
        return record if isinstance(record, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def open_pidfd(pid):
    if hasattr(os, 'pidfd_open'):
        return os.pidfd_open(pid)
    library = ctypes.CDLL(None, use_errno=True)
    if not hasattr(library, 'pidfd_open'):
        raise RuntimeError('安全停止需要 Linux pidfd 支持；不会使用普通 PID 发送信号。')
    function = library.pidfd_open
    function.argtypes = (ctypes.c_int, ctypes.c_uint)
    function.restype = ctypes.c_int
    descriptor = function(pid, 0)
    if descriptor < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return descriptor


def signal_pidfd(descriptor, signum):
    if hasattr(signal, 'pidfd_send_signal'):
        return signal.pidfd_send_signal(descriptor, signum)
    library = ctypes.CDLL(None, use_errno=True)
    if not hasattr(library, 'pidfd_send_signal'):
        raise RuntimeError('安全停止需要 Linux pidfd 支持；不会使用普通 PID 发送信号。')
    function = library.pidfd_send_signal
    function.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(descriptor, signum, None, 0) < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def stop_owned(record, config_path):
    try:
        descriptor = open_pidfd(record['pid'])
    except ProcessLookupError:
        return
    try:
        if owned_process(record, config_path):
            signal_pidfd(descriptor, signal.SIGTERM)
    except ProcessLookupError:
        pass
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'stop'])
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    port = config.get('port', 18200)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError('工作台端口必须为 1024–65535 的整数。')
    state = Path(config['state_dir'])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    record_path = state / 'process.json'
    descriptor = os.open(state / 'launcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a+b') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError('工作台启动锁必须为当前用户的私有文件。')
        fcntl.flock(lock, fcntl.LOCK_EX)
        record = read_record(record_path)
        owned = owned_process(record, config_path)
        if args.command == 'status':
            if not owned:
                print('status: stopped')
                return 1
            try:
                health(record, config_path, port)
            except Exception:
                print('status: not-ready')
                return 1
            print('status: running')
            return 0
        if args.command == 'stop':
            if owned:
                status = health(record, config_path, port, timeout=3)
                if status.get('commands', {}).get('running'):
                    raise RuntimeError('工作台有操作正在执行，请稍后停止。')
                stop_owned(record, config_path)
                for _ in range(100):
                    if not owned_process(record, config_path):
                        break
                    time.sleep(.1)
            stopping = owned_process(record, config_path)
            print('status: stopping' if stopping else 'status: stopped')
            return 1 if stopping else 0
        if owned:
            health(record, config_path, port)
            print('status: running')
            return 0
        try:
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError('工作台端口已被占用，未启动新进程。') from None
        previous_mask = os.umask(0o077)
        try:
            descriptor = os.open(state / 'service.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'ab', buffering=0) as log:
                os.fchmod(log.fileno(), 0o600)
                process = subprocess.Popen([sys.executable, '-m', 'feishu_workbench', 'serve', '--config', str(config_path)],
                                           cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, start_new_session=True)
        finally:
            os.umask(previous_mask)
        record = identity(process.pid)
        if not owned_process(record, config_path):
            raise RuntimeError('工作台启动失败，请检查私有日志。')
        temporary = record_path.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(record_path)
        for _ in range(50):
            try:
                health(record, config_path, port)
                print('status: running')
                return 0
            except Exception:
                pass
            if not owned_process(record, config_path):
                break
            time.sleep(.2)
        raise RuntimeError('工作台未通过启动检查，请检查私有日志。')


if __name__ == '__main__':
    raise SystemExit(main())
