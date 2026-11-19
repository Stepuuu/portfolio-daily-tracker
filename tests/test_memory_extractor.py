"""The fixed-rule extractor: a profile choice only from what the user says about themself. Fictional text."""
import pytest

from core.memory import MemoryExtractor


def profile(message):
    """{key: value} of the experience / style / risk choices the rules find in ``message``."""
    return {memory.key: memory.value for memory in MemoryExtractor().extract_from_user_message(message)
            if memory.category == 'profile' and memory.key in ('experience', 'style', 'risk')}


def found(message, key):
    return [memory.value for memory in MemoryExtractor().extract_from_user_message(message) if memory.key == key]


@pytest.mark.parametrize('message, expected', [
    ('我是新手，最近很慌', {'experience': 'beginner'}),
    ('我刚开始炒股，想学学怎么止损。', {'experience': 'beginner'}),
    ('我炒股3年了', {'experience': 'intermediate'}),
    ('我有两年的交易经验', {'experience': 'intermediate'}),
    ('说实话我是个老股民。', {'experience': 'expert'}),
    ('我炒股十几年了', {'experience': 'expert'}),
    ('我主要做波段', {'style': 'swing'}),
    ('我一般做日内，T+0 为主', {'style': 'day'}),
    ('我坚持价值投资', {'style': 'value'}),
    ('我比较保守', {'risk': 'conservative'}),
    ('我的风险偏好比较激进。', {'risk': 'aggressive'}),
    ('其实我挺稳健的', {'risk': 'moderate'}),
    ('我觉得我比较激进，我主要做短线', {'risk': 'aggressive', 'style': 'swing'}),
])
def test_a_first_person_statement_about_the_user_sets_the_choice(message, expected):
    assert profile(message) == expected


@pytest.mark.parametrize('message', [
    # the review's case: a question about what a report says of someone else
    '报告里说“这位资深日内交易员很激进”，这是什么意思？',
    '这位资深日内交易员很激进是什么意思',
    # quotations
    '他说：“我是老股民，我很激进。”',
    '「我比较保守」这句话该怎么理解',
    # questions
    '我是新手吗？',
    '我算激进吗',
    '我是不是太保守了',
    '你觉得我是新手还是老手？',
    # third persons
    '我朋友很激进',
    '我们公司的交易员都做日内',
    '他是老股民，做价值投资',
    '这篇文章的作者比较保守',
    # negations
    '我不是新手',
    '我并不激进',
    '我从不做日内',
    '我一点也不保守',
    # hypotheticals
    '如果我是新手，应该怎么开始？',
    '假如我很激进的话，仓位该怎么配？',
    '我要是做日内，手续费会不会太高',
    # other words that merely appear
    '新手常犯的错误有哪些',
    '日内交易的风险大吗',
    '激进的策略回撤也大',
    '这只股票适合长期持有吗？',
])
def test_anything_else_sets_no_choice(message):
    assert profile(message) == {}


@pytest.mark.parametrize('message, trigger', [
    ('最近很慌', ['fear:慌']),
    ('我很担心明天低开', ['fear:担心']),
    ('报告说散户都很慌', []),
    ('他们在恐慌性抛售吗？', []),
    ('我不慌', []),
    ('“别慌”是什么意思', []),
])
def test_emotional_triggers_come_only_from_the_users_own_statements(message, trigger):
    assert found(message, 'emotional_trigger') == trigger


@pytest.mark.parametrize('message, sectors', [
    ('我是新手，最近很慌，比较关注医药', ['医药']),
    ('我主要看半导体和新能源', ['新能源', '半导体']),
    ('研报说医药要涨', []),
    ('医药板块今天怎么了？', []),
])
def test_sectors_come_only_from_the_users_own_statements(message, sectors):
    assert sorted(found(message, 'preferred_sector')) == sorted(sectors)


# ---------------------------------------------------------------- release gate: a sector the user rejects is not preferred

@pytest.mark.parametrize('message, sectors', [
    ('我不喜欢银行股，只喜欢科技股。', ['科技']),          # the gate's case
    ('我不喜欢银行股，只喜欢科技股', ['科技']),
    ('我不喜欢银行但喜欢科技', ['科技']),
    ('我不碰地产，关注新能源', ['新能源']),
    ('回避白酒，看好半导体', ['半导体']),
    ('我不买医药', []),
    ('讨厌军工', []),
    ('远离地产和银行', []),
    ('我不太喜欢光伏和锂电', []),
    ('除了银行我都可以看看', []),
    ('银行股我不碰', []),
    ('对传媒不感兴趣', []),
    ('别买游戏股', []),
    ('不考虑汽车，主要做芯片', ['芯片']),
    # Changed in round 5: a rejection turns down the sector after it (or the one fronted before it when
    # nothing follows), not every sector of its clause; 40868ac stored both, round 4 neither.
    ('我喜欢科技不喜欢银行', ['科技']),
    ('我喜欢科技', ['科技']),
    # round 5: a negation about something else rejects no sector (40868ac: 科技; round 4: nothing)
    ('我喜欢科技股不想追高', ['科技']),
    ('我喜欢科技股，不想追高', ['科技']),
    ('我看好半导体但不想追高', ['半导体']),
    ('关注新能源，不碰期货', ['新能源']),
    ('银行股我不碰了', []),
    ('不碰银行也不买地产', []),
])
def test_a_sector_the_user_rejects_is_never_a_preferred_sector(message, sectors):
    assert sorted(found(message, 'preferred_sector')) == sorted(sectors)
    assert set(MemoryExtractor().rejected_sectors(message)).isdisjoint(sectors)


def test_the_rejected_sectors_of_a_message():
    assert set(MemoryExtractor().rejected_sectors('我不喜欢银行股，只喜欢科技股。')) == {'银行'}
    assert set(MemoryExtractor().rejected_sectors('远离地产和银行，科技还行')) == {'地产', '银行'}
    assert MemoryExtractor().rejected_sectors('我喜欢科技') == []
    assert MemoryExtractor().rejected_sectors('我喜欢科技不喜欢银行') == ['银行']
    assert MemoryExtractor().rejected_sectors('我喜欢科技股不想追高') == []
    assert set(MemoryExtractor().rejected_sectors('银行股我不碰')) == {'银行'}
