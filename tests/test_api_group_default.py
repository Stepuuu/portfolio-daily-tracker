"""A configuration without a selected API group falls back to a shipped one."""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api import settings as settings_api
from config.settings import Config, DEFAULT_API_GROUP


@pytest.fixture
def unselected(tmp_path, monkeypatch):
    """Shipped defaults, saved by a version that recorded no current group."""
    monkeypatch.delenv('TRACKER_CONFIG_FILE', raising=False)
    path = tmp_path / 'config.json'
    data = Config(str(tmp_path / 'defaults.json'))._default_config()
    del data['current_api_group']
    path.write_text(json.dumps(data))
    return Config(str(path))


def test_shipped_defaults_select_the_fallback_group(tmp_path, monkeypatch):
    monkeypatch.delenv('TRACKER_CONFIG_FILE', raising=False)
    defaults = Config(str(tmp_path / 'config.json'))._default_config()
    assert defaults['current_api_group'] == DEFAULT_API_GROUP
    assert DEFAULT_API_GROUP in defaults['api_groups']
    example = json.loads((Path(__file__).parents[1] / 'dashboard' / 'config.example.json').read_text())
    assert example['current_api_group'] == DEFAULT_API_GROUP
    assert DEFAULT_API_GROUP in example['api_groups']


def test_missing_selection_resolves_to_an_existing_group(unselected):
    group = unselected.get_current_api_group()
    assert group['name'] == unselected.get_all_api_groups()[DEFAULT_API_GROUP]['name']
    assert unselected.get_available_models()
    assert unselected.switch_model(unselected.get_available_models()[0]['id'])


def test_config_endpoint_reports_an_existing_group(unselected, monkeypatch):
    monkeypatch.setattr(settings_api, 'get_config', lambda: unselected)
    app = FastAPI()
    app.include_router(settings_api.router, prefix='/api/settings')
    body = TestClient(app).get('/api/settings/config').json()
    assert body['current_api_group'] == DEFAULT_API_GROUP
    assert body['current_api_group'] in body['api_groups']
    assert body['available_models'] == body['api_groups'][body['current_api_group']]['models']
