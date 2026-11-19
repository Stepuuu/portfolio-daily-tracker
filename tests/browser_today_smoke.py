"""今日 (/) and the app shell in a real browser, on disposable fictional data.

A demo portfolio with a confirmed ledger and scripted quotes (``fake_quotes.json``, clock pinned to a
Tuesday morning session). Checks: holdings render right-aligned; a stale quote, a missing one and a
full outage show 已过期 / 不可用 and never 0.00 in the affected cells; a position sold out today stays
listed; pending ledger proposals show in the banner and the tray; the assistant opens prefilled; demo
mode disables 记一笔 with its reason; research-only mode shows only 研究 and 设置; 390 px has no
horizontal overflow; no page errors. Set TRACKER_SCREENSHOT_DIR to keep screenshots.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = "light" if os.environ.get("TRACKER_SCREENSHOT_THEME") == "light" else "dark"
NOW = "2026-11-17T10:32:05+08:00"   # a Tuesday, A-share morning session; New York closed
AFFECTED = ["现价", "涨跌幅", "今日盈亏", "市值", "持仓盈亏", "收益率", "占比"]


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


def quotes(sha=None, nasdaq=None, outage=None):
    """The scripted quote file: by default a live A-share quote and last night's New York close."""
    script = {
        "now": NOW,
        "quotes": {
            "SHA:DEMO": sha or {"name": "示例宽基", "price": "61.20", "prev_close": "60.80",
                                "quote_time": "2026-11-17T10:32:02+08:00"},
            "NASDAQ:DEMO": nasdaq or {"name": "示例研究标的", "price": "119.60", "prev_close": "118.90",
                                      "quote_time": "2026-11-17T05:00:00+08:00"},
        },
        "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90", "age_seconds": 5}},
    }
    if outage:
        script["outage"] = outage
    return script


def write_quotes(directory, script):
    (directory / "fake_quotes.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def cells(page, ticker):
    """Header text -> cell text for the table row of ``ticker``."""
    table = page.get_by_role("table", name="持仓")
    headers = [text.strip() for text in table.locator("thead th").all_inner_texts()]
    row = table.locator("tbody tr").filter(has_text=ticker).first
    values = [text.strip() for text in row.locator("td").all_inner_texts()]
    return dict(zip(headers, values))


def full_screenshot(page, path):
    """The page scrolls inside <main>: grow the viewport to the content for a whole-page picture."""
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({"width": size["width"], "height": size["height"] + max(0, extra)})
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="today-browser-")))
    portfolio = out / "portfolio"
    subprocess.run([sys.executable, str(ROOT / "engine/scripts/create_demo.py"), "--output", str(portfolio), "--ledger", "--opening-only",
                    "--today", NOW[:10]],   # the demo is made for the day the scripted clock shows
                   check=True, stdout=subprocess.DEVNULL)
    write_quotes(portfolio, quotes())
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, "PORTFOLIO_DIR": str(portfolio), "TRACKER_CONFIG_FILE": str(out / "config.json"),
                "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_QUOTE_SOURCE": "fake", "TRACKER_DEMO_MODE": "0",
                "TRACKER_LAB_ONLY": "0", "PYTHONPATH": str(ROOT / "dashboard"),
                "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    log = stack.enter_context((out / "preview.log").open("w"))
    capture = Path(os.environ["TRACKER_SCREENSHOT_DIR"]) if os.environ.get("TRACKER_SCREENSHOT_DIR") else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def seed(code):
        """Runs ``code`` against the portfolio's desk store, as an agent tool or a routine would."""
        subprocess.run([sys.executable, "-c", code], cwd=out, env=base_env, check=True)

    def backend(**overrides):
        """A backend on the fixed port (Vite proxies to it); ``app`` picks the application module."""
        app = overrides.pop("app", "browser_app:app")
        env = {**base_env, **overrides}
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", app, "--app-dir", str(ROOT / "tests"),
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

        # -- holdings at live prices ------------------------------------------------------------
        page.goto(url + "/")
        table = page.get_by_role("table", name="持仓")
        expect(table.get_by_text("示例宽基", exact=True)).to_be_visible(timeout=60000)
        expect(table.get_by_text("示例研究标的", exact=True)).to_be_visible()
        expect(page.get_by_text("总资产", exact=True)).to_be_visible()
        row = cells(page, "SHA:DEMO")
        assert row["现价"] == "61.20" and row["涨跌幅"] == "+0.66%" and row["今日盈亏"] == "+400.00", row
        assert row["持仓"] == "1,000" and row["市值"] == "61,200.00", row
        # The weight is the backend's own digits (two places), not rounded again to one.
        served = {r["ticker"]: r for a in page.request.get(f"{api}/today").json()["accounts"] for r in a["positions"]}
        assert row["占比"] == served["SHA:DEMO"]["weight_pct"] + "%", (row, served["SHA:DEMO"]["weight_pct"])
        value = table.locator("tbody tr").filter(has_text="SHA:DEMO").first.locator("td").nth(6)
        assert value.evaluate("node => getComputedStyle(node).textAlign") == "right"
        assert "tabular-nums" in value.evaluate("node => getComputedStyle(node).fontVariantNumeric")
        # Last night's New York close: the shipped calendar knows 2026, so it is a verified close.
        us = table.locator("tbody tr").filter(has_text="NASDAQ:DEMO").first
        expect(us.get_by_text("收盘价", exact=False)).to_be_visible()
        assert cells(page, "NASDAQ:DEMO")["现价"] == "$119.60"
        # Costs in a mixed-currency column each carry their currency, CNY included.
        assert cells(page, "NASDAQ:DEMO")["成本"] == "$100.00" and cells(page, "SHA:DEMO")["成本"] == "¥50.00"
        expect(page.get_by_role("button", name="估算")).to_have_count(0)
        expect(page.get_by_text("现金 USD", exact=True)).to_be_visible()
        expect(page.get_by_text("A股 盘中", exact=False).first).to_be_visible()
        assert page.get_by_role("navigation", name="主导航").get_by_role("link").all_inner_texts() == \
            ["今日", "行情", "账本", "计划与复盘", "研究", "设置"]
        if capture:
            full_screenshot(page, capture / "today-desktop.png")
        results["holdings"] = "passed"

        # -- a manual refresh is never overwritten by an older poll that answers after it ----------
        held = []

        def hold_one_poll(route):
            if "refresh=1" in route.request.url or held:
                route.continue_()
            else:
                held.append(route)

        page.route("**/api/today*", hold_one_poll)
        for _ in range(150):   # the next 5 s poll
            if held:
                break
            page.wait_for_timeout(100)
        assert held, "a poll went out"
        older = page.request.get(f"{api}/today").json()
        write_quotes(portfolio, quotes(sha={"name": "示例宽基", "price": "61.50", "prev_close": "60.80",
                                            "quote_time": "2026-11-17T10:32:04+08:00"}))
        page.get_by_role("button", name="刷新").click()
        price = table.locator("tbody tr").filter(has_text="SHA:DEMO").first.locator("td").nth(1)
        expect(price).to_have_text("61.50")
        # The held poll now answers with an older snapshot and a price that was never current.
        for account in older["accounts"]:
            for position in account["positions"]:
                if position["ticker"] == "SHA:DEMO":
                    position["price"] = "1.00"
        older["as_of"] = "2026-11-17T10:31:00+08:00"
        try:
            held[0].fulfill(json=older)
        except Exception:   # the refresh cancelled it: fine, it never lands
            pass
        page.wait_for_timeout(800)
        expect(price).to_have_text("61.50")
        page.unroute("**/api/today*")
        write_quotes(portfolio, quotes())
        page.get_by_role("button", name="刷新").click()
        expect(price).to_have_text("61.20")
        results["refresh_order"] = "passed"

        # The global search is keyboard-reachable (its journey is in browser_market_smoke.py).
        page.keyboard.press("/")
        assert page.evaluate("document.activeElement.id") == "global-search"
        assert page.evaluate("document.activeElement.getAttribute('role')") == "combobox"
        page.keyboard.press("Escape")

        # -- notifications: unread count, list, mark read ---------------------------------------
        seed("""
from core import notify
notify.publish('alert', 'critical', '止损提醒：示例宽基 SHA:DEMO', '示例宽基（SHA:DEMO）现价 49.95，已跌到止损价 50 或以下。这只是提醒，不是委托单。', dedupe_key='smoke:1')
notify.publish('routine.close_snapshot', 'info', '收盘快照已生成', '2026-11-16 的收盘快照已保存。', dedupe_key='smoke:2')
""")
        page.reload()
        bell = page.get_by_role("button", name="通知，2 条未读")
        expect(bell).to_be_visible(timeout=20000)
        bell.click()
        inbox = page.get_by_role("dialog", name="通知列表")
        expect(inbox.get_by_text("止损提醒：示例宽基 SHA:DEMO")).to_be_visible()
        if capture:
            page.wait_for_timeout(200)
            page.screenshot(path=str(capture / "today-notifications.png"))
        # A mark-read the server refuses is undone: the row is unread again, the count comes back and a
        # second click retries.
        page.route("**/api/notifications/*/read", lambda route: route.fulfill(status=503, json={"detail": "暂时不可用"}))
        inbox.get_by_text("收盘快照已生成").click()
        expect(inbox.get_by_text("标记已读失败", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="通知，2 条未读")).to_be_visible()
        page.unroute("**/api/notifications/*/read")
        inbox.get_by_text("收盘快照已生成").click()
        expect(page.get_by_role("button", name="通知，1 条未读")).to_be_visible()
        page.keyboard.press("Escape")
        # Persisted, not only shown: after a reload the server still counts one unread.
        page.reload()
        expect(page.get_by_role("button", name="通知，1 条未读")).to_be_visible(timeout=20000)
        assert page.request.get(f"{api}/notifications").json()["unread"] == 1
        # A tab opened in the background does not ask for notifications until it is shown.
        background = browser.new_page(color_scheme=SCHEME, viewport={"width": 1440, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai")
        background.on("pageerror", lambda error: errors.append(str(error)))
        background.add_init_script("""
            window.__shown = false;
            Object.defineProperty(Document.prototype, 'visibilityState', { configurable: true, get() { return window.__shown ? 'visible' : 'hidden' } });
            Object.defineProperty(Document.prototype, 'hidden', { configurable: true, get() { return !window.__shown } });
        """)
        bell_requests = []
        background.on("request", lambda request: bell_requests.append(request.url) if "/api/notifications" in request.url else None)
        background.goto(url + "/")
        expect(background.get_by_role("table", name="持仓").get_by_text("示例宽基", exact=True)).to_be_visible(timeout=60000)
        background.wait_for_timeout(1500)
        assert bell_requests == [], bell_requests
        background.evaluate("window.__shown = true; document.dispatchEvent(new Event('visibilitychange'))")
        expect(background.get_by_role("button", name="通知，1 条未读")).to_be_visible(timeout=20000)
        assert bell_requests, "shown: the bell asks at once"
        background.close()
        results["notifications"] = "passed"

        # -- waiting for confirmation: banner, header chip, tray; a generic one confirmed in place
        seed("""
from core import proposals
import core.alerts
proposals.propose('alert_rule', {'ticker': 'SHA:DEMO', 'kind': 'stop_loss', 'threshold': '55'}, source='agent:chat')
""")
        proposal = page.request.post(f"{api}/ledger/proposals", data={"events": [
            {"kind": "buy", "date": "2026-11-17", "account": "长期账户", "ticker": "SHA:DEMO", "currency": "CNY",
             "quantity": "100", "price": "61.20", "fee": "5"}]}).json()
        page.reload()
        banner = page.get_by_role("region", name="待确认账本方案")
        expect(banner).to_contain_text("尚未入账")
        expect(banner).to_contain_text("11-17 买入 SHA:DEMO 100 股")
        page.get_by_role("button", name="待确认 2 项").click()
        tray = page.get_by_role("dialog", name="待确认")
        expect(tray.get_by_text("11-17 买入 SHA:DEMO 100 股")).to_be_visible()
        alert_group = tray.get_by_role("region", name="提醒方案")
        expect(alert_group).to_be_visible()
        if capture:
            page.wait_for_timeout(400)   # the drawer slides in
            page.screenshot(path=str(capture / "today-tray.png"))
        alert_group.get_by_role("button", name="确认").click()
        expect(page.get_by_role("region", name="通知").get_by_text("已确认", exact=False)).to_be_visible()
        expect(alert_group).to_have_count(0)
        # The ledger proposal is reviewed in place, in the tray; Today's link opens the ledger page's review.
        tray.get_by_role("button", name="核对方案").click()
        expect(tray.get_by_role("region", name="核对入账")).to_contain_text("1 笔待入账")
        expect(page).to_have_url(f"{url}/")
        page.keyboard.press("Escape")
        page.get_by_role("link", name="核对方案", exact=False).first.click()
        expect(page).to_have_url(f"{url}/ledger?proposal={proposal['id']}")
        expect(page.get_by_role("dialog", name="核对入账")).to_be_visible()
        # An old /portfolio link keeps its query and hash through the redirect.
        page.goto(f"{url}/portfolio?proposal={proposal['id']}#events")
        expect(page).to_have_url(f"{url}/ledger?proposal={proposal['id']}#events")
        page.request.delete(f"{api}/ledger/proposals/{proposal['id']}")
        page.goto(url + "/")
        expect(page.get_by_text("今天还没有触发提醒（已启用 1 条规则）。")).to_be_visible()
        # The alert history failing is 不可用, never "nothing fired"; the research notes failing is said too,
        # not a missing 今日事项 panel.
        page.route("**/api/alerts/events*", lambda route: route.fulfill(status=503, json={"detail": "提醒记录维护中"}))
        page.route("**/api/ledger/journal*", lambda route: route.fulfill(status=503, json={"detail": "笔记维护中"}))
        page.reload()
        expect(page.get_by_text("提醒记录暂不可用", exact=False)).to_be_visible(timeout=20000)
        expect(page.get_by_text("今天还没有触发提醒", exact=False)).to_have_count(0)
        expect(page.get_by_text("研究笔记的复查日暂时读不到", exact=False)).to_be_visible()
        page.unroute("**/api/alerts/events*")
        page.unroute("**/api/ledger/journal*")
        page.reload()
        expect(page.get_by_text("今天还没有触发提醒（已启用 1 条规则）。")).to_be_visible()
        results["pending"] = "passed"

        # -- the assistant opens prefilled from a row ------------------------------------------
        page.goto(url + "/")
        expect(table.get_by_text("示例宽基", exact=True)).to_be_visible()
        first = table.locator("tbody tr").filter(has_text="SHA:DEMO").first
        first.hover()
        first.get_by_role("button", name="更多操作").click()
        page.get_by_role("dialog", name="示例宽基 更多操作").get_by_role("button", name="问助手").click()
        dock = page.get_by_role("complementary", name="助手")
        expect(dock).to_be_visible()
        # Only the exchange code goes in: a name from an import or a quote provider never reaches the model
        # as the person's own words.
        expect(dock.locator("form input[type=text]")).to_have_value("关于 SHA:DEMO：")
        if capture:
            page.screenshot(path=str(capture / "today-assistant.png"))
        dock.get_by_role("button", name="关闭助手").first.click()
        expect(dock).to_be_hidden()
        results["assistant"] = "passed"

        # -- one stale quote: the totals stay numeric and are marked 估算 --------------------------
        write_quotes(portfolio, quotes(
            sha={"name": "示例宽基", "price": "61.00", "prev_close": "60.80", "quote_time": "2026-11-17T10:00:00+08:00"}))
        page.get_by_role("button", name="刷新").click()
        expect(table.locator("tbody tr").filter(has_text="SHA:DEMO").first.get_by_text("已过期 10:00", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="估算").first).to_be_visible()

        # Total assets unknown while the profits are estimated (the backend's case: a stale holding plus
        # cash without a rate): each figure that is still shown carries its own 估算, and a percentage the
        # backend could not compute says 不可用 instead of vanishing.
        def unknown_total(route):
            response = route.fetch()
            body = response.json()
            body["totals"].update({"total_cny": None, "day_pnl_pct": None, "quality": "estimated"})
            route.fulfill(response=response, json=body)

        page.route("**/api/today*", unknown_total)
        page.get_by_role("button", name="刷新").click()
        figures = page.locator("main dl")
        expect(figures.filter(has_text="总资产").first.get_by_text("不可用", exact=True)).to_be_visible()
        day_figure = figures.filter(has_text="今日盈亏").first
        expect(day_figure.get_by_role("button", name="估算")).to_be_visible()
        expect(day_figure.get_by_text("不可用", exact=True)).to_be_visible()
        expect(figures.filter(has_text="总盈亏").first.get_by_role("button", name="估算")).to_be_visible()
        page.unroute("**/api/today*")

        # -- stale and missing quotes: chips, 不可用, never 0.00 ---------------------------------
        write_quotes(portfolio, quotes(
            sha={"name": "示例宽基", "price": "61.00", "prev_close": "60.80", "quote_time": "2026-11-17T10:00:00+08:00"},
            nasdaq={"name": "示例研究标的", "unavailable": "停牌"}))
        page.get_by_role("button", name="刷新").click()
        sha = table.locator("tbody tr").filter(has_text="SHA:DEMO").first
        expect(sha.get_by_text("已过期 10:00", exact=True)).to_be_visible()
        us = table.locator("tbody tr").filter(has_text="NASDAQ:DEMO").first
        expect(us.get_by_text("不可用", exact=True).first).to_be_visible()
        row = cells(page, "NASDAQ:DEMO")
        for column in AFFECTED:
            assert row[column] == "不可用", (column, row)
        assert row["持仓"] == "20", row
        expect(page.get_by_text("不可用（1 项缺价）").first).to_be_visible()
        assert cells(page, "SHA:DEMO")["现价"] == "61.00"
        if capture:
            full_screenshot(page, capture / "today-stale.png")
        write_quotes(portfolio, quotes(outage="数据源维护"))
        page.get_by_role("button", name="刷新").click()
        # Wait on the price cell itself: the row already shows 不可用 (占比) before the outage lands.
        for ticker in ("SHA:DEMO", "NASDAQ:DEMO"):
            expect(table.locator("tbody tr").filter(has_text=ticker).first.locator("td").nth(1)).to_have_text("不可用")
        for ticker in ("SHA:DEMO", "NASDAQ:DEMO"):
            row = cells(page, ticker)
            for column in AFFECTED:
                assert row[column] == "不可用", (ticker, column, row)
                assert "0.00" not in row[column], (ticker, column, row)
        figures = page.locator("main dl").first.inner_text()
        assert "不可用" in figures and "0.00" not in figures, figures
        results["outage"] = "passed"

        # -- a sub-dollar quote keeps its digits in the stock drawer, as on Today ------------------------
        write_quotes(portfolio, quotes(nasdaq={"name": "示例研究标的", "price": "0.8437", "prev_close": "0.84",
                                               "quote_time": "2026-11-17T05:00:00+08:00"}))
        page.get_by_role("button", name="刷新").click()
        expect(table.locator("tbody tr").filter(has_text="NASDAQ:DEMO").first.locator("td").nth(1)).to_have_text("$0.8437")
        table.get_by_role("button", name="示例研究标的", exact=True).click()
        drawer_quote = page.get_by_role("dialog").filter(has_text="NASDAQ:DEMO").get_by_role("region", name="行情")
        expect(drawer_quote.get_by_text("$0.8437", exact=True)).to_be_visible(timeout=20000)
        expect(drawer_quote.get_by_text("+0.0037", exact=True)).to_be_visible()
        expect(drawer_quote.get_by_text("+0.44%", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        results["sub_dollar"] = "passed"

        # -- a position sold out today stays listed with its day profit -------------------------
        write_quotes(portfolio, quotes())
        sale = page.request.post(f"{api}/ledger/proposals", data={"events": [
            {"kind": "sell", "date": "2026-11-17", "account": "长期账户", "ticker": "SHA:DEMO", "currency": "CNY",
             "quantity": "1000", "price": "61.00", "fee": "5"}]}).json()
        assert page.request.post(f"{api}/ledger/proposals/{sale['id']}/confirm").ok
        page.get_by_role("button", name="刷新").click()
        sold = table.locator("tbody tr").filter(has_text="SHA:DEMO").first
        expect(sold.get_by_text("今日清仓", exact=True)).to_be_visible()
        assert cells(page, "SHA:DEMO")["持仓"] == "0"
        assert cells(page, "SHA:DEMO")["今日盈亏"] not in ("", "不可用")
        results["closed_today"] = "passed"

        # -- a laptop: the icon rail, and the assistant over the page ---------------------------
        page.set_viewport_size({"width": 1100, "height": 800})
        page.goto(url + "/")
        expect(table.get_by_text("示例研究标的", exact=True)).to_be_visible()
        rail = page.locator("aside").first
        assert rail.evaluate("node => node.getBoundingClientRect().width") == 56
        assert page.get_by_role("navigation", name="主导航").get_by_role("link", name="账本").is_visible()
        header_assistant = page.get_by_role("banner").get_by_role("button", name="助手")
        header_assistant.click()
        dock = page.get_by_role("complementary", name="助手")
        expect(dock).to_be_visible()
        assert dock.evaluate("node => getComputedStyle(node).position") == "fixed", "below 1440 px the dock lies over the page"
        # Opening moves focus into the dock, with or without prefilled text.
        page.wait_for_function("""document.querySelector('aside[aria-label="助手"]')?.contains(document.activeElement)""")
        if capture:
            page.screenshot(path=str(capture / "today-laptop-dock.png"))
        page.keyboard.press("Escape")
        expect(dock).to_be_hidden()
        assert header_assistant.evaluate("node => node === document.activeElement"), "focus returned to 助手"
        results["laptop"] = "passed"

        # -- phones: cards, bottom bar, no sideways scrolling ----------------------------------
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(url + "/")
        expect(page.get_by_role("list", name="持仓")).to_be_visible()
        expect(page.get_by_role("navigation", name="主导航").get_by_role("button", name="记一笔")).to_be_visible()
        page.wait_for_timeout(300)
        widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
        assert max(widths) <= 390, widths
        main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
        assert main[0] <= main[1], main
        if capture:
            full_screenshot(page, capture / "today-mobile.png")
        # The assistant from the keyboard: focus goes into the full-screen panel and stays there; Esc
        # closes it and focus returns to the bottom bar.
        bar_assistant = page.get_by_role("navigation", name="主导航").get_by_role("button", name="助手")
        bar_assistant.focus()
        page.keyboard.press("Enter")
        dock = page.get_by_role("complementary", name="助手")
        expect(dock).to_be_visible()
        inside = """document.querySelector('aside[aria-label="助手"]')?.contains(document.activeElement)"""
        page.wait_for_function(inside)
        for key in ["Tab"] * 15 + ["Shift+Tab"] * 15:
            page.keyboard.press(key)
            assert page.evaluate(inside), f"focus left the full-screen assistant after {key}"
        page.keyboard.press("Escape")
        expect(dock).to_be_hidden()
        assert bar_assistant.evaluate("node => node === document.activeElement"), "focus returned to the bottom bar"
        results["mobile_width"] = 390
        if capture:
            # The same page in the light theme (a saved preference), for review at both widths.
            page.evaluate("localStorage.setItem('pdt.theme', 'light')")
            page.reload()
            expect(page.get_by_role("list", name="持仓")).to_be_visible()
            page.wait_for_timeout(300)
            full_screenshot(page, capture / "today-mobile-light.png")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.reload()
            expect(table.get_by_text("示例宽基", exact=True)).to_be_visible()
            page.wait_for_timeout(300)
            full_screenshot(page, capture / "today-desktop-light.png")
            page.evaluate("localStorage.removeItem('pdt.theme')")
        page.set_viewport_size({"width": 1440, "height": 900})
        stop(server)

        # -- demo mode: read-only, 记一笔 disabled with its reason -------------------------------
        server = backend(TRACKER_DEMO_MODE="1")
        page.goto(url + "/")
        expect(page.get_by_text("数据为虚构，只读")).to_be_visible(timeout=30000)
        entry = page.get_by_role("banner").get_by_role("button", name="记一笔")
        expect(entry).to_have_attribute("aria-disabled", "true")
        reason = page.locator(f"[id='{entry.get_attribute('aria-describedby')}']")
        assert reason.text_content() == "演示模式只读"
        entry.click(force=True)
        expect(page.get_by_role("dialog", name="记一笔")).to_have_count(0)
        if capture:
            page.screenshot(path=str(capture / "today-demo.png"))
        # The status endpoint failing (not absent) is an unknown mode: still read-only, a neutral notice,
        # never the full mode with its write buttons, and no claim that the data is fictional.
        page.route("**/api/app/status", lambda route: route.fulfill(status=503, json={"detail": "暂时不可用"}))
        page.reload()
        expect(page.get_by_text("无法确认运行模式", exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_text("数据为虚构，只读")).to_have_count(0)
        entry = page.get_by_role("banner").get_by_role("button", name="记一笔")
        expect(entry).to_have_attribute("aria-disabled", "true")
        reason = page.locator(f"[id='{entry.get_attribute('aria-describedby')}']")
        assert "运行模式" in reason.text_content(), reason.text_content()
        page.unroute("**/api/app/status")
        # An answer without a mode the shell knows is the same unknown mode, retried; and nothing that
        # writes or sends to the model can be used: the assistant's send, the settings' saves.
        def answer_with(body):
            return lambda route: route.fulfill(status=200, json=body)

        for body in ({}, {"mode": "superuser"}):
            page.route("**/api/app/status", answer_with(body))
            page.reload()
            expect(page.get_by_text("无法确认运行模式", exact=True)).to_be_visible(timeout=30000)
            expect(page.get_by_role("banner").get_by_role("button", name="记一笔")).to_have_attribute("aria-disabled", "true")
            page.unroute("**/api/app/status")
        page.route("**/api/app/status", lambda route: route.fulfill(status=200, json={}))
        page.reload()
        expect(page.get_by_text("无法确认运行模式", exact=True)).to_be_visible(timeout=30000)
        page.get_by_role("banner").get_by_role("button", name="助手").click()
        dock = page.get_by_role("complementary", name="助手")
        expect(dock.get_by_text("暂时无法确认运行模式，修改操作已暂停", exact=False).first).to_be_visible(timeout=30000)
        dock.locator("form input[type=text]").fill("你好")
        expect(dock.get_by_role("button", name="发送")).to_be_disabled()
        expect(dock.get_by_role("button", name="上传图片")).to_be_disabled()
        if capture:
            page.screenshot(path=str(capture / "today-unknown-assistant.png"))
        dock.get_by_role("button", name="关闭助手").first.click()
        page.goto(url + "/settings/models")
        chat_model = page.get_by_role("region", name="对话模型", exact=True)
        expect(chat_model.get_by_role("button", name="保存", exact=True)).to_be_disabled(timeout=30000)
        page.get_by_role("button", name="新增连接").click()
        expect(page.get_by_role("button", name="保存连接")).to_be_disabled()
        page.unroute("**/api/app/status")
        # A 404 is an older backend without the endpoint only when this backend says so in FastAPI's words
        # (and still answers its other routes); a proxy's or a static host's 404 is the unknown mode.
        page.route("**/api/app/status", lambda route: route.fulfill(status=404, content_type="text/html", body="<html>Not Found</html>"))
        page.goto(url + "/")
        expect(page.get_by_text("无法确认运行模式", exact=True)).to_be_visible(timeout=30000)
        page.unroute("**/api/app/status")
        page.route("**/api/app/status", lambda route: route.fulfill(status=404, json={"detail": "Not Found"}))
        page.reload()
        expect(page.get_by_role("banner").get_by_role("button", name="记一笔")).not_to_have_attribute("aria-disabled", "true", timeout=30000)
        expect(page.get_by_text("无法确认运行模式", exact=True)).to_have_count(0)
        page.unroute("**/api/app/status")
        page.reload()
        expect(page.get_by_text("数据为虚构，只读")).to_be_visible(timeout=30000)
        results["demo"] = "passed"

        # -- the backend restarts in another mode while the tab stays open (`make demo`, then `make start`) --
        # No reload: the banner and the write controls follow the backend running now, both ways.
        entry = page.get_by_role("banner").get_by_role("button", name="记一笔")
        stop(server)
        server = backend()
        expect(page.get_by_text("数据为虚构，只读")).to_have_count(0, timeout=90000)
        expect(entry).not_to_have_attribute("aria-disabled", "true", timeout=30000)
        stop(server)
        server = backend(TRACKER_DEMO_MODE="1")
        expect(page.get_by_text("数据为虚构，只读")).to_be_visible(timeout=90000)
        expect(entry).to_have_attribute("aria-disabled", "true")
        results["mode_follows_backend"] = "passed"
        stop(server)

        # -- research-only mode: 研究 and 设置 only; / goes to the workbench ---------------------
        server = backend(TRACKER_LAB_ONLY="1", app="backend.main:app")
        page.goto(url + "/")
        expect(page).to_have_url(f"{url}/lab", timeout=30000)
        nav = page.get_by_role("navigation", name="主导航")
        expect(nav.get_by_role("link")).to_have_count(2)
        assert nav.get_by_role("link").all_inner_texts() == ["研究", "设置"]
        expect(page.get_by_role("button", name="记一笔")).to_have_count(0)
        expect(page.get_by_role("heading", name="股票研究工作台", exact=True)).to_be_visible()
        results["lab_mode"] = "passed"

        assert not errors, errors
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors},
                     ensure_ascii=False))
