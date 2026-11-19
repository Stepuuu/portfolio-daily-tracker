#!/usr/bin/env python3
"""Generate fictional portfolio history offline, in a NEW directory only.

With ``--refresh`` (what ``make demo`` runs) an existing directory is used when it is a demo of this
version made with the same options, rebuilt when it is recognisably a generated demo of another version
(``demo_directory``) and holds nothing but what the generator wrote (``foreign_content``), and refused
otherwise, untouched."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from portfolio_accounting import atomic_json_dump
from portfolio_snapshot import calculate_snapshot

SNAPSHOT_WEEKS = 4
SEEDED_DAYS = 12      # core.demo_seed.HISTORY_DAYS, for the engine-only image that lacks it
SHANGHAI = timezone(timedelta(hours=8))
# The marker every demo directory gets last: what made it. Raise DEMO_FORMAT whenever a demo made before
# would no longer show what this version's demo shows (``make demo`` then rebuilds it). v3.2.1's demo had
# no marker and no ledger: format 1.
MARKER = "demo.json"
DEMO_FORMAT = 2
DEMO_ACCOUNTS = {"长期账户", "研究账户"}
DEMO_TICKERS = {"SHA:DEMO", "NASDAQ:DEMO"}


def history_start(today):
    """The oldest day the seed writes on (the 12th mainland trading day before ``today``)."""
    try:
        import ledger_bridge  # noqa: F401  (puts the dashboard package on sys.path)
        from core.demo_seed import history_days
    except ImportError:
        # The engine-only image seeds no history; count weekdays the way the seed would.
        return _weekday_start(today)
    return min(history_days(today).values())


def _weekday_start(today):
    """``history_start`` without the dashboard package: ``SEEDED_DAYS`` weekdays before ``today``."""
    day, left = today, SEEDED_DAYS
    while left:
        day -= timedelta(days=1)
        left -= day.weekday() < 5
    return day


def snapshot_days(today, start=None):
    """The demo's daily snapshots: four whole weeks, Monday to Friday, ending on the Friday before the
    seeded history starts. The opening balance is dated on the first of them, so everything the demo
    holds is dated before ``today``, whatever day it is made for.

    A ``today`` so close to year 1 that these days do not exist is a ``ValueError`` with the reason. ``start``
    replaces ``history_start(today)``."""
    try:
        last = (start or history_start(today)) - timedelta(days=1)
        while last.weekday() != 4:
            last -= timedelta(days=1)
        first = last - timedelta(days=7 * SNAPSHOT_WEEKS - 3)
    except OverflowError:
        raise ValueError(f"cannot make the demo for {today}: its snapshots begin about two months earlier, "
                         f"before {date.min}, the first date there is") from None
    return [first + timedelta(days=offset) for offset in range(7 * SNAPSHOT_WEEKS) if (first + timedelta(days=offset)).weekday() < 5]


def check_history(today):
    """Refuse, before anything is written, a ``today`` whose seeded history cannot be dated (ValueError)."""
    try:
        import ledger_bridge  # noqa: F401  (puts the dashboard package on sys.path)
        from core.demo_seed import check_history as check
    except ImportError:
        return  # the engine-only image seeds no history
    check(today)


def seed_dashboard_demo(destination, days, ledger, today=None, history=True):
    """Scripted quotes for the dashboard; with ``ledger`` also its confirmed ledger and populated pages."""
    try:
        import ledger_bridge  # noqa: F401  (puts the dashboard package on sys.path)
        from core.demo_seed import seed_demo
    except ImportError:
        if ledger:
            raise
        return  # the engine-only image ships without the dashboard package
    holdings, prices, rates = days[-1]
    seed_demo(destination, holdings, prices, rates, previous_prices=days[-2][1], ledger=ledger,
              opened_on=days[0][0]["date"], today=today, history=history)


def create_demo(destination, ledger=False, today=None, history=True):
    """Write the demo into the new directory ``destination``, made for ``today`` (a date; today in Shanghai by
    default): its snapshots, opening balance and seeded history are all dated before that day.

    ``history=False`` with ``ledger`` confirms only the opening balance."""
    destination = Path(destination)
    today = today or datetime.now(SHANGHAI).date()
    # Every date the demo is made of is worked out first: a day without room for them is refused
    # (ValueError) before the directory exists.
    snapshot_dates = snapshot_days(today)
    if ledger and history:
        check_history(today)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "holdings").mkdir()
    (destination / "snapshots").mkdir()
    atomic_json_dump({"groups": {"长期账户": {}, "研究账户": {}}}, destination / "config.json")
    snapshots, days = [], []
    # The prices follow the calendar-day offset from the first snapshot, as when the dates were fixed.
    for day in snapshot_dates:
        index = (day - snapshot_dates[0]).days
        holdings = {"date": day.isoformat(), "groups": {
            "长期账户": {"cost_basis": 81000, "cash": 20000, "fund": 0,
                "cash_balances": {"CNY": 20000, "USD": 1000, "HKD": 5000},
                "positions": [{"name": "示例宽基", "ticker": "SHA:DEMO", "quantity": 1000, "cost_price": 50}]},
            "研究账户": {"cost_basis": 29000, "cash": 10000, "fund": 5000,
                "positions": [{"name": "示例研究标的", "ticker": "NASDAQ:DEMO", "quantity": 20, "cost_price": 100}]},
        }}
        prices = {"SHA:DEMO": 50 + index * .4 - (6 if 9 <= index <= 15 else 0),
                  "NASDAQ:DEMO": 100 + index * .7}
        rates = {"CNY": 1, "USD": 7 + index * .002, "HKD": .9}
        snapshot = calculate_snapshot(holdings, prices, {"NASDAQ:DEMO": "USD"}, rates,
                                      snapshots[-1] if snapshots else None, snapshots)
        snapshot["demo"] = True
        snapshot["generated_at"] = day.isoformat() + "T16:00:00+08:00"
        atomic_json_dump(holdings, destination / "holdings" / f"{day}.json")
        atomic_json_dump(snapshot, destination / "snapshots" / f"{day}.json")
        snapshots.append(snapshot)
        days.append((holdings, prices, rates))
    seed_dashboard_demo(destination, days, ledger, today, history)
    # Last: a directory left half-made has no marker (``--refresh`` makes its demo beside the directory).
    atomic_json_dump(_marker(ledger, history, today), destination / MARKER)
    return len(snapshots)


def _marker(ledger, history, today):
    return {"demo": True, "format": DEMO_FORMAT, "ledger": bool(ledger), "history": bool(ledger and history),
            "made_for": today.isoformat()}


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def demo_directory(path):
    """Whether ``path`` is a directory this script generated (of any version), so it may be rebuilt.

    Its marker says so, or it has v3.2.1's layout and nothing but the demo's fictional data in it: the
    configuration names exactly the demo's two accounts, there are snapshots and every one is marked
    ``demo``, and every holdings file holds only the invented tickers. A symbolic link never is one.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        return False
    marker = _json(path / MARKER)
    if isinstance(marker, dict) and marker.get("demo") is True:
        return True
    config = _json(path / "config.json")
    if not isinstance(config, dict) or set(config.get("groups") or {}) != DEMO_ACCOUNTS:
        return False
    snapshots = sorted((path / "snapshots").glob("*.json"))
    if not snapshots or any(not isinstance(_json(item), dict) or _json(item).get("demo") is not True
                            for item in snapshots):
        return False
    for item in (path / "holdings").glob("*.json"):
        holdings = _json(item)
        groups = holdings.get("groups") if isinstance(holdings, dict) else None
        if not isinstance(groups, dict) or set(groups) - DEMO_ACCOUNTS:
            return False
        tickers = {position.get("ticker") for group in groups.values() if isinstance(group, dict)
                   for position in group.get("positions") or [] if isinstance(position, dict)}
        if tickers - DEMO_TICKERS:
            return False
    return True


class NotADemo(ValueError):
    """``--refresh`` was given a path that exists but is not a demo directory; nothing was touched."""


# What the generator writes in a demo directory, and what SQLite and the portfolio write lock keep beside its
# two databases. A refresh deletes a directory only when it holds nothing else.
GENERATED = {MARKER, "config.json", "fake_quotes.json", "holdings", "snapshots", "ledger.sqlite3", "desk.sqlite3"}
BESIDE = {".portfolio-update.lock"} | {f"{name}{suffix}" for name in ("ledger.sqlite3", "desk.sqlite3")
                                       for suffix in ("-wal", "-shm", "-journal")}
CONFIG = {"groups": {"长期账户": {}, "研究账户": {}}}
DATED_JSON = re.compile(r"\d{4}-\d{2}-\d{2}\.json")
# Tables the application fills by itself: schema versions, its caches, the alerts it fired, notifications,
# routine runs and the briefs they made, proposal side effects, the setup page's progress. Every other
# table's rows (the ledger's events and previews; the watchlist, plans, rules, proposals, imports, aliases,
# account profiles, ...) were written by someone and must be the generator's.
SYSTEM_TABLES = {"sqlite_sequence", "ledger_meta", "schema_versions", "alert_state", "alert_events",
                 "alert_monitor", "alert_monitor_lease", "alert_meta", "notifications", "routine_runs", "briefs",
                 "symbol_index", "symbol_index_lists", "effects", "setup_state"}
# What differs between two runs of the generator for the same day: random identifiers (uuid4 hex) and a
# pending proposal's expiry, which follows the real clock.
RANDOM_ID = re.compile(r"\b[0-9a-f]{32}\b")
REAL_CLOCK_COLUMNS = {"expires_ts"}




# v3.2.1's generator (it wrote no marker) wrote holdings for each weekday of 2026-08-03 to 2026-08-30.
V321_DAYS = [date(2026, 8, 3) + timedelta(days=offset) for offset in range(28)
             if (date(2026, 8, 3) + timedelta(days=offset)).weekday() < 5]


def _holdings(days):
    """The holdings files the generator writes for ``days`` (v3.2.1's and this version's): alike but for the date."""
    return {f"{day}.json": {"date": day.isoformat(), "groups": {
        "长期账户": {"cost_basis": 81000, "cash": 20000, "fund": 0,
            "cash_balances": {"CNY": 20000, "USD": 1000, "HKD": 5000},
            "positions": [{"name": "示例宽基", "ticker": "SHA:DEMO", "quantity": 1000, "cost_price": 50}]},
        "研究账户": {"cost_basis": 29000, "cash": 10000, "fund": 5000,
            "positions": [{"name": "示例研究标的", "ticker": "NASDAQ:DEMO", "quantity": 20, "cost_price": 100}]},
    }} for day in days}


class NotOnlyTheDemo(NotADemo):
    """``--refresh`` would have rebuilt a demo directory that holds something the generator did not write."""


def _rows(path, directory):
    """``{table: Counter(row)}`` of the SQLite file ``path``, read without writing; ``{}`` without the file.

    A row is its columns and values, with random identifiers, real-clock columns and the directory's own path
    left out, so the generator's rows of one day compare equal wherever and whenever they were written.
    """
    import ledger_bridge  # noqa: F401  (puts the dashboard package, or the image's core/, on the path)
    from core.ledger import read_only_connection
    if not path.is_file():
        return {}
    places = {str(Path(directory).resolve()), str(Path(directory).absolute())}

    def plain(value):
        if isinstance(value, str):
            for place in places:
                value = value.replace(place, "<directory>")
            return RANDOM_ID.sub("<id>", value)
        return value
    tables = {}
    with read_only_connection(path, timeout=5) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in names:
            if name in SYSTEM_TABLES:
                continue
            cursor = db.execute(f'SELECT * FROM "{name}"')
            columns = [column[0] for column in cursor.description]
            tables[name] = Counter(tuple((column, plain(value)) for column, value in zip(columns, row)
                                         if column not in REAL_CLOCK_COLUMNS) for row in cursor)
    return tables


def _recorded(destination):
    """``(ledger, history, day)`` the directory was made with, from its marker; v3.2.1's demo, which has
    none, had no ledger and no desk (``day`` None). ``None`` when the marker cannot say."""
    if not (destination / MARKER).exists():
        return False, False, None
    marker = _json(destination / MARKER)
    if not isinstance(marker, dict) or type(marker.get("ledger")) is not bool or type(marker.get("history")) is not bool:
        return None
    try:
        return marker["ledger"], marker["history"], date.fromisoformat(marker.get("made_for"))
    except (TypeError, ValueError):
        return None


def foreign_content(destination):
    """What in the demo directory ``destination`` the generator did not write, as a short text, or ``None``.

    ``None`` only when that is proven: every entry is one the generator writes (``GENERATED``, or a file
    SQLite or the write lock keeps beside them, ``BESIDE``); the configuration is the demo's; the holdings and
    snapshots are dated JSON files of the demo's accounts and invented tickers, every snapshot marked
    ``demo``; every holdings file is one the generator wrote, as it wrote it (for the options and day the marker
    records, or v3.2.1's); and every row of the ledger and the desk, outside the tables the application fills
    by itself (``SYSTEM_TABLES``), is one the generator writes for those options and that day, made again
    elsewhere to compare. Reads only.
    """
    recorded = _recorded(destination)
    if recorded is None:
        return "demo.json 无法读取或不完整"
    ledger, history, day = recorded
    for entry in sorted(destination.iterdir()):
        if entry.name not in GENERATED | BESIDE:
            return f"文件 {entry.name}"
        if entry.is_symlink() or (entry.name in ("holdings", "snapshots")) != entry.is_dir():
            return f"文件 {entry.name}"
    for folder in ("holdings", "snapshots"):
        for item in sorted((destination / folder).iterdir()) if (destination / folder).is_dir() else ():
            if item.is_symlink() or not item.is_file() or not DATED_JSON.fullmatch(item.name):
                return f"文件 {folder}/{item.name}"
    if _json(destination / "config.json") != CONFIG or not _fictional(destination):
        return "配置、持仓或快照"
    with tempfile.TemporaryDirectory() as scratch:
        reference = Path(scratch) / "demo"
        if ledger:
            try:
                create_demo(reference, ledger=True, today=day, history=history)
            except ImportError:
                return "无法核对：此安装没有演示数据的生成程序"
        try:
            # Made with the dashboard, the snapshots end before the seeded history's mainland trading days; in
            # the engine-only image, before as many weekdays.
            made = _holdings(V321_DAYS if day is None else
                             {*snapshot_days(day), *snapshot_days(day, start=_weekday_start(day))})
        except (ValueError, OverflowError):
            return "demo.json 记录的日期无法核对"
        for item in sorted((destination / "holdings").glob("*.json")):
            if item.name not in made or _json(item) != made[item.name]:
                return f"文件 holdings/{item.name}"
        for name, label in (("ledger.sqlite3", "账本"), ("desk.sqlite3", "工作台数据")):
            try:
                found, made = _rows(destination / name, destination), _rows(reference / name, reference)
            except Exception:   # unreadable, busy or not a database: it cannot be proven to be the demo's
                return f"{label}（{name}）无法读取"
            for table, rows in found.items():
                if rows - made.get(table, Counter()):
                    return f"{label}的 {table} 中有不是演示数据的记录"
    return None


def _fictional(destination):
    """Only the demo's accounts and invented tickers in the holdings, and every snapshot marked ``demo``."""
    snapshots = sorted((destination / "snapshots").glob("*.json"))
    if not snapshots or any(not isinstance(_json(item), dict) or _json(item).get("demo") is not True
                            for item in snapshots):
        return False
    for item in (destination / "holdings").glob("*.json"):
        holdings = _json(item)
        groups = holdings.get("groups") if isinstance(holdings, dict) else None
        if not isinstance(groups, dict) or set(groups) - DEMO_ACCOUNTS:
            return False
        tickers = {position.get("ticker") for group in groups.values() if isinstance(group, dict)
                   for position in group.get("positions") or [] if isinstance(position, dict)}
        if tickers - DEMO_TICKERS:
            return False
    return True


def _made_beside(destination, ledger, today, history):
    """The demo made in a new sibling of ``destination``; removed again when making it fails or is interrupted."""
    staging = destination.parent / f".{destination.name}.new-{uuid.uuid4().hex}"
    try:
        create_demo(staging, ledger=ledger, today=today, history=history)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def refresh_demo(destination, ledger=False, today=None, history=True):
    """``created``, ``kept`` or ``rebuilt``: the demo in ``destination`` as ``create_demo`` would make it.

    A demo of this version made with the same options is kept as it is. A directory that is a demo
    (``demo_directory``) of another version or made otherwise is made again only when it holds nothing but
    what the generator wrote (``foreign_content``); otherwise ``NotOnlyTheDemo`` is raised. Anything else
    raises ``NotADemo``. Refused directories are left exactly as they were. The new demo is made beside the
    directory and then swapped in, so an interrupted run leaves the old one whole. Nothing else is touched.
    """
    destination = Path(destination)
    if not destination.exists() and not destination.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(_made_beside(destination, ledger, today, history), destination)
        return "created"
    if not demo_directory(destination):
        raise NotADemo(f"{destination} 不是演示数据目录（不是本脚本生成的），未做任何改动；"
                       "请换一个目录，或确认其中没有需要的数据后自己删除它")
    marker = _json(destination / MARKER) or {}
    wanted = _marker(ledger, history, today or datetime.now(SHANGHAI).date())
    if all(marker.get(key) == wanted[key] for key in ("format", "ledger", "history")):
        return "kept"
    # Every date is checked before anything is read or made: a day the demo cannot be made for changes nothing.
    snapshot_days(today or datetime.now(SHANGHAI).date())
    if ledger and history:
        check_history(today or datetime.now(SHANGHAI).date())
    found = foreign_content(destination)
    if found is not None:
        raise NotOnlyTheDemo(f"{destination} 里有不是演示数据生成程序写入的内容（{found}），未做任何改动；"
                             "请换一个目录，或确认其中没有需要的数据后自己删除它")
    staging = _made_beside(destination, ledger, today, history)
    retired = destination.parent / f".{destination.name}.old-{uuid.uuid4().hex}"
    os.rename(destination, retired)
    try:
        os.rename(staging, destination)
    except BaseException:
        os.rename(retired, destination)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(retired, ignore_errors=True)
    return "rebuilt"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", action="store_true",
                        help="also confirm a fictional opening balance and a few ledger events on recent trading days, and fill the "
                             "watchlist, trade plans, alert rules (one fired) and a pending proposal, so every page has data")
    parser.add_argument("--opening-only", action="store_true",
                        help="with --ledger: confirm the opening balance and nothing else")
    parser.add_argument("--today", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                        help="make the demo for this day (default: today in Shanghai); everything in it is dated before it")
    parser.add_argument("--refresh", action="store_true",
                        help="the output may exist: keep a demo of this version made with these options, rebuild an "
                             "older demo in place (one by v3.2.1 has no ledger), refuse a directory that is not a demo")
    args = parser.parse_args(argv)
    make = refresh_demo if args.refresh else create_demo
    try:
        done = make(args.output, ledger=args.ledger, today=args.today, history=not args.opening_only)
    except ValueError as exc:
        parser.error(str(exc))   # its own refusal: a day it cannot make the demo for, or not a demo directory
    if not args.refresh:
        print(f"Created {done} fictional snapshots in {args.output}")
        return
    print({"created": f"已生成演示数据：{args.output}",
           "kept": f"沿用已有的演示数据：{args.output}",
           "rebuilt": f"已按当前版本重新生成演示数据（原目录是旧版本的演示）：{args.output}"}[done])


if __name__ == "__main__":
    main()
