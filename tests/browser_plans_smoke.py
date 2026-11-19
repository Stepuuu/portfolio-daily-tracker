"""计划 (/plans) in a real browser, on disposable fictional data.

A demo portfolio with an opening-balance ledger and scripted quotes (``fake_quotes.json``, clock pinned
to a Tuesday morning session). The journey: an empty list invites the first plan; a draft is created and
saved with 保存并启用 (while that save is on its way, 保存草稿 and Enter send nothing), which lists the
alert rules it made; the plan shows its distance to the stop (price 61.20, stop 54: -11.76%) in the drawer
and the table; 按计划买入 opens the entry with the plan's id; rules and trades that cannot be read are
不可用; Today shows the chip and it opens the drawer, and says 计划 不可用 when the plans cannot be read; an
edit made against an old version of the plan is refused and lists what changed, a fresh edit sends only
the changed field with the version, and a plan due for review is listed in Today's 今日事项; a second
active plan for the same ticker shows the backend's 409 message on the form and keeps the draft, whose row
shows the market block, the name and 未填写 for its loss; ending the plan needs a reason and leaves it
read-only; /plans?ticker= shows one listing's plans; focus returns to the opener after Esc; 390 px has no
sideways scrolling, even with a long unbroken thesis in the drawer; demo mode lists the plans and disables
every write with its reason. No page errors.
Set TRACKER_SCREENSHOT_DIR to keep screenshots.
"""
from contextlib import ExitStack
from decimal import Decimal
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

ROOT = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = "light" if os.environ.get("TRACKER_SCREENSHOT_THEME") == "light" else "dark"
NOW = "2026-11-17T10:32:05+08:00"   # a Tuesday, A-share morning session; New York closed


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



with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="plans-browser-")))
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
    results = {}

    def shot(page, name):
        if capture:
            page.wait_for_timeout(450)   # drawers slide in
            page.screenshot(path=str(capture / name))

    api = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def plan_api(method, plan_id, body=None):
        """A plan read or edited behind the page's back (another tab, the assistant)."""
        request = urllib.request.Request(f"http://127.0.0.1:{backend_port}/api/plans/{plan_id}", method=method,
                                         data=None if body is None else json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with api.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def fill_plan(page, dialog, thesis, quantity=None):
        dialog.get_by_label("标的").fill("SHA:DEMO")
        dialog.get_by_label("为什么买").fill(thesis)
        dialog.get_by_label("什么情况说明我错了").fill("放量跌破 54")
        dialog.get_by_label("入场下限").fill("58")
        dialog.get_by_label("入场上限").fill("61")
        dialog.get_by_label("止损").fill("54")
        dialog.get_by_label("目标").fill("66")
        if quantity:
            dialog.get_by_role("radio", name="计划股数").click()
            dialog.get_by_role("textbox", name="计划股数").fill(quantity)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                                              headless=True, args=["--no-sandbox"])
        page = browser.new_page(color_scheme=SCHEME, viewport={"width": 1440, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # The 409 on the second active plan is expected: the browser logs it and so does the API client; so is
        # the 503 of the plans while they are made unreadable on purpose.
        expected = ["409", "已有启用中的交易计划"]
        page.on("console", lambda message: errors.append(message.text)
                if message.type == "error" and not any(text in message.text for text in expected) else None)
        # A server error names its request, so a failure says which one.
        page.on("response", lambda response: errors.append(f"{response.status} {response.request.method} {response.url}")
                if response.status >= 500 and not any(text in str(response.status) for text in expected) else None)

        # -- the empty page invites the first plan ------------------------------------------------
        page.goto(url + "/plans")
        expect(page.get_by_text("还没有交易计划")).to_be_visible(timeout=60000)
        expect(page.get_by_role("navigation", name="计划与复盘").get_by_role("link", name="计划")).to_have_attribute("aria-current", "page")
        shot(page, "plans-empty.png")
        results["empty"] = "passed"

        # -- a draft with a planned quantity, saved and enabled; the alert rules are listed -----------
        page.get_by_role("button", name="新建计划").first.click()
        dialog = page.get_by_role("dialog", name="新建交易计划")
        expect(dialog).to_be_visible()
        # The ticker field has the focus and its list (holdings and watchlist) opens under it, where it was
        # placed on the screen: the dialog does not move it, and does not scroll sideways because of it (its
        # content then jumped back when the list closed, and a click on the footer missed its button).
        popup = page.get_by_role("listbox", name="代码搜索结果").locator("xpath=..")
        expect(popup).to_be_visible()
        page.wait_for_timeout(300)   # the dialog's entrance animation
        field, placed = dialog.get_by_label("标的").bounding_box(), popup.bounding_box()
        assert abs(placed["x"] - field["x"]) <= 2 and 0 <= placed["y"] - (field["y"] + field["height"]) <= 8, (field, placed)
        assert dialog.evaluate("node => [node.scrollLeft, node.scrollWidth - node.clientWidth]") == [0, 0]
        # Required fields are checked before anything is sent.
        dialog.get_by_role("button", name="保存草稿").click()
        expect(dialog.get_by_text("请写下为什么买")).to_be_visible()
        fill_plan(page, dialog, "指数估值处在低位", quantity="1200")
        expect(dialog.get_by_label("账户")).to_have_value("长期账户")   # the only account holding it
        estimate = dialog.get_by_text("最大亏损约", exact=False)
        expect(estimate).to_contain_text("8,400.00 CNY")
        expect(dialog.get_by_text("估算", exact=True)).to_be_visible()
        shot(page, "plans-form.png")
        # One save at a time, however it is asked for: while the create is on its way (held here), 保存草稿 and
        # Enter send nothing, so a slow save never makes two plans.
        held, creates = [], []

        def hold(route):
            if route.request.method == "POST":
                held.append(route)
            else:
                route.continue_()

        def count(request):
            if request.method == "POST" and request.url.split("?")[0].endswith("/api/plans"):
                creates.append(request.url)

        creating = re.compile(r".*/api/plans$")
        page.on("request", count)
        page.route(creating, hold)
        dialog.get_by_role("button", name="保存并启用").click()
        for _ in range(400):
            if held:
                break
            page.wait_for_timeout(50)
        assert held, "保存并启用 sent no create"
        dialog.get_by_role("button", name="保存草稿").click(force=True)
        dialog.get_by_label("止损").press("Enter")
        page.wait_for_timeout(800)
        assert len(creates) == 1, f"one save sent {len(creates)} creates"
        page.unroute(creating, hold)
        page.remove_listener("request", count)
        held[0].continue_()
        done = page.get_by_role("dialog", name="计划已启用")
        expect(done).to_be_visible()
        rules = done.get_by_role("list", name="提醒规则")
        expect(rules).to_contain_text("止损提醒：价格跌到 54 或以下")
        expect(rules).to_contain_text("目标价提醒：价格涨到 66 或以上")
        shot(page, "plans-activated.png")
        done.get_by_role("button", name="查看计划").click()
        drawer = page.get_by_role("dialog", name="SHA:DEMO", exact=False)
        expect(drawer).to_be_visible()
        expect(drawer.get_by_text("启用中", exact=True)).to_be_visible()
        expect(drawer.get_by_text("指数估值处在低位")).to_be_visible()
        expect(drawer.get_by_text("放量跌破 54")).to_be_visible()
        expect(drawer.get_by_text("(入场价上限 61 − 止损价 54) × 计划股数 1200", exact=False)).to_be_visible()
        stop_value = drawer.locator("dt", has_text="距止损").locator("xpath=following-sibling::dd[1]")
        expect(stop_value).to_contain_text("-11.76%")
        expect(drawer.locator("dt", has_text="距目标").locator("xpath=following-sibling::dd[1]")).to_contain_text("+7.84%")
        expect(drawer.get_by_text("持有 1,000 股 / 计划 1,200 股")).to_be_visible()
        expect(drawer.get_by_role("button", name="按计划买入")).to_be_visible()
        expect(drawer.get_by_text("还没有对应的成交", exact=False)).to_be_visible()
        shot(page, "plans-drawer-active.png")
        # Buying under the plan opens the entry with the plan's id; nothing is booked here.
        drawer.get_by_role("button", name="按计划买入").click()
        entry = page.get_by_role("dialog", name="记一笔")
        expect(entry).to_be_visible()
        expect(entry.get_by_text("来自交易计划 #1", exact=False)).to_be_visible()
        page.keyboard.press("Escape")
        expect(entry).to_have_count(0)
        expect(drawer).to_be_hidden()
        results["created_and_activated"] = "passed"

        # -- the table: right-aligned figures, the distance, keyboard open and focus back --------------
        table = page.get_by_role("table", name="交易计划")
        row = table.locator("tbody tr").filter(has_text="SHA:DEMO").first
        expect(row).to_contain_text("启用中")
        expect(row).to_contain_text("-11.76%")
        expect(row).to_contain_text("58–61")
        stop_cell = row.locator("td").nth(3)
        assert stop_cell.evaluate("node => getComputedStyle(node).textAlign") == "right"
        assert "tabular-nums" in stop_cell.evaluate("node => getComputedStyle(node).fontVariantNumeric")
        shot(page, "plans-table.png")
        row.focus()
        page.keyboard.press("Enter")
        expect(drawer).to_be_visible()
        page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()
        assert row.evaluate("node => node === document.activeElement"), "focus returns to the row that opened the drawer"
        # Rules or linked trades the backend could not read are 不可用 with its reason, never "none".
        def unreadable(route):
            if route.request.method != "GET":
                route.continue_()
                return
            response = route.fetch()
            detail = response.json()
            detail.update(links=None, links_error="关联成交暂时无法读取：测试", alerts=None, alerts_error="提醒规则暂时无法读取：测试")
            route.fulfill(response=response, json=detail)

        first = re.compile(r".*/api/plans/1(\?.*)?$")
        page.route(first, unreadable)
        page.reload()
        row.click()
        expect(drawer).to_be_visible()
        expect(drawer.get_by_text("关联成交暂时无法读取：测试")).to_be_visible()
        expect(drawer.get_by_text("提醒规则暂时无法读取：测试")).to_be_visible()
        expect(drawer.get_by_text("还没有对应的成交", exact=False)).to_have_count(0)
        expect(drawer.get_by_text("没有提醒规则", exact=False)).to_have_count(0)
        shot(page, "plans-drawer-unreadable.png")
        page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()
        page.unroute(first, unreadable)
        results["table"] = "passed"

        # -- Today: the chip opens the same drawer ---------------------------------------------------
        page.goto(url + "/")
        holdings = page.get_by_role("table", name="持仓")
        chip = holdings.get_by_role("button", name="计划 · 距止损 -11.76%")
        expect(chip).to_be_visible(timeout=60000)
        # The holding without a plan is on the page, so its missing chip says something.
        unplanned = holdings.locator("tbody tr").filter(has_text="NASDAQ:DEMO")
        expect(unplanned.first).to_be_visible()
        expect(unplanned.get_by_text("计划", exact=False)).to_have_count(0)
        shot(page, "plans-today-chip.png")
        chip.click()
        expect(page.get_by_role("dialog", name="SHA:DEMO", exact=False)).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog", name="SHA:DEMO", exact=False)).to_be_hidden()
        assert chip.evaluate("node => node === document.activeElement"), "focus returns to the chip that opened the drawer"
        # The plans cannot be read: the chip says so instead of disappearing.
        failing = re.compile(r".*/api/plans\?.*")
        expected.extend(["503", "交易计划暂时无法读取"])
        page.route(failing, lambda route: route.fulfill(status=503, content_type="application/json",
                                                         body=json.dumps({"detail": "交易计划暂时无法读取"})))
        page.reload()
        unavailable = page.get_by_role("table", name="持仓").get_by_role("button", name="计划 不可用").first
        expect(unavailable).to_be_visible(timeout=60000)
        assert "交易计划读取失败" in (unavailable.get_attribute("title") or "")
        shot(page, "plans-today-chip-unavailable.png")
        page.unroute(failing)
        unavailable.click()                                # retries
        del expected[-2:]
        expect(page.get_by_role("table", name="持仓").get_by_role("button", name="计划 · 距止损 -11.76%")).to_be_visible(timeout=60000)
        results["today_chip"] = "passed"

        # -- editing the active plan: a stale edit is refused and says what changed; a fresh one sends only the change
        page.goto(url + "/plans")
        table = page.get_by_role("table", name="交易计划")
        drawer = page.get_by_role("dialog", name="SHA:DEMO", exact=False)
        form = page.get_by_role("dialog", name="编辑交易计划")
        table.locator("tbody tr").filter(has_text="SHA:DEMO").first.click()
        drawer.get_by_role("button", name="编辑", exact=True).click()
        expect(form.get_by_text("保存后，它的提醒规则会立即按新的止损、目标和计划仓位更新", exact=False)).to_be_visible()
        plan_api("PATCH", 1, {"stop_price": "55"})          # changed elsewhere while this form is open
        expected.append("已在别处修改")                      # the API client logs the expected 409
        # The plan under the form is refreshed (every 30 s) and now has stop 55; the edit still counts from the
        # version the form opened with, so it is refused instead of putting 54 back.
        with page.expect_response(lambda response: response.request.method == "GET"
                                  and re.search(r"/api/plans/1(\?|$)", response.url) is not None, timeout=60000):
            pass
        form.get_by_label("为什么买").fill("指数估值处在低位，分红稳定")
        form.locator("footer").get_by_role("button", name="保存", exact=True).click()
        expect(form.get_by_role("alert")).to_contain_text("没有保存")
        expect(form.get_by_role("list", name="期间的修改")).to_contain_text("止损 54 → 55")
        shot(page, "plans-stale-edit.png")
        form.locator("footer").get_by_role("button", name="取消", exact=True).click()
        expect(form).to_be_hidden()
        expected.remove("已在别处修改")
        kept = plan_api("GET", 1)
        assert kept["thesis"] == "指数估值处在低位" and Decimal(kept["stop_price"]) == 55, kept
        page.goto(url + "/plans")
        table.locator("tbody tr").filter(has_text="SHA:DEMO").first.click()
        drawer.get_by_role("button", name="编辑", exact=True).click()
        sent = []

        def patched(request):
            if request.method == "PATCH":
                sent.append(request.post_data_json)

        page.on("request", patched)
        form.get_by_label("复查日").fill(NOW[:10])
        form.locator("footer").get_by_role("button", name="保存", exact=True).click()
        expect(form).to_be_hidden()
        page.remove_listener("request", patched)
        assert len(sent) == 1 and sorted(sent[0]) == ["if_version", "review_on"] and sent[0]["review_on"] == NOW[:10], sent
        page.keyboard.press("Escape")
        results["edit"] = "passed"

        # -- Today's 今日事项 lists the plan once its review date has come ------------------------------------
        page.goto(url + "/")
        due = page.get_by_role("list", name="到期的交易计划")
        expect(due).to_contain_text("SHA:DEMO", timeout=60000)
        shot(page, "plans-today-agenda.png")
        due.get_by_role("link", name="打开").click()
        expect(page.get_by_role("dialog", name="SHA:DEMO", exact=False)).to_be_visible()
        page.keyboard.press("Escape")
        results["agenda"] = "passed"

        # -- a second active plan for the same ticker: 409 on the form, the draft is kept --------------
        page.goto(url + "/plans")
        page.get_by_role("button", name="新建计划").first.click()
        dialog = page.get_by_role("dialog", name="新建交易计划")
        fill_plan(page, dialog, "再买一笔")
        dialog.get_by_role("button", name="保存并启用").click()
        alert = dialog.get_by_role("alert")
        expect(alert).to_contain_text("已保存为草稿，但没有启用")
        expect(alert).to_contain_text("SHA:DEMO 已有启用中的交易计划")
        shot(page, "plans-conflict.png")
        dialog.get_by_role("button", name="取消", exact=True).click()
        expect(dialog).to_be_hidden()
        page.get_by_role("radio", name="草稿").click()
        expect(page.get_by_role("table", name="交易计划").locator("tbody tr")).to_have_count(1)
        draft_row = page.get_by_role("table", name="交易计划").locator("tbody tr").first
        expect(draft_row).to_contain_text("草稿")
        # A draft has the market block too (price and distance), the instrument's name, and 未填写 for the loss
        # it cannot compute without a planned quantity.
        expect(draft_row).to_contain_text("示例宽基")
        expect(draft_row).to_contain_text("61.20")
        expect(draft_row).to_contain_text("-11.76%")
        expect(draft_row).to_contain_text("未填写")
        shot(page, "plans-draft-row.png")
        results["conflict"] = "passed"

        # -- ending the active plan: a reason is required; then it is read-only ---------------------------
        page.get_by_role("radio", name="进行中").click()
        page.get_by_role("table", name="交易计划").locator("tbody tr").first.click()
        drawer = page.get_by_role("dialog", name="SHA:DEMO", exact=False)
        drawer.get_by_role("button", name="结束", exact=True).click()
        ending = page.get_by_role("dialog", name="结束计划")
        ending.get_by_role("button", name="结束计划").click()
        expect(ending.get_by_text("请写明结束原因", exact=False)).to_be_visible()
        ending.get_by_label("结束原因").fill("已到目标价卖出")
        shot(page, "plans-end.png")
        ending.get_by_role("button", name="结束计划").click()
        expect(ending).to_be_hidden()
        expect(drawer.get_by_text("已关闭", exact=True).first).to_be_visible()
        expect(drawer.get_by_text("已到目标价卖出")).to_be_visible()
        for name in ("编辑", "结束", "按计划买入", "取消计划", "启用"):
            expect(drawer.get_by_role("button", name=name, exact=True)).to_have_count(0)
        shot(page, "plans-drawer-closed.png")
        page.keyboard.press("Escape")
        page.get_by_role("radio", name="已结束").click()
        expect(page.get_by_role("table", name="交易计划").locator("tbody tr").first).to_contain_text("已关闭")
        results["ended"] = "passed"

        # -- one listing's plans: /plans?ticker= in any spelling, and back to all ------------------------------
        page.goto(url + "/plans?ticker=NASDAQ:DEMO")
        expect(page.get_by_text("NASDAQ:DEMO 还没有交易计划")).to_be_visible(timeout=30000)
        page.goto(url + "/plans?ticker=sha:demo")
        page.get_by_role("radio", name="全部").click()
        expect(page.get_by_role("table", name="交易计划").locator("tbody tr")).to_have_count(2)
        page.get_by_role("button", name="只看 sha:demo").click()
        expect(page).to_have_url(re.compile(r".*/plans$"))
        results["listing"] = "passed"

        # -- a laptop and a phone: no sideways scrolling ----------------------------------------------------
        page.get_by_role("radio", name="全部").click()
        page.set_viewport_size({"width": 1100, "height": 800})
        page.wait_for_timeout(300)
        shot(page, "plans-laptop.png")
        page.set_viewport_size({"width": 390, "height": 844})
        unbroken = "A1" * 50                                       # 100 characters without a break
        plan_api("PATCH", 2, {"thesis": unbroken})
        page.goto(url + "/plans")
        expect(page.get_by_role("list", name="交易计划")).to_be_visible(timeout=30000)
        page.wait_for_timeout(300)
        widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
        assert max(widths) <= 390, widths
        main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
        assert main[0] <= main[1], main
        shot(page, "plans-mobile.png")
        page.get_by_role("list", name="交易计划").locator("li").first.click()
        drawer = page.get_by_role("dialog", name="SHA:DEMO", exact=False)
        expect(drawer).to_be_visible()
        widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
        assert max(widths) <= 390, widths
        # The drawer's own scrolling body does not scroll sideways either: the long word wraps.
        expect(drawer.get_by_text(unbroken)).to_be_visible()
        page.wait_for_timeout(450)   # the drawer slides in
        scrollers = drawer.evaluate("""node => [node, ...node.querySelectorAll('*')]
            .filter((el) => ['auto', 'scroll'].includes(getComputedStyle(el).overflowX))
            .map((el) => [el.className, el.scrollWidth, el.clientWidth])""")
        assert scrollers and all(width <= client + 1 for _, width, client in scrollers), scrollers
        box = drawer.get_by_text(unbroken).bounding_box()
        assert box["x"] + box["width"] <= 391, box
        shot(page, "plans-mobile-drawer.png")
        page.keyboard.press("Escape")
        page.get_by_role("button", name="新建计划").first.click()
        expect(page.get_by_role("dialog", name="新建交易计划")).to_be_visible()
        dialog_box = page.get_by_role("dialog", name="新建交易计划").evaluate("node => [node.scrollWidth, node.clientWidth]")
        assert dialog_box[0] <= dialog_box[1], dialog_box
        shot(page, "plans-mobile-form.png")
        page.keyboard.press("Escape")
        results["mobile_width"] = 390
        page.set_viewport_size({"width": 1440, "height": 900})
        # Leave the app before its backend restarts: Today is polled every 5 s in the pinned session, and a poll
        # while no backend listens gets the dev proxy's 500, a page error that is the fixture's, not the app's.
        page.goto("about:blank")
        stop(server)

        # -- demo mode: the plans are readable, every write says why it is off ------------------------------
        server = backend(TRACKER_DEMO_MODE="1")
        page.goto(url + "/plans")
        expect(page.get_by_text("数据为虚构，只读")).to_be_visible(timeout=30000)
        page.get_by_role("radio", name="全部").click()
        table = page.get_by_role("table", name="交易计划")
        expect(table.locator("tbody tr")).to_have_count(2)
        new = page.get_by_role("button", name="新建计划").first
        expect(new).to_have_attribute("aria-disabled", "true")
        assert page.locator(f"[id='{new.get_attribute('aria-describedby')}']").text_content() == "演示模式只读"
        new.click(force=True)
        expect(page.get_by_role("dialog", name="新建交易计划")).to_have_count(0)
        page.get_by_role("radio", name="草稿").click()
        table.locator("tbody tr").first.click()
        drawer = page.get_by_role("dialog", name="SHA:DEMO", exact=False)
        expect(drawer.get_by_text("草稿", exact=True).first).to_be_visible()
        for name in ("编辑", "启用", "取消计划"):
            expect(drawer.get_by_role("button", name=name, exact=True)).to_have_attribute("aria-disabled", "true")
        shot(page, "plans-demo.png")
        results["demo"] = "passed"
        stop(server)

        if errors:
            log.flush()
            lines = (out / "preview.log").read_text(encoding="utf-8", errors="replace").splitlines()
            print("\n".join(lines[-60:]))
            # The failing requests and the server's last exception lines first: a runner may show one line only.
            raised = [line.strip() for line in lines if re.search(r"(Error|Exception)\b", line)][-3:]
            raise AssertionError([error for error in errors if error[:3].isdigit()] + raised + errors)
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors},
                     ensure_ascii=False))
