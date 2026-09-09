#!/usr/bin/env bash
# Stop only launchers registered by this checkout. Never kill all Vite/uvicorn processes.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 - "$SCRIPT_DIR" <<'PY'
import json, os, signal, subprocess, sys
from pathlib import Path
for record in (Path(sys.argv[1]) / '.runtime').glob('*.json'):
    try:
        entry = json.loads(record.read_text())
        pid = int(entry['pid'])
        if pid <= 1:
            continue
        started = subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'args='], text=True)
        if started != entry['started'] or 'start.sh' not in command:
            print(f'Skipped stale process record: {record.name}')
            continue
        os.kill(pid, signal.SIGTERM)
        print(f'Stopped tracker launcher {pid}')
    except (ProcessLookupError, subprocess.CalledProcessError):
        print(f'Already stopped: {record.name}')
    except (OSError, ValueError, KeyError) as exc:
        print(f'Cannot process {record.name}: {type(exc).__name__}', file=sys.stderr)
PY
