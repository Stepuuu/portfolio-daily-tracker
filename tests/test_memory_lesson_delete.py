"""Deleting a lesson names the lesson, not only its place. Fictional data.

Two tabs show lessons 甲 / 乙 / 丙. One deletes 甲; the other, still showing the old list, deletes 乙 by its old
position, where 丙 now is. That must delete nothing and say so (409, a fixed Chinese text), never 丙.
"""
import json
import sys

import pytest

from core.memory import MemoryManager
from core.memory.manager import MemoryWriteRefused

needs_backend = pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')


def seeded(tmp_path):
    manager = MemoryManager(data_file=str(tmp_path / 'memory.json'))
    for word in ('甲', '乙', '丙'):
        manager.add_lesson(f'事件{word}', 'insight', lesson=f'教训{word}', date='2026-11-17')
    return manager


def texts(manager):
    return [lesson['lesson'] for lesson in manager.get_full_memory()['history']['lessons']]


def test_the_manager_refuses_a_position_that_no_longer_holds_the_named_lesson(tmp_path):
    manager = seeded(tmp_path)
    shown = manager.get_full_memory()['history']['lessons']
    manager.remove_lesson(0, expected=shown[0])
    on_disk = (tmp_path / 'memory.json').read_text(encoding='utf-8')
    with pytest.raises(MemoryWriteRefused, match='没有删除'):
        manager.remove_lesson(1, expected=shown[1])   # 乙's old place, where 丙 is now
    assert texts(manager) == ['教训乙', '教训丙']
    assert (tmp_path / 'memory.json').read_text(encoding='utf-8') == on_disk
    with pytest.raises(MemoryWriteRefused):
        manager.remove_lesson(5, expected=shown[1])   # a place past the end
    manager.remove_lesson(0, expected=shown[1])
    assert texts(manager) == ['教训丙']


class Service:
    def __init__(self, manager):
        self.memory_manager = manager

    def get_memory(self):
        return self.memory_manager.get_full_memory()


@needs_backend
def test_the_api_deletes_only_the_lesson_the_page_named(tmp_path, monkeypatch):
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend import main
    from backend.api import memory
    manager = seeded(tmp_path)
    monkeypatch.setattr(main, 'agent_service', Service(manager))
    app = FastAPI()
    app.include_router(memory.router, prefix='/api/memory')
    client = TestClient(app)

    shown = client.get('/api/memory').json()['history']['lessons']       # both tabs read this list
    assert client.request('DELETE', '/api/memory/history/lesson/0', json={'expect': shown[0]}).status_code == 200
    stale = client.request('DELETE', '/api/memory/history/lesson/1', json={'expect': shown[1]})
    assert stale.status_code == 409
    assert '没有删除' in stale.json()['detail'] and '刷新' in stale.json()['detail']
    assert '丙' not in stale.json()['detail'], 'a fixed text, not the stored words'
    assert texts(manager) == ['教训乙', '教训丙'], '丙 is still there'
    # Read again: 乙 is first now and is deleted.
    fresh = client.get('/api/memory').json()['history']['lessons']
    assert client.request('DELETE', '/api/memory/history/lesson/0', json={'expect': fresh[0]}).status_code == 200
    assert texts(manager) == ['教训丙']
    assert json.loads((tmp_path / 'memory.json').read_text(encoding='utf-8'))['history']['lessons'][0]['lesson'] == '教训丙'
