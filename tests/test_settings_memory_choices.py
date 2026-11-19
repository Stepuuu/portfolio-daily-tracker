"""The 设置 page mirrors backend lists in TypeScript; these keep the copies equal. Reads source text only.

* 交易偏好: the profile's choices are the memory model's enums with its own labels, and the sector boxes are
  ``MemoryExtractor.SECTORS`` (the closed set the assistant proposes from).
* 备份与导出: the statement of what ``desk.sqlite3`` holds was written for the schemas registered today; a new
  schema fails here until ``src/features/settings/backup.ts`` names what it stores.
"""
import re
from pathlib import Path

from backend import features
from core import desk_store
from core.memory.extractor import MemoryExtractor
from core.memory.models import ExperienceLevel, RiskTolerance, TradingStyle, UserMemory

FRONTEND = Path(__file__).resolve().parents[1] / 'dashboard' / 'frontend' / 'src' / 'features' / 'settings'
MEMORY_TS = (FRONTEND / 'memory.ts').read_text(encoding='utf-8')


def choices(name):
    block = re.search(rf'export const {name}: Choice\[\] = \[(.*?)\n\]', MEMORY_TS, re.S).group(1)
    return re.findall(r"\{ value: '([^']+)', label: '([^']+)' \}", block)


def test_the_profile_choices_are_the_backend_enums_with_its_labels():
    memory = UserMemory()
    expected = {
        'EXPERIENCE_CHOICES': [(level.value, memory._translate_experience(level)) for level in ExperienceLevel],
        'STYLE_CHOICES': [(style.value, memory._translate_style(style)) for style in TradingStyle],
        'RISK_CHOICES': [(risk.value, memory._translate_risk(risk)) for risk in RiskTolerance],
    }
    for name, pairs in expected.items():
        assert choices(name) == pairs, name


def test_the_sector_boxes_are_the_extractors_closed_list():
    block = re.search(r'export const SECTORS: string\[\] = \[(.*?)\]', MEMORY_TS, re.S).group(1)
    assert re.findall(r"'([^']+)'", block) == list(MemoryExtractor.SECTORS)


# What desk.sqlite3 holds, as the backup statement says it; update both together.
DESK_SCHEMAS = {
    'account_profiles', 'alerts', 'briefs', 'import_identities', 'import_mappings', 'import_sessions',
    'instrument_aliases', 'notifications', 'plan_link_drafts', 'plans', 'proposals', 'routine_runs', 'setup_state',
    'symbol_index', 'watchlist',
}


def test_every_desk_schema_is_named_in_the_backup_statement():
    features.routers()          # imports every feature, which registers its schema
    features.background_tasks()
    registered = set(desk_store._registered)
    assert registered == DESK_SCHEMAS, (
        'desk.sqlite3 has a new or removed schema: say what it stores in '
        'dashboard/frontend/src/features/settings/backup.ts and update DESK_SCHEMAS here; '
        f'new {sorted(registered - DESK_SCHEMAS)}, gone {sorted(DESK_SCHEMAS - registered)}')
    statement = (FRONTEND / 'backup.ts').read_text(encoding='utf-8')
    for words in ('提醒', '计划', '自选', '放弃', '简报', '例行任务', '导入', '列对应', '别名', '费率', '通知', '设置向导'):
        assert words in statement, words
