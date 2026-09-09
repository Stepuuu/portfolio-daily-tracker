#!/usr/bin/env python3
"""Check tracked files for common private artifacts without printing matched values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'machine-specific path': re.compile(r'/' + r'Users/[^/\s]+/|/inspire/(?:hdd|ssd)/|/' + r'home/(?!user/|example/)[^/\s]+/'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'access token': re.compile(r'\b(?:ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-(?:proj-|ant-)[A-Za-z0-9_-]{30,})\b'),
    'messaging identifier': re.compile(r'\b(?:oc_|ou_)[a-f0-9]{20,}\b'),
}
PRIVATE_PARTS = {'.runtime', '.demo', '.secrets', '.omx', '.codex', 'node_modules', '__pycache__'}
PRIVATE_SUFFIXES = {'.sqlite3', '.db', '.log', '.xlsx', '.docx'}


def main():
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    for raw in paths:
        if not raw:
            continue
        relative = Path(raw.decode())
        path = ROOT / relative
        if not path.is_file():
            continue
        if PRIVATE_PARTS.intersection(relative.parts) or path.suffix in PRIVATE_SUFFIXES:
            failures.append(f'{relative}: private artifact')
        if path.name in {'.env', 'config.json', 'portfolio.json', 'ledger.sqlite3'}:
            failures.append(f'{relative}: runtime data/configuration')
        try:
            content = path.read_text()
        except UnicodeError:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                if pattern.search(line):
                    failures.append(f'{relative}:{line_no}: {category}')
    for failure in failures:
        print(failure)
    print(f'Public artifact check: {len(failures)} findings across {sum(bool(p) for p in paths)} tracked paths')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
