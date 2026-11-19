"""行情 (自选 /market, 提醒 /alerts), the stock drawer and the global search in a real browser.

A demo portfolio with a confirmed opening balance, scripted quotes and bars (``fake_quotes.json``,
``fake_bars.json``, clock pinned to a Tuesday morning session). Journey: search a code and add it to
the watchlist (two digits and a click away add nothing; a complete code entered at once is added);
move, remove and undo puts it back where it was; write a note (⌘K while typing leaves it alone); open
the stock drawer (quote, daily chart with its labels); set a stop-loss from the drawer and see the
distance as it is typed; the rule is listed with its distance; the scripted price crosses it and one
monitor cycle, run by a process that then exits, fires it: the page says another process checked and
that it cannot vouch for it, and a rule on a stale quote is shown 暂停检查 with the backend's reason;
the fire shows with 记一笔卖出; disable and delete the rule. Then the global search by keyboard (an
input method composing searches nothing, a letter symbol's exchange is asked once and remembered by
the backend, across a reload; a 北交所 code is listed but cannot be picked), Today's 设提醒 (the account
shown and cleared for another instrument) and alert chip, a holding's plan in the drawer, 1100 px and
390 px without sideways scrolling, and the read-only demo. Set TRACKER_SCREENSHOT_DIR to keep
screenshots.
"""
from contextlib import ExitStack
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

from runner_date import create_demo_argv

ROOT = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = "light" if os.environ.get("TRACKER_SCREENSHOT_THEME") == "light" else "dark"
NOW = "2026-11-17T10:32:05+08:00"   # a Tuesday, A-share morning session


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def ready(url, process, seconds=120):
    # A deadline, not a number of tries: on a loaded machine the backend's imports alone can take tens of seconds.
    deadline = time.monotonic() + seconds
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url}: process exited")
        try:
            opener.open(url, timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError(f"{url} did not become ready")


def quotes(white_price="1680.00"):
    """Holdings, one stock to watch (示例白酒 SHA:600519) and two indexes; the rest are unavailable."""
    fresh = "2026-11-17T10:32:02+08:00"
    return {
        "now": NOW,
        "quotes": {
            "SHA:DEMO": {"name": "示例宽基", "price": "61.20", "prev_close": "60.80", "quote_time": fresh},
            "NASDAQ:DEMO": {"name": "示例研究标的", "price": "119.60", "prev_close": "118.90",
                            "quote_time": "2026-11-17T05:00:00+08:00"},
            "SHA:600519": {"name": "示例白酒", "price": white_price, "prev_close": "1660.00", "quote_time": fresh},
            "SHE:000001": {"name": "示例银行", "price": "11.02", "prev_close": "11.10", "quote_time": fresh},
            "IDX:000001": {"name": "上证指数", "price": "3312.45", "prev_close": "3295.30", "quote_time": fresh},
            "IDX:399001": {"name": "深证成指", "price": "10812.30", "prev_close": "10825.29", "quote_time": fresh},
            "IDX:HSI": {"name": "恒生指数", "price": "19880.00", "prev_close": "19820.00",
                        "quote_time": "2026-11-17T10:00:00+08:00"},
        },
        "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90", "age_seconds": 5}},
    }


def bars(end, count, last):
    """Weekday bars up to and including ``end`` (today: the provisional bar), closing at ``last``."""
    import math
    days, day = [], date.fromisoformat(end)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    shape = [1 + 0.06 * math.sin(i / 9) + 0.03 * math.sin(i / 3.1) - 0.0008 * (count - i) for i in range(count)]
    out = []
    for i, day in enumerate(reversed(days)):
        close = round(last * shape[i] / shape[-1], 2)
        out.append({"date": day.isoformat(), "open": close, "high": round(close * 1.01, 2),
                    "low": round(close * .99, 2), "close": close, "volume": 10000 + i})
    return out


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def full_screenshot(page, path):
    """The page scrolls inside <main>: grow the viewport to the content for a whole-page picture."""
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({"width": size["width"], "height": size["height"] + max(0, extra)})
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


def no_sideways_scroll(page, width):
    page.wait_for_timeout(300)
    widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
    assert max(widths) <= width, widths
    main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
    assert main[0] <= main[1], main


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="market-browser-")))
    portfolio = out / "portfolio"
    subprocess.run(create_demo_argv("--output", str(portfolio), "--ledger", "--opening-only",
                                    "--today", NOW[:10]),   # the demo is made for the day the scripted clock shows
                   check=True, stdout=subprocess.DEVNULL)
    write_json(portfolio / "fake_quotes.json", quotes())
    write_json(portfolio / "fake_bars.json", {"SHA:600519": bars("2026-11-17", 140, 1680), "SHA:DEMO": bars("2026-11-17", 140, 61.2)})
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, "PORTFOLIO_DIR": str(portfolio), "TRACKER_CONFIG_FILE": str(out / "config.json"),
                "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_QUOTE_SOURCE": "fake", "TRACKER_DEMO_MODE": "0",
                "TRACKER_LAB_ONLY": "0", "PYTHONPATH": str(ROOT / "dashboard"),
                "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    log = stack.enter_context((out / "preview.log").open("w"))
    capture = Path(os.environ["TRACKER_SCREENSHOT_DIR"]) if os.environ.get("TRACKER_SCREENSHOT_DIR") else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def shot(page, name, full=False):
        if capture:
            page.wait_for_timeout(250)   # drawers slide in, toasts pop
            (full_screenshot(page, capture / name) if full else page.screenshot(path=str(capture / name)))

    def seed(code):
        """Runs ``code`` against the portfolio's desk store, as a routine would."""
        subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=out, env=base_env, check=True)

    def backend(**overrides):
        env = {**base_env, **overrides}
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_app:app", "--app-dir", str(ROOT / "tests"),
                                    "--host", "127.0.0.1", "--port", str(backend_port)],
                                   cwd=out, env=env, stdout=log, stderr=log)
        stack.callback(stop, process)
        ready(f"http://127.0.0.1:{backend_port}/health", process)
        return process

    server = backend()
    frontend = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--strictPort"],
                                cwd=ROOT / "dashboard/frontend", env=base_env, stdout=log, stderr=log)
    stack.callback(stop, frontend)
    ready(f"http://127.0.0.1:{frontend_port}", frontend)
    url = f"http://127.0.0.1:{frontend_port}"
    api = f"http://127.0.0.1:{backend_port}/api"
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                                              headless=True, args=["--no-sandbox"])
        page = browser.new_page(color_scheme=SCHEME, viewport={"width": 1440, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        searched = []
        page.on("request", lambda request: searched.append(request.url) if "/api/instruments/search" in request.url else None)
        writes = []
        page.on("request", lambda request: writes.append(f"{request.method} {request.url.split('/api/', 1)[-1]}")
                if request.method in ("POST", "PATCH", "DELETE") and "/api/" in request.url else None)
        toasts = page.get_by_role("region", name="通知")

        try:
            # -- 自选: the index line, an empty list with its next step, add by search -----------------
            page.goto(url + "/market")
            strip = page.get_by_role("list", name="主要指数")
            expect(strip).to_contain_text("上证", timeout=60000)
            expect(strip).to_contain_text("3,312.45")
            expect(strip).to_contain_text("+0.52%")
            expect(strip.get_by_role("button", name=re.compile("恒生"))).to_contain_text("已过期")
            expect(strip.get_by_role("button", name=re.compile("纳指"))).to_contain_text("不可用")
            expect(page.get_by_text("还没有自选")).to_be_visible()
            assert page.get_by_role("navigation", name="行情").get_by_role("link").all_inner_texts() == ["自选", "提醒"]
            add = page.get_by_role("combobox", name="添加自选")
            page.get_by_role("button", name="添加第一只").click()
            expect(add).to_be_focused()
            listbox = page.get_by_role("listbox", name="代码搜索结果")
            # An empty field lists the holdings first, tagged 持仓.
            expect(listbox.get_by_role("option").first).to_contain_text("示例宽基")
            expect(listbox.get_by_role("option").first).to_contain_text("持仓")
            add.fill("600519")
            option = listbox.get_by_role("option").first
            expect(option).to_contain_text("600519")
            expect(option).to_contain_text("上交所 · CNY")
            expect(page.get_by_text("名称索引暂不可用", exact=False)).to_be_visible()
            shot(page, "market-add-search.png")
            add.press("Enter")
            expect(toasts.get_by_text("已加入自选", exact=False)).to_be_visible()
            table = page.get_by_role("table", name="自选")
            row = table.locator("tbody tr").filter(has_text="SHA:600519").first
            expect(row).to_contain_text("示例白酒")
            expect(row).to_contain_text("1,680.00")
            expect(row).to_contain_text("+1.20%")
            expect(row).to_contain_text("+20.00")
            price = row.locator("td").nth(1)
            assert price.evaluate("node => getComputedStyle(node).textAlign") == "right"
            assert "tabular-nums" in price.evaluate("node => getComputedStyle(node).fontVariantNumeric")
            expect(add).to_have_value("")
            # Two digits and a click elsewhere: the field keeps the text and nothing is added.
            add.fill("60")
            expect(listbox.get_by_role("option").first).to_be_visible()
            before = len(writes)
            add.evaluate("node => node.blur()")   # focus leaves the field (a click elsewhere)
            page.wait_for_timeout(1500)
            assert not [item for item in writes[before:] if item.startswith("POST watchlist")], writes[before:]
            expect(add).to_have_value("60")
            expect(table.locator("tbody tr")).to_have_count(1)
            # A second one: a complete code entered at once (Enter waits for the answer), then moved up.
            add.fill("SHE:000001")
            add.press("Enter")
            bank = table.locator("tbody tr").filter(has_text="SHE:000001").first
            expect(bank).to_contain_text("示例银行")
            expect(bank).to_contain_text("-0.72%")
            expect(add).to_have_value("")
            bank.hover()
            with page.expect_response(lambda response: response.request.method == "PATCH" and "/api/watchlist/" in response.url):
                bank.get_by_role("button", name="上移 示例银行").click()
            expect(table.locator("tbody tr").first).to_contain_text("示例银行")
            # Removed and put back before the next poll: back at the top, where it was on screen.
            bank.hover()
            bank.get_by_role("button", name="移出 示例银行").click()
            expect(table.locator("tbody tr")).to_have_count(1)
            with page.expect_response(lambda response: response.request.method == "PATCH" and "/api/watchlist/" in response.url):
                toasts.get_by_role("button", name="撤销").click()
            page.reload()
            expect(table.locator("tbody tr").first).to_contain_text("示例银行", timeout=20000)
            expect(table.locator("tbody tr")).to_have_count(2)
            results["watchlist_add"] = "passed"

            # -- a note, edited in place -------------------------------------------------------------
            row.hover()
            row.get_by_role("button", name="给 示例白酒 添加备注").click()
            note = row.get_by_role("textbox", name="示例白酒 的备注")
            expect(note).to_be_focused()
            note.fill("等回调到 1,600 再看")
            # ⌘K while typing: the note keeps the focus and its text, and nothing is saved by a blur.
            before = len(writes)
            page.keyboard.press("Control+k")
            page.wait_for_timeout(300)
            expect(note).to_be_focused()
            expect(note).to_have_value("等回调到 1,600 再看")
            assert not writes[before:], writes[before:]
            note.press("Enter")
            expect(toasts.get_by_text("备注已保存")).to_be_visible()
            expect(row.get_by_role("button", name=re.compile("修改备注"))).to_have_text("等回调到 1,600 再看")
            expect(row.get_by_role("button", name=re.compile("修改备注"))).to_be_focused()
            page.reload()
            expect(table.locator("tbody tr").filter(has_text="SHA:600519").first).to_contain_text("等回调到 1,600 再看", timeout=20000)
            shot(page, "market-desktop.png", full=True)
            results["note"] = "passed"

            # -- the stock drawer: quote, chart with its labels; a stop-loss set from it ----------------
            table.locator("tbody tr").filter(has_text="SHA:600519").first.get_by_text("示例白酒", exact=True).click()
            drawer = page.get_by_role("dialog", name="示例白酒")
            expect(drawer).to_be_visible()
            expect(drawer.get_by_text("1,680.00").first).to_be_visible()
            expect(drawer.get_by_role("img", name=re.compile("收盘价走势"))).to_be_visible()
            expect(drawer.get_by_text("前复权")).to_be_visible()
            expect(drawer.get_by_text("模拟日线")).to_be_visible()
            expect(drawer.get_by_text("最后一根为盘中数据")).to_be_visible()
            expect(drawer.get_by_text("等回调到 1,600 再看")).to_be_visible()
            expect(drawer.get_by_role("button", name="移出自选")).to_be_visible()
            shot(page, "market-drawer.png")
            drawer.get_by_role("button", name="设提醒").click()
            dialog = page.get_by_role("dialog", name="新建提醒")
            expect(dialog).to_be_visible()
            threshold = dialog.get_by_label("止损价")
            expect(threshold).to_be_focused()
            expect(dialog.get_by_label("标的")).to_have_value("SHA:600519")
            threshold.fill("1500")
            expect(dialog.get_by_text("现价 1,680.00 · 距止损 -10.7%")).to_be_visible()
            shot(page, "alert-dialog.png")
            threshold.fill("0")
            dialog.get_by_role("button", name="保存提醒").click()
            expect(dialog.get_by_text("止损价和目标价必须大于 0。")).to_be_visible()   # the API's own words, on the field
            threshold.fill("1500")
            dialog.get_by_role("button", name="保存提醒").click()
            expect(dialog).to_have_count(0)
            expect(toasts.get_by_text("已设提醒", exact=False)).to_be_visible()
            expect(drawer.get_by_text("≤ 1,500.00", exact=True)).to_be_visible()
            expect(drawer.get_by_role("button", name="设提醒")).to_be_focused()
            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            results["drawer"] = "passed"

            # -- 提醒: the rule with its distance; a crossing fires; 记一笔卖出; disable and delete --------
            page.get_by_role("navigation", name="行情").get_by_role("link", name="提醒").click()
            expect(page).to_have_url(url + "/alerts")
            rules = page.get_by_role("table", name="提醒规则")
            rule = rules.locator("tbody tr").filter(has_text="示例白酒").first
            expect(rule).to_contain_text("≤ 1,500.00", timeout=20000)
            expect(rule).to_contain_text("1,680.00")
            expect(rule).to_contain_text("差 10.7%")
            expect(rule).to_contain_text("手动")
            expect(page.get_by_text("仅交易时段运行，后端关闭时不会提醒；不是保证成交的止损")).to_be_visible()
            expect(page.get_by_text("未运行")).to_be_visible()   # the fixture backend runs no monitor
            shot(page, "alerts-desktop.png", full=True)
            # A target on the Hang Seng, whose scripted quote is stale: the monitor skips it.
            created = page.request.post(f"{api}/alerts/rules", data={"ticker": "IDX:HSI", "kind": "target_price", "threshold": "25000.004"})
            assert created.ok, created.text()
            hsi_rule = created.json()["id"]
            write_json(portfolio / "fake_quotes.json", quotes(white_price="1490.00"))
            seed("""
    import asyncio
    from core.alerts.monitor import AlertMonitor
    from core.market.runtime import get_market_runtime
    report = asyncio.run(AlertMonitor(get_market_runtime(), interval=5).run_cycle())
    assert report.status != 'standby', report
    """)
            page.reload()
            fired = page.get_by_role("region", name="今日触发")
            expect(fired).to_contain_text("示例白酒 跌破止损 1,500，现价 1,490", timeout=20000)
            expect(rule).to_contain_text("已达到")
            # The cycle ran in a process that has exited: its heartbeat and lease say another process
            # checked at 10:32:05, which this backend cannot vouch for (amber), never a plain 运行中.
            expect(page.get_by_text(re.compile("^另一个进程在检查"))).to_be_visible()
            expect(page.get_by_text("对方上次检查 10:32:05")).to_be_visible()
            expect(page.get_by_text("本页无法确认它此刻仍在运行", exact=False)).to_be_visible()
            expect(page.get_by_text("运行中", exact=True)).to_have_count(0)
            # The stale-quote rule: 暂停检查 with the backend's reason, and its stored digits.
            hsi = rules.locator("tbody tr").filter(has_text="IDX:HSI").first
            expect(hsi).to_contain_text("≥ 25,000.004")
            expect(hsi).to_contain_text("暂停检查")
            expect(hsi).to_contain_text("行情已过期")
            expect(hsi).not_to_contain_text("启用")
            expect(page.get_by_role("table", name="提醒历史").locator("tbody tr")).to_have_count(1)
            shot(page, "alerts-fired.png", full=True)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.get_by_role("list", name="提醒规则")).to_contain_text("暂停检查", timeout=20000)
            no_sideways_scroll(page, 390)
            shot(page, "alerts-skipped-mobile.png", full=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            assert page.request.delete(f"{api}/alerts/rules/{hsi_rule}").ok
            fired.get_by_role("button", name="记一笔卖出").click()
            expect(page.get_by_role("dialog", name="记一笔")).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog", name="记一笔")).to_have_count(0)
            rule.hover()
            rule.get_by_role("button", name="停用").click()
            expect(toasts.get_by_text("已停用", exact=False)).to_be_visible()
            expect(rule.get_by_text("已停用")).to_be_visible()
            rule.hover()
            rule.get_by_role("button", name="删除").click()
            confirm = page.get_by_role("dialog", name="删除这条提醒？")
            expect(confirm.get_by_role("button", name="取消")).to_be_focused()
            confirm.get_by_role("button", name="删除").click()
            expect(toasts.get_by_text("已删除提醒", exact=False)).to_be_visible()
            expect(page.get_by_text("还没有提醒")).to_be_visible()
            expect(page.get_by_role("table", name="提醒历史").locator("tbody tr")).to_have_count(1)
            # 新建提醒: holdings first; a full code typed and tabbed away from is taken as if picked.
            page.get_by_role("button", name="新建提醒").first.click()
            dialog = page.get_by_role("dialog", name="新建提醒")
            ticker_field = dialog.get_by_label("标的")
            expect(ticker_field).to_be_focused()
            expect(listbox.get_by_role("option").first).to_contain_text("示例宽基")
            ticker_field.fill("sha:600519")
            page.keyboard.press("Tab")
            expect(ticker_field).to_have_value("SHA:600519")
            expect(dialog.get_by_text("示例白酒 · 上交所 · CNY")).to_be_visible()
            expect(dialog.get_by_text("现价 1,490.00", exact=True)).to_be_visible()
            expect(dialog.get_by_text("常用阈值按现价 1,490.00", exact=False)).to_be_visible()
            expect(dialog.get_by_label("账户")).to_have_value("")
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            results["alerts"] = "passed"

            # -- the global search by keyboard --------------------------------------------------------
            page.goto(url + "/market")
            expect(table.locator("tbody tr").first).to_be_visible(timeout=20000)
            page.keyboard.press("Control+k")
            search = page.locator("#global-search")
            expect(search).to_be_focused()
            # An input method is composing: nothing is searched until it ends.
            search.dispatch_event("compositionstart")
            page.keyboard.insert_text("白酒")
            page.wait_for_timeout(500)
            assert not any("q=%E7%99%BD%E9%85%92" in item for item in searched), searched
            search.dispatch_event("compositionend")
            # The watched SHA:600519 has no stored name; in fake-quote mode its scripted name finds it.
            expect(listbox.get_by_role("option").first).to_contain_text("示例白酒")
            assert any("q=%E7%99%BD%E9%85%92" in item for item in searched), searched
            search.fill("示例宽")
            expect(listbox.get_by_role("option").first).to_contain_text("示例宽基")
            page.keyboard.press("ArrowDown")
            expect(search).to_have_attribute("aria-activedescendant", re.compile("opt-0"))
            page.keyboard.press("Enter")
            drawer = page.get_by_role("dialog", name="示例宽基")
            expect(drawer).to_be_visible()
            expect(drawer.get_by_role("region", name="持仓")).to_contain_text("长期账户")
            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            expect(search).to_be_focused()
            expect(search).to_have_value("")
            # A letter symbol on two exchanges: asked once, remembered.
            search.fill("DEMOQ")
            expect(listbox).to_contain_text("DEMOQ 在哪个交易所？选择后会记住")
            shot(page, "search-exchange.png")
            listbox.get_by_role("option").filter(has_text="NYSE").click()
            expect(page.get_by_role("dialog", name="NYSE:DEMOQ")).to_be_visible()
            page.keyboard.press("Escape")
            expect(search).to_be_focused()
            # Remembered by the backend, not only by this page: a reload forgets the page's copy.
            page.reload()
            expect(table.locator("tbody tr").first).to_be_visible(timeout=20000)
            page.keyboard.press("Control+k")
            expect(search).to_be_focused()
            search.fill("DEMOQ")
            expect(listbox.get_by_role("option")).to_have_count(1, timeout=10000)
            expect(listbox.get_by_role("option").first).to_contain_text("NYSE")
            expect(listbox).not_to_contain_text("在哪个交易所")
            # A code the ledger cannot hold is listed with its reason and cannot be picked.
            search.fill("430047")
            blocked = listbox.get_by_role("option").first
            expect(blocked).to_contain_text("北交所")
            expect(blocked).to_have_attribute("aria-disabled", "true")
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            expect(page.locator("dialog[open]")).to_have_count(0)
            page.keyboard.press("Escape")
            expect(listbox).to_have_count(0)
            page.keyboard.press("Escape")
            expect(search).to_have_value("")
            results["global_search"] = "passed"

            # -- Today: the alert chip, 设提醒 from the row, the name opens the drawer -------------------
            assert page.request.post(f"{api}/alerts/rules", data={"ticker": "SHA:DEMO", "kind": "stop_loss", "threshold": "55"}).ok
            page.goto(url + "/")
            holdings = page.get_by_role("table", name="持仓")
            holding = holdings.locator("tbody tr").filter(has_text="SHA:DEMO").first
            expect(holding.get_by_role("button", name="提醒 1")).to_be_visible(timeout=60000)
            holding.hover()
            holding.get_by_role("button", name="更多操作").click()
            page.get_by_role("dialog", name="示例宽基 更多操作").get_by_role("button", name="设提醒").click()
            dialog = page.get_by_role("dialog", name="新建提醒")
            expect(dialog.get_by_label("标的")).to_have_value("SHA:DEMO")
            expect(dialog.get_by_label("止损价")).to_be_focused()
            dialog.get_by_role("button", name=re.compile("^-8%")).click()
            expect(dialog.get_by_label("止损价")).to_have_value("56.3")
            expect(dialog.get_by_text("现价 61.20 · 距止损 -8.0%")).to_be_visible()
            # The row's account is shown, and another instrument does not inherit it.
            account = dialog.get_by_label("账户")
            expect(account).to_have_value("长期账户")
            dialog.get_by_label("标的").fill("sha:600519")
            page.keyboard.press("Tab")
            expect(dialog.get_by_label("标的")).to_have_value("SHA:600519")
            expect(account).to_have_value("")
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(holding.get_by_role("button", name="更多操作")).to_be_focused()
            holding.get_by_role("button", name="示例宽基").click()
            drawer = page.get_by_role("dialog", name="示例宽基")
            expect(drawer.get_by_text("≤ 55.00", exact=True)).to_be_visible()
            expect(drawer.get_by_role("region", name="持仓")).to_contain_text("模拟")   # the holding's quote source
            shot(page, "today-drawer.png")
            page.keyboard.press("Escape")
            # An active plan on the holding: the drawer lists it in the plans package's section (DRAWER_EXTRAS).
            plan = page.request.post(f"{api}/plans", data={"ticker": "SHA:DEMO", "thesis": "虚构计划：抽屉里的计划",
                                                          "invalidation": "跌破 50 就离场", "stop_price": "50"})
            assert plan.ok, plan.text()
            assert page.request.post(f"{api}/plans/{plan.json()['id']}/activate").ok
            page.reload()
            holding.get_by_role("button", name="示例宽基").click()
            plans_section = drawer.get_by_role("region", name="交易计划")
            expect(plans_section).to_contain_text("全部计划", timeout=20000)
            expect(plans_section.get_by_role("listitem")).to_have_count(1)
            expect(plans_section).not_to_contain_text("没有进行中的交易计划")
            shot(page, "drawer-plan.png")
            page.keyboard.press("Escape")
            results["today"] = "passed"

            # -- a laptop and a phone: no sideways scrolling; phones get two-line rows ------------------
            page.set_viewport_size({"width": 1100, "height": 800})
            for path in ("/market", "/alerts"):
                page.goto(url + path)
                expect(page.locator("main table").first).to_be_visible(timeout=20000)
                no_sideways_scroll(page, 1100)
                shot(page, f"{path.strip('/')}-laptop.png", full=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(url + "/market")
            cards = page.get_by_role("list", name="自选")
            expect(cards).to_contain_text("示例白酒", timeout=20000)
            no_sideways_scroll(page, 390)
            shot(page, "market-mobile.png", full=True)
            cards.locator("li").filter(has_text="示例白酒").first.click()
            drawer = page.get_by_role("dialog", name="示例白酒")
            expect(drawer).to_be_visible()
            assert drawer.evaluate("node => node.getBoundingClientRect().width") == 390
            shot(page, "drawer-mobile.png")
            page.keyboard.press("Escape")
            page.goto(url + "/alerts")
            expect(page.get_by_role("list", name="提醒规则")).to_contain_text("示例宽基", timeout=20000)
            no_sideways_scroll(page, 390)
            shot(page, "alerts-mobile.png", full=True)
            # A holding's drawer on a phone: the backend's digits, its source and time, and the plan.
            page.get_by_role("list", name="提醒规则").locator("li").filter(has_text="示例宽基").first.click()
            drawer = page.get_by_role("dialog", name="示例宽基")
            expect(drawer.get_by_role("region", name="持仓")).to_contain_text("长期账户", timeout=20000)
            no_sideways_scroll(page, 390)
            shot(page, "drawer-holding-mobile.png")
            page.keyboard.press("Escape")
            results["mobile_width"] = 390
            page.set_viewport_size({"width": 1440, "height": 900})
            stop(server)

            # -- the demo: everything readable, every write disabled with its reason --------------------
            server = backend(TRACKER_DEMO_MODE="1")
            page.goto(url + "/market")
            expect(page.get_by_text("数据为虚构，只读")).to_be_visible(timeout=30000)
            expect(table.locator("tbody tr").filter(has_text="示例白酒")).to_be_visible(timeout=20000)
            add_button = page.get_by_role("button", name="添加自选")
            expect(add_button).to_have_attribute("aria-disabled", "true")
            assert page.locator(f"[id='{add_button.get_attribute('aria-describedby')}']").text_content() == "演示模式只读"
            expect(page.get_by_role("combobox", name="添加自选")).to_have_count(0)
            table.locator("tbody tr").filter(has_text="示例白酒").first.get_by_text("示例白酒", exact=True).click()
            drawer = page.get_by_role("dialog", name="示例白酒")
            expect(drawer.get_by_role("button", name="移出自选")).to_have_attribute("aria-disabled", "true")
            expect(drawer.get_by_role("button", name="设提醒")).to_have_attribute("aria-disabled", "true")
            shot(page, "market-demo.png")
            page.keyboard.press("Escape")
            page.goto(url + "/alerts")
            expect(page.get_by_role("table", name="提醒规则")).to_contain_text("示例宽基", timeout=20000)
            expect(page.get_by_role("button", name="新建提醒")).to_have_attribute("aria-disabled", "true")
            # The heartbeat the non-demo monitor just wrote is fresh, but no monitor runs in the demo: the
            # monitor line says so, and so does an enabled rule (暂停检查 with that reason, not 启用).
            expect(page.get_by_text("演示模式不检查提醒").first).to_be_visible()
            skipped = page.get_by_role("table", name="提醒规则").locator("tbody tr").filter(has_text="暂停检查")
            expect(skipped.first).to_contain_text("演示模式不检查提醒")
            shot(page, "alerts-demo.png", full=True)
            results["demo"] = "passed"

        except Exception:
            if capture:
                page.screenshot(path=str(capture / "failure.png"))
            print("last searches:", [item.split("?", 1)[-1] for item in searched[-6:]], file=sys.stderr)
            raise
        assert not errors, errors
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors},
                     ensure_ascii=False))
