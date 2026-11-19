"""今日简报 (/review) in a real browser, on disposable fictional data.

A demo portfolio with an opening-balance ledger, an active plan for ``SHA:DEMO`` (entry 58–61, stop 54,
target 66) and scripted quotes (``fake_quotes.json``) whose ``now`` drives the clock of the brief
scheduler (``browser_brief_app``). The journey on Tuesday 2026-11-17: at 08:00 both briefs are still to
come; at 09:00 生成 makes the pre-market brief (an index row with its source and time, the distance to
the plan's stop, Hong Kong's missing indices as 不可用, the news section's 数据源不可用 in fake mode);
a buy at 62 breaks the plan; at 15:50 a quote outage makes the post-market run fail with its reason and
重新生成 (and says when it is retried); after the quotes return, 重新生成 makes it, and the plan check
lists 超出计划入场区间 with a link to the plan. Date navigation keeps the chosen date in the URL and
keyboard focus on the control used. 1100 px keeps two columns, 390 px stacks them without sideways
scrolling; demo mode shows the seeded briefs read-only and its start-up writes nothing. No page errors.
Every demo is made for the scenario's day (``--today``), never the real date. Set TRACKER_SCREENSHOT_DIR
to keep screenshots.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
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
DAY = "2026-11-17"   # a Tuesday
INDICES = {   # New York's last session ended at 05:00 Beijing; Hong Kong's indices are not in the script
    "IDX:IXIC": ("纳斯达克综合指数", "19210.35", "19102.80", "2026-11-17T05:00:00+08:00"),
    "IDX:SPX": ("标普500", "6050.12", "6020.00", "2026-11-17T05:00:00+08:00"),
    "IDX:DJIA": ("道琼斯工业指数", "44120.50", "44210.10", "2026-11-17T05:00:00+08:00"),
    "IDX:000001": ("上证指数", "3312.45", "3295.30", "2026-11-16T15:00:00+08:00"),
    "IDX:399001": ("深证成指", "10520.30", "10490.10", "2026-11-16T15:00:00+08:00"),
    "IDX:399006": ("创业板指", "2180.66", "2191.20", "2026-11-16T15:00:00+08:00"),
    "IDX:000300": ("沪深300", "3990.12", "3972.40", "2026-11-16T15:00:00+08:00"),
}


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


def quotes(now, sha_price="60.80", sha_time="2026-11-16T15:00:00+08:00", outage=None):
    """The scripted quote file at ``now``: SHA:DEMO at ``sha_price``, last night's New York close, the indices."""
    script = {
        "now": now,
        "quotes": {
            "SHA:DEMO": {"name": "示例宽基", "price": sha_price, "prev_close": "60.20", "quote_time": sha_time},
            "NASDAQ:DEMO": {"name": "示例研究标的", "price": "119.60", "prev_close": "118.90",
                            "quote_time": "2026-11-17T05:00:00+08:00"},
            **{ticker: {"name": name, "price": price, "prev_close": prev, "quote_time": at}
               for ticker, (name, price, prev, at) in INDICES.items()},
        },
        "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90", "age_seconds": 5}},
    }
    if outage:
        script["outage"] = outage
    return script


def write_quotes(directory, script):
    (directory / "fake_quotes.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def full_screenshot(page, path):
    """The page scrolls inside <main>: grow the viewport to the content for a whole-page picture."""
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({"width": size["width"], "height": size["height"] + max(0, extra)})
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


def activated_plan(env, moment, body):
    """Create and activate a plan as of ``moment`` on the scripted clock; returns its id.

    A plan is in force from the day it was activated (core.plans), and the API stamps the wall clock.
    The buy below is booked on the scripted day, so a plan activated "now" would postdate it as soon
    as the real date is later than that day.
    """
    script = ("import json, sys\nfrom datetime import datetime\nfrom core import desk_store, plans\n"
              "with desk_store.pinned_now(datetime.fromisoformat(sys.argv[1])):\n"
              "    print(plans.activate(plans.create(json.loads(sys.argv[2]))['id'])['id'])\n")
    done = subprocess.run([sys.executable, "-c", script, moment, json.dumps(body, ensure_ascii=False)],
                          env=env, check=True, capture_output=True, text=True)
    return int(done.stdout.split()[-1])


def rows(directory, sql):
    with sqlite3.connect(f"file:{directory / 'desk.sqlite3'}?mode=ro", uri=True) as db:
        return db.execute(sql).fetchall()


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="brief-browser-")))
    portfolio = out / "portfolio"
    subprocess.run([sys.executable, str(ROOT / "engine/scripts/create_demo.py"), "--output", str(portfolio), "--ledger",
                    "--opening-only", "--today", DAY], check=True, stdout=subprocess.DEVNULL)
    write_quotes(portfolio, quotes(f"{DAY}T08:00:00+08:00"))
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, "PORTFOLIO_DIR": str(portfolio), "TRACKER_CONFIG_FILE": str(out / "config.json"),
                "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_QUOTE_SOURCE": "fake", "TRACKER_DEMO_MODE": "0",
                "TRACKER_LAB_ONLY": "0", "PYTHONPATH": str(ROOT / "dashboard"),
                "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    for name in ("TRACKER_PREMARKET", "TRACKER_PREMARKET_TIME", "TRACKER_POSTMARKET", "TRACKER_POSTMARKET_TIME",
                 "TRACKER_BRIEF_COMMENTARY"):
        base_env.pop(name, None)
    log = stack.enter_context((out / "preview.log").open("w"))
    capture = Path(os.environ["TRACKER_SCREENSHOT_DIR"]) if os.environ.get("TRACKER_SCREENSHOT_DIR") else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def backend(**overrides):
        """A backend on the fixed port (Vite proxies to it); ``app`` picks the application module."""
        app = overrides.pop("app", "browser_brief_app:app")
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
        page.on("console", lambda message: errors.append("console: " + message.text) if message.type == "error" else None)
        pre = page.get_by_role("region", name="盘前简报", exact=True)
        post = page.get_by_role("region", name="盘后简报", exact=True)

        plan_id = activated_plan(base_env, f"{DAY}T08:00:00+08:00", {
            "ticker": "SHA:DEMO", "thesis": "示例理由：估值回到区间", "invalidation": "示例条件：放量跌破 54",
            "entry_low": "58", "entry_high": "61", "stop_price": "54", "target_price": "66"})

        # -- 08:00: both briefs are still to come ------------------------------------------------
        page.goto(url + "/review")
        expect(pre.get_by_text("尚未生成 · 08:45 自动生成", exact=True)).to_be_visible(timeout=60000)
        expect(post.get_by_text("尚未生成 · 15:45 自动生成", exact=True)).to_be_visible()
        expect(page.get_by_text("每个交易日 08:45 盘前、15:45 盘后自动生成", exact=False)).to_be_visible()
        expect(page.get_by_role("group", name="选择交易日").get_by_text(f"{DAY} 周二")).to_be_visible()
        expect(pre.get_by_role("button", name="生成")).to_have_count(0)
        tabs = page.get_by_role("navigation", name="计划与复盘")
        expect(tabs.get_by_role("link", name="今日简报")).to_have_attribute("aria-current", "page")
        results["scheduled"] = "passed"

        # -- 09:00: 生成 makes the pre-market brief ----------------------------------------------
        write_quotes(portfolio, quotes(f"{DAY}T09:00:00+08:00"))
        page.reload()
        expect(pre.get_by_text("尚未生成", exact=True)).to_be_visible(timeout=20000)
        pre.get_by_role("button", name="生成", exact=True).click()
        expect(pre.get_by_text("09:00 生成", exact=True)).to_be_visible(timeout=30000)
        overnight = pre.get_by_role("region", name="隔夜与上一交易日行情")
        index = overnight.get_by_role("listitem").filter(has_text="上证指数").first
        expect(index).to_contain_text("+0.52%")
        expect(index).to_contain_text("模拟行情 11-16 15:00")
        expect(overnight.get_by_role("listitem").filter(has_text="港股指数").first).to_contain_text("不可用")
        levels = pre.get_by_role("region", name="止损与目标距离")
        # The demo has two accounts: the row names the holding's account ("示例宽基（长期账户） 止损 54.00").
        stop_row = levels.get_by_role("listitem").filter(has_text=re.compile(r"示例宽基.* 止损 54")).first
        expect(stop_row).to_contain_text("11.18%")   # (60.80 - 54) / 60.80
        expect(stop_row).to_contain_text(f"距止损还有 11.18%（计划 #{plan_id}")
        news = pre.get_by_role("region", name="公告与新闻")
        expect(news.get_by_text("数据源不可用", exact=True).first).to_be_visible()
        expect(news).to_contain_text("模拟行情模式（TRACKER_QUOTE_SOURCE=fake）不联网查询公告与新闻")
        expect(news).to_contain_text("未查询")
        expect(pre.get_by_role("button", name="重新生成")).to_have_count(0)
        if capture:
            full_screenshot(page, capture / "brief-premarket.png")
        results["premarket"] = "passed"

        # -- a buy above the plan's entry range ------------------------------------------------
        proposal = page.request.post(f"{api}/ledger/proposals", data={"events": [
            {"kind": "buy", "date": DAY, "account": "长期账户", "ticker": "SHA:DEMO", "currency": "CNY",
             "quantity": "100", "price": "62.00", "fee": "5"}]}).json()
        assert page.request.post(f"{api}/ledger/proposals/{proposal['id']}/confirm").ok

        # -- 15:50 during an outage: the run fails, says why, and offers 重新生成 -----------------
        write_quotes(portfolio, quotes(f"{DAY}T15:50:00+08:00", outage="数据源维护"))
        page.reload()
        expect(post.get_by_text("尚未生成", exact=True)).to_be_visible(timeout=20000)
        post.get_by_role("button", name="生成", exact=True).click()
        expect(post.get_by_text("生成失败", exact=True)).to_be_visible(timeout=30000)
        expect(post).to_contain_text("行情全部不可用")
        expect(post).to_contain_text("自动重试")   # when, from the run row: the summary promises nothing
        expect(post.get_by_text("失败", exact=True)).to_be_visible()
        rerun = post.get_by_role("button", name="重新生成")
        expect(rerun).to_be_enabled()
        if capture:
            full_screenshot(page, capture / "brief-failed.png")
        results["failed"] = "passed"

        # -- the quotes are back: 重新生成 makes it; the broken plan shows in the plan check --------
        write_quotes(portfolio, quotes(f"{DAY}T15:50:00+08:00", sha_price="61.50", sha_time=f"{DAY}T15:00:00+08:00"))
        rerun.click()
        expect(post.get_by_text("15:50 生成", exact=True)).to_be_visible(timeout=30000)
        expect(post.get_by_text("生成失败", exact=True)).to_have_count(0)
        check = post.get_by_role("region", name="计划核对")
        group = check.get_by_role("listitem").filter(has_text="超出计划入场区间").first
        expect(group).to_contain_text("买入价 62 高于计划入场区间 58–61")
        link = group.get_by_role("link", name=f"查看计划 #{plan_id}")
        expect(link).to_have_attribute("href", f"/plans?plan={plan_id}")
        trades = post.get_by_role("region", name="今日成交")
        expect(trades).to_contain_text("100 股 × 62.00 = ¥6,200.00，费用 ¥5.00")
        pnl = post.get_by_role("region", name="盈亏归因")
        expect(pnl.get_by_role("listitem").filter(has_text="今日盈亏合计").first).to_be_visible()
        if capture:
            full_screenshot(page, capture / "brief-desktop.png")
        link.click()
        expect(page).to_have_url(f"{url}/plans?plan={plan_id}")
        results["postmarket"] = "passed"

        # -- the day before: nothing was generated, and it says so; the keyboard stays on the control --
        page.goto(url + "/review")
        expect(pre.get_by_text("09:00 生成", exact=True)).to_be_visible(timeout=20000)
        page.get_by_role("button", name="上一个交易日 2026-11-16").focus()
        page.keyboard.press("Enter")
        expect(page).to_have_url(f"{url}/review?date=2026-11-16")
        expect(pre.get_by_text("当日没有生成（后端当时可能未运行）。")).to_be_visible()
        focused = page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')")
        assert focused and focused.startswith("上一个交易日"), focused
        page.get_by_role("button", name="回到最近交易日").focus()
        page.keyboard.press("Enter")
        # The chosen date stays in the URL: going back to the latest day does not drop it.
        expect(page).to_have_url(f"{url}/review?date={DAY}")
        expect(pre.get_by_text("09:00 生成", exact=True)).to_be_visible()
        expect(page.locator("#brief-date")).to_be_focused()
        results["dates"] = "passed"

        # -- a laptop keeps two columns; a phone stacks them without sideways scrolling --------
        page.set_viewport_size({"width": 1100, "height": 800})
        page.goto(url + "/review")
        expect(post.get_by_text("15:50 生成", exact=True)).to_be_visible(timeout=20000)
        left, right = pre.bounding_box(), post.bounding_box()
        assert abs(left["y"] - right["y"]) < 2 and right["x"] > left["x"] + left["width"], (left, right)
        if capture:
            full_screenshot(page, capture / "brief-laptop.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(url + "/review")
        expect(post.get_by_text("15:50 生成", exact=True)).to_be_visible(timeout=20000)
        page.wait_for_timeout(300)
        left, right = pre.bounding_box(), post.bounding_box()
        assert right["y"] > left["y"] + left["height"] - 1, (left, right)
        widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
        assert max(widths) <= 390, widths
        main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
        assert main[0] <= main[1], main
        if capture:
            full_screenshot(page, capture / "brief-mobile.png")
        results["mobile_width"] = 390
        page.set_viewport_size({"width": 1440, "height": 900})
        stop(server)

        # -- demo mode: the seeded briefs, read-only; start-up and reading write nothing --------
        demo = out / "demo"
        subprocess.run([sys.executable, str(ROOT / "engine/scripts/create_demo.py"), "--output", str(demo), "--ledger",
                        "--today", DAY], check=True, stdout=subprocess.DEVNULL)
        stored = rows(demo, "SELECT kind, trading_date, generated_at FROM briefs ORDER BY kind")
        assert [kind for kind, _, _ in stored] == ["postmarket", "premarket"], stored
        server = backend(app="backend.main:app", TRACKER_DEMO_MODE="1", PORTFOLIO_DIR=str(demo))
        page.goto(url + "/review")
        expect(page.get_by_text("演示数据，只读")).to_be_visible(timeout=30000)
        # It opens on the newest brief when the current trading day has none.
        expect(page).to_have_url(re.compile(rf"/review(\?date={stored[0][1]})?$"))
        expect(pre.get_by_text("演示数据 · 08:45 生成", exact=True)).to_be_visible()
        expect(post.get_by_text("演示数据 · 15:45 生成", exact=True)).to_be_visible()
        expect(post.get_by_role("region", name="盈亏归因")).to_be_visible()
        expect(page.get_by_role("button", name="生成", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="重新生成")).to_have_count(0)
        refused = page.request.post(f"{api}/routines/premarket/run")
        assert refused.status == 403, refused.status
        if capture:
            full_screenshot(page, capture / "brief-demo.png")
        assert rows(demo, "SELECT kind, trading_date, generated_at FROM briefs ORDER BY kind") == stored
        if rows(demo, "SELECT COUNT(*) FROM sqlite_master WHERE name = 'routine_runs'") != [(0,)]:
            assert rows(demo, "SELECT COUNT(*) FROM routine_runs") == [(0,)]
        results["demo"] = "passed"
        stop(server)

        assert not errors, errors
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors},
                     ensure_ascii=False))
