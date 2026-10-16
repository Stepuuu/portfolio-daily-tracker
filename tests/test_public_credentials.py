"""Prevent credentials from returning to shipped defaults; all values are synthetic."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_check', ROOT / 'scripts/check_public_artifacts.py')
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


@pytest.mark.parametrize('field', ['api_key', 'Authorization', 'x-api-key', 'SECRET_KEY', 'password', 'token'])
def test_default_credentials_are_rejected_without_printing_value(field):
    # No token prefix or entropy threshold: even short credentials must be rejected.
    sentinel = 'synthetic' + '-credential'
    source = f'def _default_config():\n    return {{"nested": {{{field!r}: {sentinel!r}}}}}\n'
    findings = scanner.default_credential_findings(source)
    assert findings == [(2, 'credential in shipped default configuration')]
    assert sentinel not in repr(findings)


def test_computed_credentials_cannot_bypass_check():
    source = 'def _default_config():\n    return {"api_key": "part" + "two"}\n'
    assert scanner.default_credential_findings(source)


def test_empty_defaults_are_allowed_without_executing_code():
    source = 'raise RuntimeError("must not import")\ndef _default_config():\n    return {"api_key": "", "Authorization": "Bearer ", "token": None}\n'
    assert scanner.default_credential_findings(source) == []


def test_repository_defaults_are_credential_free():
    assert scanner.default_credential_findings((ROOT / 'dashboard/config/settings.py').read_text()) == []


def test_unconfigured_install_has_no_credentials(tmp_path, monkeypatch):
    from config.settings import Config
    monkeypatch.setenv('TRACKER_CONFIG_FILE', str(tmp_path / 'config.json'))
    for name in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    config = Config()
    assert config.get_current_api_group()['api_key'] == ''
    assert all(not group.get('api_key') for group in config.get_all_api_groups().values())


def test_official_env_key_never_goes_to_third_party(tmp_path, monkeypatch):
    from config.settings import Config
    monkeypatch.setenv('TRACKER_CONFIG_FILE', str(tmp_path / 'config.json'))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-official-key')
    config = Config()
    config.switch_api_group('third_party')
    assert config.get_current_api_group()['api_key'] == ''
    assert 'synthetic-official-key' not in config.config_file.read_text()
