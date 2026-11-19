"""研究 › 回测 (/backtest) in a real browser, offline.

Bars come from a deterministic generator (``browser_backtest_app``: no network; the 恒生指数 has no
bars, so a Hong Kong run cannot be compared with it), and the AI reading is the rule-based one,
written after a pause. Journeys:

* full mode: a link prefills a US preset run; parameters changed; the result is complete (headline,
  every caveat, 一直持有 and 标普500, the net value and drawdown charts, the price with one marker per
  fill (as many sell markers as sell fills) and a readout on hover and keys, the monthly returns,
  figures, trades, assumptions) and in dollars, never ¥; the
  AI reading is pending, then shown; the CSV export downloads; a run in progress shows its stages and
  survives leaving the page; a run whose comparisons are unavailable says 不可用 with the reasons;
  refusals are shown in Chinese next to their field (the form's own and the service's 422), never as a
  toast; 历史: open, rerun (same data shown), delete; 数据 lists the cache and clears one; 策略文件 runs a
  built-in strategy through the same form; 1100 px and 390 px without sideways scrolling;
* demo mode: from a holding on 今日 to a result in two clicks, on fictional data said to be fictional;
  history and cache are not kept and the page says so;
* research-only mode: the page works and calls no desk endpoint.
Set TRACKER_SCREENSHOT_DIR to keep screenshots.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

from runner_date import create_demo_argv

ROOT = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = "light" if os.environ.get("TRACKER_SCREENSHOT_THEME") == "light" else "dark"
DESK_ENDPOINTS = ("/api/instruments", "/api/today", "/api/ledger", "/api/plans", "/api/watchlist", "/api/alerts")


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


DAY = "2026-11-17"   # the day the scripted clock shows (quotes()["now"]); the demo is made for it


def quotes():
    fresh = "2026-11-17T10:32:02+08:00"
    return {
        "now": "2026-11-17T10:32:05+08:00",
        "quotes": {
            "SHA:DEMO": {"name": "示例宽基", "price": "61.20", "prev_close": "60.80", "quote_time": fresh},
            "NASDAQ:DEMO": {"name": "示例研究标的", "price": "119.60", "prev_close": "118.90",
                            "quote_time": "2026-11-17T05:00:00+08:00"},
        },
        "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90", "age_seconds": 5}},
    }


def full_screenshot(page, path):
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({"width": size["width"], "height": size["height"] + max(0, extra)})
    page.wait_for_timeout(200)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


def no_sideways_scroll(page, width):
    page.wait_for_timeout(300)
    widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
    assert max(widths) <= width, widths
    main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
    assert main[0] <= main[1], main


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="backtest-browser-")))
    portfolio = out / "portfolio"
    subprocess.run(create_demo_argv("--output", str(portfolio), "--ledger", "--opening-only", "--today", DAY),
                   check=True, stdout=subprocess.DEVNULL)
    (portfolio / "fake_quotes.json").write_text(json.dumps(quotes(), ensure_ascii=False), encoding="utf-8")
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, "PORTFOLIO_DIR": str(portfolio), "TRACKER_CONFIG_FILE": str(out / "config.json"),
                "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_QUOTE_SOURCE": "fake", "TRACKER_DEMO_MODE": "0",
                "TRACKER_LAB_ONLY": "0", "TRACKER_BACKTEST_DIR": str(out / "backtest"),
                "TRACKER_ALLOW_CUSTOM_STRATEGIES": "0", "BACKTEST_FIXTURE_REFLECT_SECONDS": "3",
                "BACKTEST_FIXTURE_SLOW_SECONDS": "4", "PYTHONPATH": str(ROOT / "dashboard"),
                "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    log = stack.enter_context((out / "preview.log").open("w"))
    capture = Path(os.environ["TRACKER_SCREENSHOT_DIR"]) if os.environ.get("TRACKER_SCREENSHOT_DIR") else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def shot(page, name, full=False):
        if capture:
            page.wait_for_timeout(250)
            (full_screenshot(page, capture / name) if full else page.screenshot(path=str(capture / name)))

    def backend(**overrides):
        env = {**base_env, **overrides}
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_backtest_app:app", "--app-dir", str(ROOT / "tests"),
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
        context = browser.new_context(color_scheme=SCHEME, viewport={"width": 1440, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai",
                                      accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        posted = []
        page.on("request", lambda request: posted.append((request.url, request.post_data))
                if request.method == "POST" and request.url.endswith("/api/backtest/runs") else None)
        toasts = page.get_by_role("region", name="通知")
        result = page.get_by_role("article", name="回测结果")
        form = page.get_by_role("form", name="快速回测")
        run_button = form.get_by_role("button", name="开始回测")

        def comparison(key):
            return result.locator(f"[data-comparison='{key}']")

        def wait_result(timeout=60000):
            expect(result).to_be_visible(timeout=timeout)
            expect(result.get_by_role("region", name="可信度提醒")).to_be_visible()

        try:
            # -- a link prefills a preset run; parameters changed; the result is complete, in dollars ------
            page.goto(url + "/backtest?ticker=NASDAQ:AAPL&preset=rsi_rebound&from=watch")
            expect(page.get_by_role("heading", name="回测", exact=True)).to_be_visible(timeout=60000)
            expect(form.get_by_role("radio", name=re.compile("超跌反弹"))).to_be_checked(timeout=20000)
            expect(form.get_by_text("来自行情")).to_be_visible()
            expect(form.get_by_text(re.compile("AAPL · 美股 · USD"))).to_be_visible(timeout=20000)   # the canonical US:AAPL
            params = form.get_by_role("group", name="超跌反弹的参数")
            params.get_by_label("RSI 低于多少时买入").fill("35")
            params.get_by_label("最长持有").fill("30")
            shot(page, "backtest-form.png")
            run_button.click()
            wait_result()
            body = json.loads(posted[-1][1])
            assert body == {"ticker": "NASDAQ:AAPL", "preset": "rsi_rebound", "params": {"buy_below": 35, "max_hold_days": 30}}, body
            assert "run=" in page.url
            headline = result.locator("header p").last
            expect(headline).to_contain_text("这个方法收益")
            expect(headline).to_contain_text("一直持有")
            caveats = result.get_by_role("region", name="可信度提醒").locator("li")
            assert caveats.count() >= 2, caveats.count()
            expect(caveats.filter(has_text="参数是在这同一段历史上看的结果")).to_have_count(1)
            expect(comparison("same_stock")).to_contain_text("%")
            expect(comparison("index")).to_contain_text("标普500")
            expect(comparison("index")).to_contain_text("%")
            expect(result.get_by_role("img", name=re.compile("净值走势"))).to_be_visible()
            figures = result.get_by_role("region", name="关键数字")
            expect(figures).to_contain_text("期末资产")
            expect(figures).to_contain_text("$")
            text = result.inner_text()
            assert "¥" not in text, "a USD run shows no ¥"
            expect(result.get_by_role("region", name="假设与数据")).to_contain_text("数据指纹")
            expect(result.get_by_role("region", name="假设与数据")).to_contain_text("次日开盘成交")
            expect(result.get_by_role("table", name="逐笔成交")).to_be_visible()
            results["preset_run"] = "passed"

            # -- the charts: equity, its drawdown, the price with a marker per fill, the monthly returns -----
            run_id = re.search(r"run=([^&]+)", page.url).group(1)
            served = page.request.get(f"{api}/backtest/runs/{run_id}").json()["result"]
            assert served["prices"] and served["monthly_returns"], "the service sends the price rows and the months"
            expect(result.locator("[data-chart='equity']")).to_be_visible()
            expect(result.locator("[data-chart='drawdown']").get_by_role("img", name=re.compile("^回撤：.*最深 -"))).to_be_visible()
            price = result.locator("[data-chart='price']")
            chart = price.get_by_role("img", name=re.compile("^价格走势：US:AAPL .*前复权"))
            expect(chart).to_be_visible()
            sells = sum(1 for fill in served["fills"] if fill["direction"] == "sell")
            buys = sum(1 for fill in served["fills"] if fill["direction"] == "buy")
            assert sells > 0 and buys > 0, (sells, buys)
            expect(price.locator("[data-marker='sell'], [data-marker='end_mark']")).to_have_count(sells)   # one per sell fill
            expect(price.locator("[data-marker='buy']")).to_have_count(buys)
            expect(price.locator("[data-marker='unfilled']")).to_have_count(len(served["unfilled"]))
            reading = price.locator("[data-marker-reading]")
            chart.focus()
            page.keyboard.press("ArrowRight")                                   # the first marker: a buy
            expect(reading).to_contain_text("买入")
            expect(reading).to_contain_text("成交价")
            expect(reading).to_contain_text("股")
            price.locator("[data-marker='sell']").first.hover(force=True)      # an unfilled order may share its day
            expect(reading).to_contain_text("卖出")
            expect(reading).to_contain_text("原因")
            shot(page, "backtest-price-marker.png")
            page.mouse.move(0, 0)
            expect(reading).to_have_count(0)
            monthly = result.get_by_role("table", name="月度收益")
            expect(monthly).to_be_visible()
            years = sorted({entry["year"] for entry in served["monthly_returns"]})
            expect(monthly.locator("tbody tr")).to_have_count(12)                 # a row per month, a column per year
            for year in years:
                expect(monthly.get_by_role("columnheader", name=f"{year}年")).to_be_visible()
            widths = monthly.evaluate("t => [t.parentElement.scrollWidth, t.parentElement.clientWidth]")
            assert widths[0] <= widths[1], widths                                 # every year in view, no inner scroll
            expect(monthly).to_contain_text("%")
            expect(monthly).to_contain_text("—")                                # months outside the run say so
            results["charts"] = {"sell_markers": sells, "buy_markers": buys, "months": len(served["monthly_returns"])}

            # -- AI 解读: asked for, pending, then shown as the model's reading ---------------------------
            reading = result.get_by_role("region", name="AI 解读")
            expect(reading.get_by_text("模型的看法，不是事实")).to_be_visible()
            reading.get_by_role("button", name="生成 AI 解读").click()
            expect(reading.get_by_text("解读生成中", exact=False)).to_be_visible(timeout=5000)
            shot(page, "backtest-reflection-pending.png")
            expect(reading.get_by_text("按规则生成（没有用模型）")).to_be_visible(timeout=20000)
            expect(reading.get_by_text("解读生成中", exact=False)).to_have_count(0)
            shot(page, "backtest-us-result.png", full=True)
            results["reflection"] = "passed"

            # -- export: the CSV with the run manifest ------------------------------------------------------
            with page.expect_download() as download:
                result.get_by_role("link", name="导出 CSV").click()
            saved = download.value
            assert saved.suggested_filename.endswith(".csv"), saved.suggested_filename
            content = Path(saved.path()).read_text(encoding="utf-8-sig")
            assert content.startswith("[manifest]"), content[:80]
            assert "US:AAPL" in content
            results["export"] = "passed"

            # -- the reading is the server's: reopened from 历史 in the same session, it is shown --------------
            page.get_by_role("tab", name="历史").click()
            page.get_by_role("table", name="回测历史").locator("tbody tr").filter(has_text="US:AAPL").first \
                .get_by_role("link", name="US:AAPL", exact=True).click()
            wait_result()
            reopened = result.get_by_role("region", name="AI 解读")
            expect(reopened.get_by_text("按规则生成（没有用模型）")).to_be_visible(timeout=10000)
            expect(reopened.get_by_role("button", name="生成 AI 解读")).to_have_count(0)
            results["reading_reopened"] = "passed"

            # -- a run in progress shows its stages, and can be left and opened again ---------------------
            page.goto(url + "/backtest?ticker=NASDAQ:MSFT&preset=ma_trend")
            expect(form.get_by_role("radio", name=re.compile("均线趋势"))).to_be_checked(timeout=20000)
            run_button.click()
            progress = page.get_by_role("status", name="回测进度")
            expect(progress).to_be_visible(timeout=10000)
            expect(progress).to_contain_text("准备数据")
            expect(progress.get_by_role("button", name="取消")).to_be_visible()
            shot(page, "backtest-progress.png")
            running = page.url
            page.goto(url + "/ledger")
            expect(page.get_by_role("heading", name="回测", exact=True)).to_have_count(0)
            page.goto(running)
            wait_result()
            expect(result).to_contain_text("US:MSFT")
            results["progress"] = "passed"

            # -- comparisons unavailable: 不可用 with the reasons, never 0 --------------------------------
            page.goto(url + "/backtest?ticker=HKG:00700&preset=ma_trend&capital=100")
            expect(form.get_by_role("radio", name=re.compile("均线趋势"))).to_be_checked(timeout=20000)
            run_button.click()
            wait_result()
            same = comparison("same_stock")
            expect(same).to_contain_text("不可用")
            expect(same).to_contain_text("买不进")
            assert "%" not in same.inner_text(), same.inner_text()
            index = comparison("index")
            expect(index).to_contain_text("不可用")
            expect(index).to_contain_text("恒生指数")
            assert "%" not in index.inner_text(), index.inner_text()
            unfilled = result.get_by_role("table", name="没有成交的委托")
            expect(unfilled).to_contain_text("一手超过可用资金")
            assert "¥" not in result.inner_text(), "a HKD run shows no ¥"
            expect(result.get_by_role("region", name="关键数字")).to_contain_text("HK$")
            shot(page, "backtest-unavailable.png", full=True)
            results["unavailable_comparison"] = "passed"

            # -- refusals next to their field, in Chinese: the form's own, then the service's 422 ----------
            page.goto(url + "/backtest?ticker=SHA:600519&preset=ma_trend")
            expect(form.get_by_role("radio", name=re.compile("均线趋势"))).to_be_checked(timeout=20000)
            form.get_by_role("button", name="修改（高级设置）").click()
            form.get_by_label("本金", exact=True).fill("0")
            before = len(posted)
            run_button.click()
            expect(form.get_by_text("本金必须大于 0")).to_be_visible()
            expect(form.get_by_label("本金", exact=True)).to_have_attribute("aria-invalid", "true")
            assert len(posted) == before, "nothing is sent while the form refuses"
            form.get_by_label("本金", exact=True).fill("")
            period = form.get_by_role("group", name="均线趋势的参数").get_by_label("均线天数")
            period.fill("20.5")                                  # within 2–250: the service refuses a fraction
            run_button.click()
            expect(form.get_by_text("「均线天数」应为整数", exact=False)).to_be_visible(timeout=10000)
            expect(period).to_have_attribute("aria-invalid", "true")
            assert len(posted) == before + 1
            toast_text = toasts.inner_text() if toasts.count() else ""
            assert "detail" not in toast_text and "{" not in toast_text, toast_text
            expect(page.get_by_text('"detail"')).to_have_count(0)
            shot(page, "backtest-422.png")
            period.fill("")
            results["validation"] = "passed"

            # -- 历史: open, rerun with the same data, delete -------------------------------------------
            page.get_by_role("tab", name="历史").click()
            history = page.get_by_role("table", name="回测历史")
            us_row = history.locator("tbody tr").filter(has_text="US:AAPL").first
            expect(us_row).to_be_visible(timeout=20000)
            expect(history).to_contain_text("超跌反弹")
            expect(history).to_contain_text("这个方法收益")
            assert history.locator("tbody tr").filter(has_text="HKG:00700").count() >= 1
            shot(page, "backtest-history.png")
            us_row.get_by_role("link", name="US:AAPL", exact=True).click()
            wait_result()
            expect(result.get_by_role("heading", level=2)).to_contain_text("US:AAPL")
            expect(result.get_by_role("heading", level=2)).to_contain_text("超跌反弹")
            expect(comparison("index")).to_contain_text("标普500")
            # the reading generated earlier is the server's: reopened, it is shown, not offered again
            opened_reading = result.get_by_role("region", name="AI 解读")
            expect(opened_reading.get_by_text("按规则生成（没有用模型）")).to_be_visible(timeout=10000)
            expect(opened_reading.get_by_role("button", name="生成 AI 解读")).to_have_count(0)
            # the form now shows the opened run (it last held the refused A-share run), ready to change and run again
            expect(form.get_by_role("radio", name=re.compile("超跌反弹"))).to_be_checked()
            expect(form.get_by_role("group", name="超跌反弹的参数").get_by_label("RSI 低于多少时买入")).to_have_value("35")
            page.get_by_role("tab", name="历史").click()
            us_row = history.locator("tbody tr").filter(has_text="US:AAPL").first
            us_row.hover()
            us_row.get_by_role("button", name=re.compile("^重跑")).click()
            wait_result()
            check = result.get_by_role("region", name="重跑核对")
            expect(check).to_be_visible()
            expect(check.locator("[data-check='same-data']")).to_have_text("相同")
            expect(check.locator("[data-check='same-strategy']")).to_contain_text(re.compile("相同|不同|无法核对"))
            expect(check).to_contain_text("结果数字")
            shot(page, "backtest-rerun.png")
            page.get_by_role("tab", name="历史").click()
            hk_rows = history.locator("tbody tr").filter(has_text="HKG:00700")
            expect(hk_rows.first).to_be_visible(timeout=20000)
            count = hk_rows.count()
            hk_rows.first.hover()
            hk_rows.first.get_by_role("button", name=re.compile("^删除")).click()
            confirm = page.get_by_role("dialog", name="删除这条回测记录？")
            expect(confirm).to_be_visible()
            expect(confirm.get_by_role("button", name="取消")).to_be_focused()
            confirm.get_by_role("button", name="删除").click()
            expect(toasts.get_by_text("已删除这条回测记录")).to_be_visible()
            expect(hk_rows).to_have_count(count - 1)
            results["history"] = "passed"

            # -- 数据: the cache per instrument, and clearing one ----------------------------------------
            page.get_by_role("tab", name="数据").click()
            cache = page.get_by_role("table", name="已缓存的行情")
            expect(cache.locator("tbody tr").filter(has_text="US:AAPL")).to_have_count(1, timeout=20000)
            aapl = cache.locator("tbody tr").filter(has_text="US:AAPL").first
            expect(aapl).to_contain_text("yfinance")                               # the source /data/cache sends
            expect(aapl).to_contain_text("完整")
            expect(page.get_by_text("来源、下载时间和是否完整：不可用", exact=False)).to_have_count(0)
            expect(cache).to_contain_text("前复权 ")                               # what each adjustment holds
            # 更新到今天 refreshes every adjustment and says per adjustment what the cache now holds
            # (the download itself needs the network: its answer is scripted here, as the service sends it).
            sent = []
            def scripted_download(route):
                sent.append(json.loads(route.request.post_data))
                route.fulfill(json={"symbol": "US:AAPL", "rows_saved": 6, "start_date": "2023-08-07", "end_date": "2026-11-19", "adjustments": [
                    {"adjust": "qfq", "label": "前复权", "ok": True, "rows_saved": 6, "rows": 858, "first": "2023-08-07", "last": "2026-11-18",
                     "last_session": "2026-11-18", "up_to_date": True, "error": None},
                    {"adjust": "", "label": "不复权", "ok": False, "rows_saved": None, "rows": 853, "first": "2023-08-07", "last": "2026-11-13",
                     "last_session": "2026-11-18", "up_to_date": False, "error": "数据下载失败：数据源暂时不可用"}]})
            page.route("**/api/backtest/data/download", scripted_download)
            aapl.hover()
            aapl.get_by_role("button", name="更新 US:AAPL 到今天").click()
            report = page.get_by_role("region", name="更新结果")
            expect(report).to_contain_text("前复权 到 2026-11-18（最新）")
            expect(report).to_contain_text("不复权 没有更新：数据下载失败：数据源暂时不可用；缓存仍到 2026-11-13")
            assert sent and sent[-1].get("all_adjustments") is True, sent
            page.unroute("**/api/backtest/data/download")
            shot(page, "backtest-data.png", full=True)
            msft = cache.locator("tbody tr").filter(has_text="US:MSFT").first
            msft.hover()
            msft.get_by_role("button", name="删除 US:MSFT 的缓存").click()
            page.get_by_role("dialog", name="删除 US:MSFT 的缓存？").get_by_role("button", name="删除缓存").click()
            expect(toasts.get_by_text("已删除 US:MSFT", exact=False)).to_be_visible()
            expect(cache.locator("tbody tr").filter(has_text="US:MSFT")).to_have_count(0, timeout=10000)
            results["data"] = "passed"

            # -- 策略文件: a built-in strategy runs through the same form ---------------------------------
            page.get_by_role("tab", name="策略文件").click()
            builtin = page.get_by_role("region", name="内置示例策略")
            expect(builtin.get_by_role("button", name=re.compile("^用 .+ 回测$")).first).to_be_visible(timeout=20000)
            expect(page.get_by_text("没有自定义策略文件")).to_be_visible()
            shot(page, "backtest-strategies.png", full=True)
            builtin.get_by_role("button", name=re.compile("^用 .+ 回测$")).first.click()
            expect(form.get_by_text(re.compile("^策略文件："))).to_be_visible()
            run_button.click()
            wait_result()
            assert '"strategy"' in posted[-1][1], posted[-1][1]
            results["strategy_file"] = "passed"

            # -- what is still held at the end is a valuation, never a completed trade ---------------------
            page.goto(url + "/backtest?ticker=NASDAQ:AAPL&preset=buy_and_hold")
            expect(form.get_by_role("radio", name=re.compile("一直持有"))).to_be_checked(timeout=20000)
            run_button.click()
            wait_result()
            served = page.request.get(f"{api}/backtest/runs/{re.search(r'run=([^&]+)', page.url).group(1)}").json()["result"]
            assert served["facts"]["closed_trades"] == 0, served["facts"]["closed_trades"]
            held = [trip for trip in served["round_trips"] if trip.get("end_mark") or trip.get("exit_reason") == "end_mark"]
            assert len(held) == 1, served["round_trips"]
            trips = result.get_by_role("region", name="完整交易")
            expect(trips).to_contain_text("0 条")
            expect(trips).to_contain_text("没有完整交易")
            expect(trips).not_to_contain_text("期末估值")
            still = result.get_by_role("region", name="期末仍持有")
            expect(still).to_contain_text("1 条")
            expect(still).to_contain_text("期末估值（未卖出）")
            expect(still).to_contain_text("最后收盘价")
            expect(still).not_to_contain_text("卖出均价")
            mark_fill = result.get_by_role("table", name="逐笔成交").locator("tbody tr").filter(has_text="期末估值")
            expect(mark_fill).to_have_count(1)
            expect(mark_fill).to_contain_text("按最后收盘价估值，不是成交价")              # its price is a valuation
            results["end_mark"] = "passed"

            # -- 历史 past one page: the API pages by offset, the page loads more ----------------------------
            def history_pages(route):
                query = dict(part.split("=", 1) for part in route.request.url.split("?", 1)[1].split("&"))
                offset, limit = int(query.get("offset", "0")), int(query.get("limit", "50"))
                rows = [{"run_id": f"page{n}", "ticker": f"US:P{n:03d}", "strategy_name": "均线趋势",
                         "start_date": "2023-11-18", "end_date": "2026-11-17", "first_date": "2023-11-20",
                         "last_date": "2026-11-17", "total_return": 0.01, "run_date": "2026-11-18T10:00:00",
                         "headline_zh": f"第 {n + 1} 条", "engine_version": "2.2.0"} for n in range(100)]
                # the oldest, saved by v3.2.1: its newer columns are null
                rows.append({"run_id": "legacy1", "symbol": "600519", "ticker": None, "strategy_name": "DualMaStrategy",
                             "start_date": "2024-01-02", "end_date": "2024-12-31", "total_return": 0.0523,
                             "run_date": "2025-03-01T09:00:00", "headline_zh": None, "engine_version": None,
                             "data_sha256": None, "first_date": None, "last_date": None})
                route.fulfill(json={"runs": rows[offset:offset + limit], "stats": {"total_runs": 101, "kept": True}})
            page.route("**/api/backtest/history?*", history_pages)
            page.goto(url + "/backtest?tab=history")
            listed = page.get_by_role("table", name="回测历史")
            expect(listed.locator("tbody tr").filter(has_text="US:P099")).to_have_count(1, timeout=20000)
            expect(listed.locator("tbody tr").filter(has_text="600519")).to_have_count(0)
            page.get_by_role("button", name=re.compile("^加载更多")).click()
            expect(listed.locator("tbody tr").filter(has_text="600519")).to_have_count(1)    # the 101st, at offset 100
            expect(page.get_by_role("button", name=re.compile("^加载更多"))).to_have_count(0)
            expect(page.get_by_text("共 101 条", exact=False)).to_be_visible()
            results["history_pages"] = "passed"

            # -- a run saved by v3.2.1: opens as a legacy result (the service's read-only translation) ------
            old_row = listed.locator("tbody tr").filter(has_text="600519").first
            old_row.hover()
            expect(old_row.get_by_role("button", name=re.compile("^重跑"))).to_have_attribute("aria-disabled", "true")
            legacy_result = {
                "run_id": "legacy1", "created_at": "2025-03-01T09:00:00",
                "legacy": {"saved_by": "v3.2.1 或更早",
                           "note_zh": "这是旧版本（v3.2.1 或更早）保存的回测，只保留了当时的汇总指标、抽样净值和成交明细；"
                                      "没有的部分：运行清单、对照收益、完整交易明细、未成交信号、期末估值标记、K 线、月度收益、每日净值、其余指标、成交的费用明细和原因。",
                           "unavailable": [{"key": "manifest", "label_zh": "运行清单（设置、数据指纹、引擎和费用表版本）：不能按原设置重跑，也不能核对当时用的行情"},
                                           {"key": "benchmark", "label_zh": "“一直持有”和指数的对比"},
                                           {"key": "round_trips", "label_zh": "每一笔完整交易（买入到卖出）的明细"},
                                           {"key": "unfilled", "label_zh": "没有成交的信号"},
                                           {"key": "prices", "label_zh": "回测用的 K 线"},
                                           {"key": "monthly_returns", "label_zh": "月度收益"},
                                           {"key": "fills_detail", "label_zh": "每笔成交的费用明细和成交原因"}],
                           "saved_metrics": {"sharpe_ratio": 0, "win_rate": 0}},
                "facts": {"ticker": "SHA:600519", "label": "600519", "market": "SHA", "market_label": "A股", "currency": "CNY", "kind": "stock",
                          "strategy": {"kind": None, "id": "DualMaStrategy", "name": "DualMaStrategy", "class": "DualMaStrategy", "params": {}},
                          "period": {"requested_start": "2024-01-02", "requested_end": "2024-12-31", "first_date": "2024-01-02",
                                     "last_date": "2024-12-31", "span_label": "1 年"},
                          "first_date": "2024-01-02", "last_date": "2024-12-31", "capital": 100000, "final_value": 105230,
                          "total_return": 0.0523, "annualized_return": 0.0531, "max_drawdown": -0.081,
                          "same_stock_return": None, "same_stock_reason": "旧版本（v3.2.1 或更早）保存的记录没有“一直持有”的对照。",
                          "index_return": None, "index_reason": "旧版本（v3.2.1 或更早）保存的记录没有指数对比。",
                          "closed_trades": None, "winning_trades": None, "fills": 2, "fees_total": None, "end_mark": None, "adjust": None,
                          "warmup_bars": None, "commission": {"buy": 0.0003, "sell": 0.0013, "min": None}, "slippage_pct": 0.001},
                "headline_zh": "旧版本（v3.2.1 或更早）保存的回测，2024-01-02 至 2024-12-31（1 年），这个方法收益 +5.2%，最大回撤 -8.1%，共 2 笔成交。",
                "caveats": [{"code": "legacy_result", "level": "warning", "text": "这是旧版本（v3.2.1 或更早）保存的回测。"}],
                "metrics": {"final_value": 105230, "total_return": 0.0523, "annualized_return": 0.0531, "max_drawdown": -0.081,
                            "sharpe_ratio": None, "win_rate": None,
                            "undefined": {"sharpe_ratio": "旧版本保存的记录：这个指标算不出时记为 0，和真实的 0 分不开，这里不显示",
                                          "win_rate": "旧版本保存的记录：这个指标算不出时记为 0，和真实的 0 分不开，这里不显示"}},
                "equity": [{"date": "2024-01-02", "total_value": 100000, "net_value": 1.0, "drawdown": 0.0},
                           {"date": "2024-06-28", "total_value": 96000, "net_value": 0.96, "drawdown": -0.04},
                           {"date": "2024-12-31", "total_value": 105230, "net_value": 1.0523, "drawdown": 0.0}],
                "benchmark": {"same_stock": None, "index": None}, "round_trips": [], "unfilled": [],
                "fills": [{"date": "2024-02-01", "symbol": "600519", "direction": "buy", "quantity": 100, "price": 1650.5, "commission": 49.5, "pnl": 0, "reason": None, "fees": None},
                          {"date": "2024-10-08", "symbol": "600519", "direction": "sell", "quantity": 100, "price": 1720.0, "commission": 51.6, "pnl": 6849.5, "reason": None, "fees": None}],
                "assumptions": {"line_zh": "按旧版本（v3.2.1 或更早）当时的费用和成交规则计算", "slippage_pct": 0.001},
                "rerun": None, "end_mark": None,
                "manifest": {"legacy": True, "engine_version": None, "spec": None, "coverage": [], "data_sha256": None},
            }
            def legacy_run(route):
                full = "include_result=false" not in route.request.url
                route.fulfill(json={"run_id": "legacy1", "kind": "saved", "status": "completed", "stage": None, "stage_label": None,
                                    "stage_detail": None, "progress": None, "stages": [], "error": None,
                                    "reflection": {"status": "none", "result": None, "message": None},
                                    "result": legacy_result if full else None})
            page.route("**/api/backtest/runs/legacy1*", legacy_run)
            old_row.get_by_role("link", name="600519", exact=True).click()
            wait_result()
            expect(result.locator("header p").last).to_contain_text("旧版本（v3.2.1 或更早）保存的回测")
            parts = result.get_by_role("region", name="旧版本记录")
            expect(parts).to_contain_text("每一笔完整交易（买入到卖出）的明细")
            expect(result.get_by_role("region", name="完整交易")).to_contain_text("不可用：旧版本没有保存")
            expect(result.get_by_role("region", name="没有成交的委托")).to_contain_text("不可用：旧版本没有保存")
            expect(result.get_by_text("每个信号都成交了")).to_have_count(0)
            expect(result.get_by_role("region", name="假设与数据")).to_contain_text("复权不可用")
            expect(comparison("same_stock")).to_contain_text("不可用")
            shot(page, "backtest-legacy.png", full=True)
            page.unroute("**/api/backtest/runs/legacy1*")
            page.unroute("**/api/backtest/history?*")
            results["legacy_run"] = "passed"

            # -- widths ------------------------------------------------------------------------------------
            page.goto(url + "/backtest?tab=history")
            page.get_by_role("table", name="回测历史").locator("tbody tr").filter(has_text="US:AAPL").first.get_by_role("link", name="US:AAPL", exact=True).click()
            wait_result()
            page.set_viewport_size({"width": 1100, "height": 900})
            no_sideways_scroll(page, 1100)
            page.set_viewport_size({"width": 390, "height": 844})
            wait_result()
            no_sideways_scroll(page, 390)
            shot(page, "backtest-mobile.png", full=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            results["widths"] = [1100, 390]
            page.goto("about:blank")
            stop(server)

            # -- demo: from a holding to a result in two clicks, on fictional data said to be fictional ----
            server = backend(TRACKER_DEMO_MODE="1")
            page.goto(url + "/")
            holdings = page.get_by_role("table", name="持仓")
            expect(holdings.get_by_text("示例宽基", exact=True)).to_be_visible(timeout=60000)
            row = holdings.locator("tbody tr").filter(has_text="SHA:DEMO").first
            row.hover()
            row.get_by_role("link", name="回测这只持仓：示例宽基").click()               # click 1
            expect(page).to_have_url(re.compile(r"/backtest\?ticker=SHA%3ADEMO&preset=ma_trend&from=holding"))
            expect(form.get_by_role("radio", name=re.compile("均线趋势"))).to_be_checked(timeout=20000)
            expect(form.get_by_text("来自持仓")).to_be_visible()
            expect(page.get_by_text("回测用程序生成的虚构行情", exact=False)).to_be_visible()
            shot(page, "backtest-demo-form.png")
            run_button.click()                                                              # click 2
            wait_result()
            body = json.loads(posted[-1][1])
            assert body["ticker"] == "SHA:DEMO" and body["preset"] == "ma_trend", body
            expect(result.get_by_text("演示数据，不是真实行情")).to_be_visible()
            expect(result.get_by_role("region", name="可信度提醒")).to_contain_text("演示数据")
            expect(result.get_by_text("没有保存到历史")).to_be_visible()
            expect(comparison("index")).to_contain_text("不可用")
            expect(comparison("index")).to_contain_text("演示模式没有指数行情")
            expect(comparison("same_stock")).to_contain_text("%")
            expect(result.locator("[data-chart='price'] [data-marker='buy']").first).to_be_attached()
            expect(result.get_by_role("table", name="月度收益")).to_be_visible()
            shot(page, "backtest-demo-result.png", full=True)
            page.set_viewport_size({"width": 390, "height": 844})
            wait_result()
            no_sideways_scroll(page, 390)
            shot(page, "backtest-demo-mobile.png", full=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.get_by_role("tab", name="历史").click()
            expect(page.get_by_text("演示模式不保存回测")).to_be_visible(timeout=20000)
            page.get_by_role("tab", name="数据").click()
            expect(page.get_by_text("演示模式不保存行情")).to_be_visible(timeout=20000)
            results["demo_two_clicks"] = "passed"
            page.goto("about:blank")     # the demo page polls 待确认 (/api/ledger); it must not run into the next phase
            stop(server)

            # -- research-only mode: the page works and calls no desk endpoint ---------------------------
            server = backend(TRACKER_LAB_ONLY="1")
            desk_calls = []
            page.on("request", lambda request: desk_calls.append(request.url)
                    if any(path in request.url for path in DESK_ENDPOINTS) else None)
            page.goto(url + "/backtest")
            expect(page.get_by_role("heading", name="回测", exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_text("研究模式不读取持仓和自选", exact=False)).to_be_visible(timeout=20000)
            expect(form.get_by_role("combobox", name="标的")).to_have_count(0)
            form.get_by_role("textbox", name="标的").fill("SHA:600519")
            expect(form.get_by_role("radio", name=re.compile("均线趋势"))).to_be_checked(timeout=20000)
            run_button.click()
            wait_result()
            shot(page, "backtest-lab.png")
            assert not desk_calls, desk_calls
            results["lab_mode"] = "passed"
        except Exception:
            if capture:
                page.screenshot(path=str(capture / "backtest-failure.png"))
            raise

        assert not errors, errors
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors}, ensure_ascii=False))
