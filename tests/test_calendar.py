"""Session phases, the shipped holiday tables and what happens without one."""
import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from core.market import calendar
from core.market.calendar import HOLIDAY_FILE, SHANGHAI, SessionClock, load_early_closes, load_holidays
from tests.fakes import FakeClock, shanghai

CLOSED_WEEKDAYS_2026 = [
    '2026-01-01', '2026-01-02', '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
    '2026-02-23', '2026-04-06', '2026-05-01', '2026-05-04', '2026-05-05', '2026-06-19', '2026-09-25',
    '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07']
# HKEX circulars CT/075/25 (2026) and CT/077/26 (2027); NYSE Holidays & Trading Hours.
HK_CLOSED = {
    2026: ['2026-01-01', '2026-02-17', '2026-02-18', '2026-02-19', '2026-04-03', '2026-04-06', '2026-04-07',
           '2026-05-01', '2026-05-25', '2026-06-19', '2026-07-01', '2026-10-01', '2026-10-19', '2026-12-25'],
    2027: ['2027-01-01', '2027-02-08', '2027-02-09', '2027-03-26', '2027-03-29', '2027-04-05', '2027-05-13',
           '2027-06-09', '2027-07-01', '2027-09-16', '2027-10-01', '2027-10-08', '2027-12-27'],
}
US_CLOSED = {
    2026: ['2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03',
           '2026-09-07', '2026-11-26', '2026-12-25'],
    2027: ['2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18', '2027-07-05',
           '2027-09-06', '2027-11-25', '2027-12-24'],
}
HK_HALF_DAYS = ['2026-02-16', '2026-12-24', '2026-12-31', '2027-02-05', '2027-12-24', '2027-12-31']
US_EARLY_CLOSES = ['2026-11-27', '2026-12-24', '2027-11-26']


def clock_at(text):
    return FakeClock(text).session()


def iso(days):
    return sorted(day.isoformat() for day in days)


def test_shipped_table_lists_exactly_the_2026_mainland_closures():
    table = load_holidays()
    assert sorted(day.isoformat() for day in table['CN'][2026]) == CLOSED_WEEKDAYS_2026
    assert all(date.fromisoformat(day).weekday() < 5 for day in CLOSED_WEEKDAYS_2026)
    # Changed with the HK/US tables: mainland 2027 is not shipped until the exchanges publish it.
    assert set(table) == {'CN', 'HK', 'US'} and set(table['CN']) == {2026}


def test_shipped_table_lists_exactly_the_hong_kong_and_new_york_closures():
    table = load_holidays()
    assert {year: iso(days) for year, days in table['HK'].items()} == HK_CLOSED
    assert {year: iso(days) for year, days in table['US'].items()} == US_CLOSED
    for listed in list(HK_CLOSED.values()) + list(US_CLOSED.values()):
        assert all(date.fromisoformat(day).weekday() < 5 for day in listed)


def test_shipped_early_closes_are_the_hong_kong_half_days_and_the_new_york_one_o_clock_closes():
    early = load_early_closes()
    assert set(early) == {'HK', 'US'}
    assert {day.isoformat(): close for day, close in early['HK'].items()} == {day: time(12, 0) for day in HK_HALF_DAYS}
    assert {day.isoformat(): close for day, close in early['US'].items()} == {day: time(13, 0) for day in US_EARLY_CLOSES}


def test_every_shipped_market_year_names_its_official_source_and_cross_check():
    raw = json.loads(HOLIDAY_FILE.read_text(encoding='utf-8'))
    years = [(market, year, entry) for market, entries in raw.items() if not market.startswith('_')
             for year, entry in entries.items()]
    assert {(market, year) for market, year, _ in years} == {
        ('CN', '2026'), ('HK', '2026'), ('HK', '2027'), ('US', '2026'), ('US', '2027')}
    for market, year, entry in years:
        source = entry['source']
        assert source['url'].startswith('https://'), (market, year)
        assert date.fromisoformat(source['retrieved']) == date(2026, 11, 17), (market, year)
        assert source['title'] and source['cross_check'], (market, year)


def test_2026_has_242_mainland_trading_days():
    clock = clock_at('2026-06-01 10:00')
    days = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(365)]
    assert sum(clock.is_trading_day('CN', day) for day in days) == 261 - len(CLOSED_WEEKDAYS_2026)


@pytest.mark.parametrize('day', CLOSED_WEEKDAYS_2026)
def test_every_2026_holiday_is_closed_all_day(day):
    clock = clock_at(f'{day} 10:30')
    assert not clock.is_trading_day('CN')
    assert clock.phase('CN') == 'closed'
    assert clock.confidence('CN') == 'calendar'


def test_spring_festival_is_in_february_not_january():
    # The scheduler's old table closed 26-30 January 2026; the market was open.
    clock = clock_at('2026-01-27 10:00')
    assert clock.is_trading_day('CN') and clock.phase('CN') == 'morning'
    assert clock.previous_trading_day('CN', date(2026, 2, 24)) == date(2026, 2, 13)


@pytest.mark.parametrize('time,phase', [
    ('08:59:59', 'pre_open'), ('09:14:59', 'pre_open'), ('09:15:00', 'call_auction'), ('09:29:59', 'call_auction'),
    ('09:30:00', 'morning'), ('11:29:59', 'morning'), ('11:30:00', 'lunch_break'), ('12:59:59', 'lunch_break'),
    ('13:00:00', 'afternoon'), ('14:59:59', 'afternoon'), ('15:00:00', 'closed'), ('23:00:00', 'closed')])
def test_mainland_phases_on_a_trading_day(time, phase):
    clock = clock_at(f'2026-11-17 {time}')
    assert clock.phase('CN') == phase
    assert clock.is_trading('CN') == (phase in {'morning', 'afternoon'})


def test_weekend_is_closed_even_though_it_is_not_in_the_holiday_table():
    clock = clock_at('2026-11-14 10:00')   # Saturday
    assert not clock.is_trading_day('CN')
    assert clock.phase('CN') == 'closed'
    assert clock.current_trading_day('CN') == date(2026, 11, 13)
    assert clock.previous_trading_day('CN') == date(2026, 11, 13)
    assert clock.next_open('CN') == shanghai('2026-11-16 09:30')


def test_next_open_and_last_session_end_follow_the_lunch_break_and_holidays():
    assert clock_at('2026-11-17 08:00').next_open('CN') == shanghai('2026-11-17 09:30')
    assert clock_at('2026-11-17 10:00').next_open('CN') == shanghai('2026-11-17 13:00')
    assert clock_at('2026-11-17 12:00').next_open('CN') == shanghai('2026-11-17 13:00')
    assert clock_at('2026-11-17 15:30').next_open('CN') == shanghai('2026-11-18 09:30')
    assert clock_at('2026-09-30 16:00').next_open('CN') == shanghai('2026-10-08 09:30')   # National Day
    assert clock_at('2026-11-17 12:00').last_session_end('CN') == shanghai('2026-11-17 11:30')
    assert clock_at('2026-11-17 08:00').last_session_end('CN') == shanghai('2026-11-16 15:00')
    assert clock_at('2026-10-08 09:00').last_session_end('CN') == shanghai('2026-09-30 15:00')


def test_a_year_without_holiday_data_is_weekday_only_and_says_so():
    clock = clock_at('2027-02-08 10:00')   # a Monday in a year the table does not cover
    assert clock.confidence('CN') == 'weekday_only'
    assert clock.is_trading_day('CN') and clock.phase('CN') == 'morning'
    assert clock.confidence('CN', date(2026, 12, 31)) == 'calendar'
    # The same Monday is a Lunar New Year holiday in Hong Kong, whose 2027 table is known.
    assert clock.confidence('HK') == 'calendar' and not clock.is_trading_day('HK') and clock.phase('HK') == 'closed'


def test_hong_kong_and_us_years_after_the_table_are_weekday_only():
    # Replaces "Hong Kong and US have no holiday data": 2026 and 2027 are now shipped.
    clock = clock_at('2028-12-25 23:30')   # Christmas 2028, a Monday: closed in both, unknown here
    assert clock.confidence('HK') == 'weekday_only' and clock.confidence('US') == 'weekday_only'
    assert clock.is_trading_day('HK') and clock.is_trading_day('US') and clock.phase('US') == 'open'


@pytest.mark.parametrize('market,at', [('HK', '2026-12-25 10:30'), ('US', '2026-12-25 23:30'),
                                       ('HK', '2026-04-07 10:30'), ('US', '2027-07-05 22:00'),
                                       ('HK', '2027-02-09 14:00'), ('US', '2026-11-26 23:00')])
def test_hong_kong_and_new_york_holidays_are_closed_and_known(market, at):
    clock = clock_at(at)
    assert clock.confidence(market) == 'calendar'
    assert not clock.is_trading_day(market) and clock.phase(market) == 'closed' and not clock.is_trading(market)


def test_good_friday_is_found_on_both_calendars_across_the_easter_weekend():
    # 2026-04-03 is Good Friday in both places; Hong Kong also closes Easter Monday's day after (7 April).
    assert clock_at('2026-04-07 10:00').previous_trading_day('HK') == date(2026, 4, 2)
    assert clock_at('2026-04-02 17:00').next_open('HK') == shanghai('2026-04-08 09:30')
    assert clock_at('2026-04-06 10:00').last_session_end('US') == shanghai('2026-04-03 04:00')   # Thursday 16:00 NY


@pytest.mark.parametrize('time,phase', [('09:29', 'closed'), ('09:30', 'open'), ('11:59', 'open'), ('12:00', 'closed'),
                                        ('12:59', 'closed'), ('13:00', 'open'), ('15:59', 'open'), ('16:00', 'closed')])
def test_hong_kong_is_open_or_closed_in_local_time(time, phase):
    assert clock_at(f'2026-11-17 {time}').phase('HK') == phase


def test_us_session_follows_new_york_daylight_saving():
    # 09:30 New York is 21:30 Shanghai in summer and 22:30 in winter.
    assert clock_at('2026-07-15 21:29').phase('US') == 'closed'
    assert clock_at('2026-07-15 21:30').phase('US') == 'open'
    assert clock_at('2026-07-16 03:59').phase('US') == 'open'
    assert clock_at('2026-07-16 04:00').phase('US') == 'closed'
    assert clock_at('2026-11-17 21:30').phase('US') == 'closed'
    assert clock_at('2026-11-17 22:30').phase('US') == 'open'
    assert clock_at('2026-11-18 04:59').phase('US') == 'open'
    assert clock_at('2026-11-18 05:00').phase('US') == 'closed'


def test_us_trading_day_is_the_new_york_date():
    # Saturday morning in Shanghai is still Friday's session in New York.
    clock = clock_at('2026-11-14 03:00')
    assert clock.local_date('US') == date(2026, 11, 13)
    assert clock.is_trading_day('US') and clock.phase('US') == 'open'
    assert not clock.is_trading_day('CN')
    assert clock.next_open('US') == shanghai('2026-11-16 22:30')
    assert clock.last_session_end('US') == shanghai('2026-11-13 05:00')


@pytest.mark.parametrize('market,at,day', [
    ('CN', '2026-11-17 10:00', '2026-11-17'),
    ('CN', '2026-11-17 09:15', '2026-11-17'),     # the call auction belongs to today's session
    ('CN', '2026-11-17 09:14', '2026-11-16'),     # before it, prices are still Monday's
    ('CN', '2026-11-17 20:00', '2026-11-17'),
    ('CN', '2026-11-15 12:00', '2026-11-13'),     # Sunday
    ('CN', '2026-02-22 12:00', '2026-02-13'),     # Spring Festival closure
    ('CN', '2026-10-08 09:00', '2026-09-30'),     # National Day, before the first open after it
    ('HK', '2026-11-17 09:00', '2026-11-17'),     # pre-opening session
    ('HK', '2026-11-17 08:59', '2026-11-16'),
    ('US', '2026-11-17 10:00', '2026-11-16'),     # 21:00 Monday in New York
    ('US', '2026-11-17 22:30', '2026-11-17'),
    ('US', '2026-11-17 21:00', '2026-11-16'),     # 08:00 in New York, before the open
])
def test_session_of_is_the_trading_day_a_moment_belongs_to(market, at, day):
    clock = clock_at('2026-11-17 10:00')
    assert clock.session_of(market, shanghai(at)) == date.fromisoformat(day)
    assert clock_at(at).session_of(market) == date.fromisoformat(day)     # defaults to now


def test_now_is_always_shanghai_time():
    utc = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)
    assert SessionClock(lambda: utc).now() == shanghai('2026-11-17 10:00')
    assert SessionClock(lambda: utc).now().utcoffset() == timedelta(hours=8)
    assert SessionClock(lambda: datetime(2026, 11, 17, 10, 0)).now().tzinfo is SHANGHAI


def test_a_malformed_holiday_table_is_rejected(tmp_path):
    path = tmp_path / 'holidays.json'
    path.write_text('{"CN": {"2026": ["2027-01-01"]}}')
    with pytest.raises(ValueError):
        load_holidays(path)
    path.write_text('{"MARS": {"2026": []}}')
    with pytest.raises(ValueError):
        load_holidays(path)


def test_a_year_may_be_a_plain_list_or_an_object_with_its_source(tmp_path):
    path = tmp_path / 'holidays.json'
    path.write_text(json.dumps({
        '_comment': 'ignored', '_sources': {'anything': 'ignored'},
        'CN': {'2026': ['2026-01-01']},
        'US': {'2026': {'source': {'url': 'https://example.invalid/', 'retrieved': '2026-11-17', 'note': 'free text'},
                        'closed': ['2026-12-25'], 'early_close': {'2026-12-24': '13:00'}}}}))
    assert load_holidays(path) == {'CN': {2026: frozenset({date(2026, 1, 1)})}, 'US': {2026: frozenset({date(2026, 12, 25)})}}
    assert load_early_closes(path) == {'US': {date(2026, 12, 24): time(13, 0)}}


@pytest.mark.parametrize('year', [
    {'closed': ['2026-12-25'], 'early_closes': {}},                       # misspelt key
    {'early_close': {}},                                                  # no closures
    {'closed': '2026-12-25'},                                             # not a list
    {'closed': [], 'early_close': ['2026-12-24']},                        # not a mapping
    {'closed': [], 'early_close': {'2027-12-24': '13:00'}},               # another year
    {'closed': [], 'early_close': {'2026-12-26': '13:00'}},               # a Saturday
    {'closed': ['2026-12-24'], 'early_close': {'2026-12-24': '13:00'}},   # closed all day already
    {'closed': [], 'early_close': {'2026-12-24': '1 pm'}},                # not HH:MM
    {'closed': [], 'early_close': {'2026-12-24': '16:00'}},               # not before the regular close
    {'closed': [], 'early_close': {'2026-12-24': '09:00'}},               # before the open
    {'closed': [], 'source': 'a string'},                                 # source must be an object
])
def test_a_malformed_year_entry_is_rejected(tmp_path, year):
    path = tmp_path / 'holidays.json'
    path.write_text(json.dumps({'US': {'2026': year}}))
    with pytest.raises(ValueError):
        load_holidays(path)


def test_a_table_handed_in_without_early_closes_has_none():
    # Callers (and tests) that pass their own closures get exactly that calendar.
    clock = SessionClock(FakeClock('2026-12-24 14:00'), {'HK': {2026: frozenset()}})
    assert clock.phase('HK') == 'open' and clock.early_close('HK') is None
    clock = SessionClock(FakeClock('2026-12-24 14:00'), {'HK': {2026: frozenset()}},
                         {'HK': {date(2026, 12, 24): time(12, 0)}})
    assert clock.phase('HK') == 'closed' and clock.early_close('HK') == time(12, 0)


# -- early closes ---------------------------------------------------------------

@pytest.mark.parametrize('time_,phase', [('09:29', 'closed'), ('09:30', 'open'), ('11:59', 'open'),
                                         ('12:00', 'closed'), ('13:00', 'closed'), ('15:59', 'closed')])
def test_a_hong_kong_half_day_has_only_the_morning_session(time_, phase):
    clock = clock_at(f'2026-12-24 {time_}')
    assert clock.is_trading_day('HK') and clock.confidence('HK') == 'calendar'
    assert clock.phase('HK') == phase and clock.is_trading('HK') == (phase == 'open')


def test_a_half_day_ends_at_noon_for_every_session_question():
    clock = clock_at('2026-12-24 14:00')
    assert clock.early_close('HK') == time(12, 0)
    assert clock.session_end('HK') == shanghai('2026-12-24 12:00')
    assert clock.last_session_end('HK') == shanghai('2026-12-24 12:00')
    # Christmas Day and the weekend follow: the next open is Monday the 28th.
    assert clock.next_open('HK') == shanghai('2026-12-28 09:30')
    assert clock_at('2026-12-24 11:00').next_open('HK') == shanghai('2026-12-28 09:30'), 'no afternoon session'
    assert clock_at('2026-12-24 08:00').next_open('HK') == shanghai('2026-12-24 09:30')
    assert clock_at('2026-12-28 08:00').last_session_end('HK') == shanghai('2026-12-24 12:00')
    assert clock_at('2026-12-23 14:00').session_end('HK') == shanghai('2026-12-23 16:00')   # an ordinary day


def test_a_new_york_early_close_ends_at_one_in_the_afternoon():
    # Friday 27 November 2026, the day after Thanksgiving: 13:00 New York is 02:00 Saturday in Shanghai.
    assert clock_at('2026-11-28 01:59').phase('US') == 'open'
    assert clock_at('2026-11-28 02:00').phase('US') == 'closed'
    clock = clock_at('2026-11-28 03:00')
    assert clock.is_trading_day('US') and clock.early_close('US') == time(13, 0)
    assert clock.last_session_end('US') == shanghai('2026-11-28 02:00')
    assert clock.session_end('US') == shanghai('2026-11-28 02:00')
    assert clock.next_open('US') == shanghai('2026-11-30 22:30')


def test_without_a_table_an_early_close_day_is_a_full_day():
    # 24 November 2028 will close at 13:00 in New York, but 2028 is not shipped:
    # the calendar knows nothing about it and says so.
    clock = clock_at('2028-11-25 03:00')
    assert clock.confidence('US') == 'weekday_only' and clock.early_close('US') is None
    assert clock.phase('US') == 'open'


def test_hosts_without_a_tz_database_get_equivalent_zones():
    # A broken or missing database must not stop the module from importing.
    fallback = timezone(timedelta(hours=8))
    assert calendar._zone('Nowhere/Missing', fallback) is fallback
    eastern = calendar._USEastern()
    day = date(2026, 1, 1)
    while day < date(2028, 1, 1):
        for wall in (time(9, 30), time(16, 0)):
            instant = datetime.combine(day, wall, tzinfo=calendar.ZONES['US']).astimezone(timezone.utc)
            assert instant.astimezone(eastern).replace(tzinfo=None) == datetime.combine(day, wall), day
            assert datetime.combine(day, wall, tzinfo=eastern).astimezone(timezone.utc) == instant, day
        noon = datetime.combine(day, time(12, 0), tzinfo=timezone.utc)
        assert noon.astimezone(calendar._UTC8).replace(tzinfo=None) == noon.astimezone(SHANGHAI).replace(tzinfo=None)
        day += timedelta(days=1)
