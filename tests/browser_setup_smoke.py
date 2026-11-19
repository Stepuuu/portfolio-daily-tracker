"""The first-run setup wizard (/setup) in a real browser.

Fictional data only: empty portfolio directories, a synthetic v3.2 holdings file with one cost that
cannot be read, scripted quotes (``fake_quotes.json``, clock pinned to a Tuesday morning session) and a
fake OpenAI-compatible model server on loopback that this script runs itself. Journeys:

* a fresh directory: ``/`` opens /setup once; 从零开始: account and opening balance through the standard
  review; the fee profile; the market checks with their environment lines; the model key saved, tested
  (ok, then a classified failure) and never in the page, nor in the browser's storage; the checklist
  turns done; 完成 opens Today with the holding;
* dismissing: 稍后再说 on another empty directory, after which ``/`` stays on Today, also in a new session;
* the legacy file: preview, the unreadable cost flagged and filled, the proposal confirmed; the file is
  unchanged and the checklist says migrated;
* 390 px without sideways scrolling, and the read-only demo.

Set TRACKER_SCREENSHOT_DIR to keep screenshots.
"""
from contextlib import ExitStack
import http.server
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

from runner_date import create_demo_argv

ROOT = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = "light" if os.environ.get("TRACKER_SCREENSHOT_THEME") == "light" else "dark"
NOW = "2026-11-17T10:32:05+08:00"   # a Tuesday, A-share morning session
KEY = "sk-smoke-" + "SENTINEL" + "-0123456789"
BAD_KEY = "sk-smoke-" + "WRONGKEY" + "-0123456789"
QUOTES = {
    "now": NOW,
    "quotes": {
        "SHA:603999": {"name": "示例蓝筹", "price": "20.00", "prev_close": "19.80", "quote_time": "2026-11-17T10:32:00+08:00"},
    },
    "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90", "age_seconds": 5}},
}
LEGACY = {
    "date": "2026-11-14",
    "groups": {
        "长期账户": {"cost_basis": 80000, "cash": 50000, "fund": 0, "positions": [
            {"name": "示例蓝筹", "ticker": "SHA:603999", "quantity": 1000, "cost_price": 18.5},
            {"name": "示例港股", "ticker": "HKG:00700", "quantity": 100, "cost_price": "约三百八"},
        ]},
    },
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


def label(scope, name):
    """The field labelled ``name``; a required field's label ends with an asterisk the screen reader skips."""
    return scope.get_by_label(re.compile("^" + re.escape(name) + r"\s*\*?$"))


def no_sideways_scroll(page, width):
    page.wait_for_timeout(300)
    widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
    assert max(widths) <= width, widths
    main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
    assert main[0] <= main[1], main


def full_screenshot(page, path):
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({"width": size["width"], "height": size["height"] + max(0, extra)})
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


class ModelServer:
    """An OpenAI-compatible chat endpoint: ``Bearer KEY`` gets an answer, any other key a 401 that echoes it."""

    def __init__(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                auth = self.headers.get("Authorization", "")
                if self.path != "/v1/chat/completions":
                    return self.answer(404, {"error": {"message": "no such route"}})
                if auth != f"Bearer {KEY}":
                    return self.answer(401, {"error": {"message": f"Incorrect API key provided: {auth}"}})
                return self.answer(200, {"id": "x", "model": body.get("model"), "usage": {},
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "pong"}}]})

            def answer(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def portfolio(directory, legacy=None):
    directory.mkdir(parents=True)
    (directory / "fake_quotes.json").write_text(json.dumps(QUOTES, ensure_ascii=False), encoding="utf-8")
    if legacy is not None:
        (directory / "holdings").mkdir()
        path = directory / "holdings" / "2026-11-14.json"
        path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    return None


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="setup-browser-")))
    fresh, dismissed, migrating = out / "fresh", out / "dismissed", out / "migrating"
    portfolio(fresh)
    portfolio(dismissed)
    legacy_file = portfolio(migrating, LEGACY)
    legacy_bytes = legacy_file.read_bytes()
    demo = out / "demo"
    subprocess.run(create_demo_argv("--output", str(demo), "--ledger",
                                    "--today", NOW[:10]),   # the demo is made for the day the scripted clock shows
                   check=True, stdout=subprocess.DEVNULL)
    model = ModelServer()
    stack.callback(model.close)
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, "PORTFOLIO_DIR": str(fresh), "TRACKER_CONFIG_FILE": str(out / "config.json"),
                "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_QUOTE_SOURCE": "fake", "TRACKER_DEMO_MODE": "0",
                "TRACKER_LAB_ONLY": "0", "PYTHONPATH": str(ROOT / "dashboard"),
                "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        base_env.pop(name, None)
    log_path = out / "preview.log"
    log = stack.enter_context(log_path.open("w"))
    capture = Path(os.environ["TRACKER_SCREENSHOT_DIR"]) if os.environ.get("TRACKER_SCREENSHOT_DIR") else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def shot(page, name, full=False):
        if capture:
            page.wait_for_timeout(250)
            (full_screenshot(page, capture / name) if full else page.screenshot(path=str(capture / name)))

    servers = []

    def backend(**overrides):
        for running in servers:
            stop(running)
        servers.clear()
        env = {**base_env, **{key: str(value) for key, value in overrides.items()}}
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "browser_app:app", "--app-dir", str(ROOT / "tests"),
                                    "--host", "127.0.0.1", "--port", str(backend_port)],
                                   cwd=out, env=env, stdout=log, stderr=log)
        servers.append(process)
        stack.callback(lambda: process.poll() is None and stop(process))
        ready(f"http://127.0.0.1:{backend_port}/health", process)
        return process

    backend()
    frontend = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--strictPort"],
                                cwd=ROOT / "dashboard/frontend", env=base_env, stdout=log, stderr=log)
    stack.callback(stop, frontend)
    ready(f"http://127.0.0.1:{frontend_port}", frontend)
    url = f"http://127.0.0.1:{frontend_port}"
    api = f"http://127.0.0.1:{backend_port}/api"
    results = {}

    def checklist():
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"{api}/setup/status?quotes=0", timeout=10) as answer:
            return {item["id"]: item for item in json.load(answer)["items"]}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                                              headless=True, args=["--no-sandbox"])
        errors = []

        def new_page(width=1440, height=900):
            context = browser.new_context(color_scheme=SCHEME, viewport={"width": width, "height": height}, locale="zh-CN",
                                          timezone_id="Asia/Shanghai")
            context.grant_permissions(["clipboard-read", "clipboard-write"])
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            return page

        page = new_page()
        toasts = page.get_by_role("region", name="通知")
        try:
            # -- a fresh directory: / opens the wizard once -------------------------------------------
            page.goto(url + "/")
            expect(page).to_have_url(re.compile(r"/setup$"), timeout=60000)
            expect(page.get_by_role("heading", name="设置向导")).to_be_visible()
            steps = page.get_by_role("navigation", name="设置步骤")
            expect(steps.get_by_role("button")).to_have_count(6)
            expect(steps.get_by_role("button", name=re.compile("欢迎"))).to_have_attribute("aria-current", "step")
            expect(page.get_by_role("heading", name="几分钟完成首次设置")).to_be_visible()
            shot(page, "setup-welcome.png", full=True)
            # Once: Today stays Today on the next visit in this tab.
            page.goto(url + "/")
            expect(page.get_by_text("还没有已确认的持仓")).to_be_visible(timeout=30000)
            page.wait_for_timeout(1500)
            assert page.url.rstrip("/") == url, page.url
            page.get_by_role("link", name="开始设置").click()
            expect(page).to_have_url(re.compile(r"/setup$"))
            results["first_visit_redirect"] = "once"

            # -- 从零开始: account and opening balance through the standard review -----------------------
            page.get_by_role("button", name=re.compile("^从零开始")).click()
            expect(page).to_have_url(re.compile(r"step=account&start=zero"))
            form = page.get_by_role("region", name="新建账户")
            expect(label(form, "账户名称")).to_have_value("主账户")
            label(form, "期初日期").fill("2026-11-14")
            label(form, "CNY 期初现金").fill("20000")
            form.get_by_label("期初持仓 1 代码").fill("SHA:603999")
            form.get_by_label("期初持仓 1 数量").fill("100")
            form.get_by_label("期初持仓 1 单位成本").fill("18")
            expect(form).to_contain_text("累计净投入建议：¥21,800.00（按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金）")
            form.get_by_role("button", name="填入建议值").click()
            expect(label(form, "累计净投入（CNY）")).to_have_value("21800")
            shot(page, "setup-account-form.png", full=True)
            form.get_by_role("button", name="预览入账").click()
            review = page.get_by_role("region", name="核对入账")
            expect(review).to_contain_text("1 笔待入账")
            expect(review.get_by_role("table")).to_contain_text("无")
            shot(page, "setup-account-review.png", full=True)
            assert checklist()["opening"]["state"] == "todo"
            page.get_by_role("button", name="确认入账").click()
            expect(toasts).to_contain_text("已入账 1 笔")
            expect(page.get_by_text("这一步已完成")).to_be_visible()
            expect(page.get_by_text("已有期初余额：主账户", exact=False)).to_be_visible()
            expect(steps.get_by_role("button", name=re.compile("账户与期初.*已完成"))).to_be_visible()
            assert checklist()["opening"]["state"] == "done"
            results["opening"] = "passed"

            # -- a v3.2 file with another account, found after the ledger has an opening: still offered ----
            extra = fresh / "holdings" / "2026-11-13.json"
            extra.parent.mkdir(exist_ok=True)
            extra.write_text(json.dumps({"groups": {"旧账户": {"cost_basis": 100, "cash": 100}}}, ensure_ascii=False),
                             encoding="utf-8")
            page.goto(url + "/setup?step=account")
            expect(page.get_by_text("还没有迁移：holdings/2026-11-13.json（旧账户）", exact=False)).to_be_visible(timeout=30000)
            assert checklist()["legacy"]["state"] == "optional"
            page.get_by_role("button", name="迁移旧版持仓", exact=True).click()
            expect(page).to_have_url(re.compile(r"step=account&start=legacy"))
            offered = page.get_by_role("region", name="迁移旧版持仓")
            expect(offered.get_by_role("heading", name="旧账户")).to_be_visible(timeout=30000)
            shot(page, "setup-legacy-offer.png", full=True)
            extra.unlink()
            page.goto(url + "/setup?step=account")
            expect(page.get_by_text("这一步已完成")).to_be_visible(timeout=30000)
            expect(page.get_by_text("发现旧版持仓文件")).to_have_count(0)
            results["legacy_offer"] = "an account not in the ledger is offered after the first opening"

            # -- 费率: the account's fee profile ------------------------------------------------------------
            page.get_by_role("navigation", name="向导翻页").get_by_role("button", name=re.compile("下一步")).click()
            expect(page).to_have_url(re.compile(r"step=fees"))
            expect(page.get_by_text("佣金万分之 2.5，最低 5 元", exact=False)).to_be_visible()
            section = page.get_by_role("region", name="账户与费率")
            expect(label(section, "账户")).to_have_value("主账户", timeout=30000)
            expect(section).to_contain_text("预设")
            assert checklist()["fees"]["state"] == "todo"
            label(section, "佣金率").fill("1")
            section.get_by_role("button", name="保存", exact=True).click()
            expect(toasts).to_contain_text("已保存「主账户」的费率")
            expect(page.get_by_text("1 个账户的费率都已核对保存", exact=False)).to_be_visible()
            assert checklist()["fees"]["state"] == "done"
            shot(page, "setup-fees.png", full=True)
            results["fees"] = "passed"

            # -- 行情与提醒: what runs, and the exact lines for what is configured outside the UI ----------
            page.get_by_role("navigation", name="向导翻页").get_by_role("button", name=re.compile("下一步")).click()
            checks = page.get_by_role("list", name="行情与提醒的检查结果")
            expect(checks).to_contain_text("TRACKER_QUOTE_SOURCE=fake")
            expect(checks.locator("li").filter(has_text="行情源")).to_contain_text("已完成")
            alerts = checks.locator("li").filter(has_text="价格提醒监控")
            expect(alerts).to_contain_text("待处理")
            expect(alerts.locator("code")).to_have_text("make start")
            routines = checks.locator("li").filter(has_text="每日例行任务")
            expect(routines).to_contain_text("收盘快照 15:40 开")
            expect(routines.locator("code")).to_contain_text("TRACKER_PREMARKET_TIME=08:45")
            shot(page, "setup-market.png", full=True)
            results["market"] = "passed"

            # -- AI 助手: saved, tested, a classified failure, the key never in the page -----------------
            page.get_by_role("navigation", name="向导翻页").get_by_role("button", name=re.compile("下一步|跳过")).click()
            expect(page).to_have_url(re.compile(r"step=ai"))
            ai = page.get_by_role("region", name="AI 助手（可选）")
            expect(ai.get_by_text("未配置")).to_be_visible()
            ai.get_by_role("radio", name="兼容接口").click()
            label(ai, "模型名称").fill("local-model")
            label(ai, "API 地址").fill(model.url)
            key_field = label(ai, "API 密钥")
            assert key_field.get_attribute("type") == "password"
            key_field.fill(KEY)
            ai.get_by_role("button", name="保存", exact=True).click()
            expect(toasts).to_contain_text("已保存：兼容接口 / local-model")
            expect(key_field).to_have_value("")
            expect(key_field).to_have_attribute("placeholder", "已保存（不显示）")
            ai.get_by_role("button", name="测试连接").click()
            expect(ai.get_by_role("status").filter(has_text="连接成功")).to_contain_text("local-model")
            shot(page, "setup-ai-ok.png", full=True)
            key_field.fill(BAD_KEY)
            ai.get_by_role("button", name="保存", exact=True).click()
            expect(key_field).to_have_value("")
            ai.get_by_role("button", name="测试连接").click()
            failure = ai.get_by_role("alert").filter(has_text="连接失败")
            expect(failure).to_contain_text("密钥无效或已失效（服务返回 401）")
            shot(page, "setup-ai-failed.png", full=True)
            content = page.content()
            assert "SENTINEL" not in content and "WRONGKEY" not in content
            assert "Incorrect API key" not in content
            stored = page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])")
            assert "SENTINEL" not in stored and "WRONGKEY" not in stored, stored
            key_field.fill(KEY)
            ai.get_by_role("button", name="保存", exact=True).click()
            expect(key_field).to_have_value("")
            ai.get_by_role("button", name="测试连接").click()
            expect(ai.get_by_role("status").filter(has_text="连接成功")).to_be_visible()
            assert "SENTINEL" not in page.content()
            assert checklist()["llm"]["state"] == "done"
            results["model"] = "saved, tested, failure classified, key not in page or storage"

            # -- 完成: the checklist, then Today with the holding ------------------------------------------
            page.get_by_role("navigation", name="向导翻页").get_by_role("button", name=re.compile("下一步")).click()
            expect(page).to_have_url(re.compile(r"step=done"))
            done = page.get_by_role("region", name="已完成", exact=True)
            for name in ("账户与期初余额", "账户费率", "行情源", "AI 助手（可选）", "旧版持仓文件"):
                expect(done).to_contain_text(name)
            expect(page.get_by_role("region", name="待处理", exact=True)).to_contain_text("价格提醒监控")
            shot(page, "setup-done.png", full=True)
            page.get_by_role("button", name="完成，打开今日").click()
            expect(page).to_have_url(re.compile(r"/$"))
            holdings = page.get_by_role("table", name="持仓")
            row = holdings.locator("tbody tr").filter(has_text="SHA:603999").first
            expect(row).to_contain_text("示例蓝筹", timeout=30000)
            expect(row).to_contain_text("20.00")
            shot(page, "today-after-setup.png")
            results["finish"] = "Today shows the holding"
            page.context.close()

            # -- 稍后再说: no redirect afterwards, also in a new session -------------------------------------
            backend(PORTFOLIO_DIR=dismissed)
            page = new_page()
            toasts = page.get_by_role("region", name="通知")
            page.goto(url + "/")
            expect(page).to_have_url(re.compile(r"/setup$"), timeout=60000)
            page.get_by_role("button", name="稍后再说").click()
            expect(toasts).to_contain_text("已关闭设置向导")
            expect(page).to_have_url(re.compile(r"/$"))
            expect(page.get_by_text("还没有已确认的持仓")).to_be_visible()
            page.context.close()
            page = new_page()
            page.goto(url + "/")
            expect(page.get_by_text("还没有已确认的持仓")).to_be_visible(timeout=60000)
            page.wait_for_timeout(2000)
            assert page.url.rstrip("/") == url, page.url
            expect(page.get_by_role("link", name="开始设置")).to_be_visible()
            shot(page, "today-dismissed.png")
            page.context.close()
            results["dismiss"] = "no redirect afterwards"

            # -- the legacy holdings file: preview, the unreadable cost, the proposal ------------------------
            backend(PORTFOLIO_DIR=migrating)
            page = new_page()
            toasts = page.get_by_role("region", name="通知")
            page.goto(url + "/setup")
            expect(page.get_by_role("heading", name="设置向导")).to_be_visible(timeout=60000)
            page.get_by_role("button", name=re.compile("^迁移旧版持仓")).click()
            migration = page.get_by_role("region", name="迁移旧版持仓")
            expect(migration).to_contain_text("读取 holdings/2026-11-14.json（2026-11-14） · 1 个账户 · 2 只持仓 · 1 项需要填写", timeout=30000)
            account = migration.get_by_role("region", name="旧账户 长期账户")
            expect(account).to_contain_text("¥50,000.00")
            expect(account).to_contain_text("¥80,000.00")
            expect(account).to_contain_text("1,000")
            expect(account).to_contain_text("¥18.50")
            expect(account).to_contain_text("成本价无法读取：“约三百八”")
            cost = account.get_by_label("长期账户 示例港股 成本价（需要填写）")
            expect(cost).to_have_value("")
            shot(page, "setup-legacy-draft.png", full=True)
            migration.get_by_role("button", name="生成入账预览").click()
            expect(account.get_by_text("请填写", exact=True)).to_be_visible()
            cost.fill("380")
            migration.get_by_role("button", name="生成入账预览").click()
            review = page.get_by_role("region", name="核对入账")
            expect(review).to_contain_text("1 笔待入账")
            expect(review.get_by_role("table")).to_contain_text("示例港股")
            shot(page, "setup-legacy-review.png", full=True)
            page.get_by_role("button", name="确认入账").click()
            expect(toasts).to_contain_text("已入账 1 笔")
            expect(page.get_by_text("这一步已完成")).to_be_visible()
            legacy_item = checklist()["legacy"]
            assert legacy_item["state"] == "done" and "已迁移" in legacy_item["detail"], legacy_item
            assert legacy_file.read_bytes() == legacy_bytes
            # The migration's own link, visited again: the file is migrated, so nothing offers it a second time.
            page.goto(url + "/setup?step=account&start=legacy")
            expect(page.get_by_text("这一步已完成")).to_be_visible(timeout=30000)
            expect(page.get_by_role("region", name="迁移旧版持仓")).to_have_count(0)
            expect(page.get_by_role("button", name="生成入账预览")).to_have_count(0)
            results["legacy"] = "unreadable cost flagged, filled, confirmed; file unchanged; its link stays closed"

            # -- 390 px: no sideways scrolling ---------------------------------------------------------------
            page.set_viewport_size({"width": 390, "height": 844})
            for step in ("welcome", "account", "fees", "market", "ai", "done"):
                page.goto(f"{url}/setup?step={step}")
                expect(page.get_by_role("heading", name="设置向导")).to_be_visible(timeout=30000)
                expect(page.get_by_role("navigation", name="设置步骤")).to_be_visible()
                page.wait_for_timeout(500)
                no_sideways_scroll(page, 390)
                shot(page, f"setup-{step}-mobile.png", full=True)
            page.context.close()
            page = new_page(390, 844)
            backend(PORTFOLIO_DIR=dismissed)
            page.goto(f"{url}/setup?step=account&start=zero")
            expect(page.get_by_role("region", name="新建账户")).to_be_visible(timeout=60000)
            no_sideways_scroll(page, 390)
            shot(page, "setup-account-form-mobile.png", full=True)
            page.context.close()
            results["mobile_width"] = 390

            # -- the demo: every step viewable, nothing can be saved --------------------------------------------
            backend(PORTFOLIO_DIR=demo, TRACKER_DEMO_MODE=1)
            page = new_page()
            page.goto(url + "/setup?step=ai")
            expect(page.get_by_text("演示模式：只能浏览")).to_be_visible(timeout=60000)
            ai = page.get_by_role("region", name="AI 助手（可选）")
            save = ai.get_by_role("button", name="保存", exact=True)
            expect(save).to_have_attribute("aria-disabled", "true")
            assert page.locator(f"[id='{save.get_attribute('aria-describedby')}']").text_content() == "演示模式只读"
            expect(ai.get_by_role("button", name="测试连接")).to_have_attribute("aria-disabled", "true")
            shot(page, "setup-demo-ai.png", full=True)
            page.goto(url + "/setup?step=account")
            expect(page.get_by_text("这一步已完成")).to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="再建一个账户")).to_have_attribute("aria-disabled", "true")
            page.goto(url + "/setup?step=fees")
            expect(page.get_by_role("region", name="账户与费率").get_by_role("button", name="保存", exact=True)).to_have_attribute("aria-disabled", "true", timeout=30000)
            page.goto(url + "/setup?step=done")
            expect(page.get_by_role("link", name=re.compile("打开今日"))).to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="完成，打开今日")).to_have_count(0)
            expect(page.get_by_role("link", name="返回今日")).to_be_visible()
            shot(page, "setup-demo-done.png", full=True)
            page.goto(url + "/")
            expect(page.get_by_role("table", name="持仓")).to_be_visible(timeout=30000)
            page.wait_for_timeout(1000)
            assert page.url.rstrip("/") == url, page.url
            results["demo"] = "read-only"
        except Exception:
            if capture:
                page.screenshot(path=str(capture / "failure.png"))
            print(log_path.read_text()[-3000:], file=sys.stderr)
            raise
        assert not errors, errors
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors},
                     ensure_ascii=False))
