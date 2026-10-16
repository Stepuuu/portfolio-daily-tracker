#!/usr/bin/env python3
"""Check tracked files for common private artifacts without printing matched values."""
from pathlib import Path
import ast
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'machine-specific path': re.compile(r'/' + r'Users/[^/\s]+/|/inspire/(?:hdd|ssd)/|/' + r'home/(?!user/|example/)[^/\s]+/'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'access token': re.compile(r'\b(?:ghp_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|sk-(?:proj-|ant-)[A-Za-z0-9_-]{30,})\b'),
    'private cloud document link': re.compile(r'(?:docs\.google\.com/(?:document|spreadsheets|presentation)/d/|drive\.google\.com/(?:drive/(?:u/\d+/)?folders/|file/d/)|[A-Za-z0-9-]+\.(?:feishu\.cn|larksuite\.com)/(?:docx|wiki|sheets|base)/)[A-Za-z0-9_-]{20,}'),
    'messaging identifier': re.compile(r'\b(?:oc_|ou_)[a-f0-9]{20,}\b'),
}
PRIVATE_PARTS = {'.runtime', '.demo', '.secrets', '.omx', '.codex', 'node_modules', '__pycache__'}
PRIVATE_SUFFIXES = {'.sqlite3', '.db', '.log', '.xlsx', '.docx'}


def default_credential_findings(content):
    """Reject credentials in shipped Python defaults, regardless of token format.

    Parse without importing the configuration (which could write runtime files).
    Only return line numbers/categories, never the matched values.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [(1, 'configuration cannot be parsed')]
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != '_default_config':
            continue
        for mapping in ast.walk(node):
            if not isinstance(mapping, ast.Dict):
                continue
            for key, value in zip(mapping.keys, mapping.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value.lower() not in {'api_key', 'authorization', 'x-api-key', 'secret_key', 'password', 'token'}:
                    continue
                # Defaults must be empty; environment credentials are read at use time.
                empty = isinstance(value, ast.Constant) and value.value in ('', None, 'Bearer ')
                if not empty:
                    findings.append((value.lineno, 'credential in shipped default configuration'))
    return findings


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
        if relative.as_posix() == 'dashboard/config/settings.py':
            for line_no, category in default_credential_findings(content):
                failures.append(f'{relative}:{line_no}: {category}')
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
