"""设置 (/settings/:section) in a real browser, on disposable fictional data.

A demo portfolio with a confirmed ledger and its desk data (watchlist, plans, alert rules), scripted quotes
and an assistant memory file of its own (fixture app ``tests/browser_settings_app.py``). Checks: every
section renders at 1440, 1100 and 390 px without sideways scrolling, with the current section marked in
the list (a select on phones, which navigates); old paths (/memory, /help, /dashboard, /settings/cash, a
bare or unknown /settings) land on their new place with the query and hash; 交易偏好 saves and survives a
reload, an emotional trigger and a lesson are added and deleted; a pending lesson proposal is confirmed
into the lessons, a profile proposal rejected, a rejected lesson and a rejected profile suggestion forgotten; 立即运行 shows the scheduler's
refusal; the three backup downloads start and are what they say; the theme switch; demo mode disables
every save with its reason and still downloads the desk backup; research-only mode shows only its three
sections. The restyled pages (组合走势, the research workbench, the assistant and its history) are opened
too. No page errors. Set TRACKER_SCREENSHOT_DIR to keep screenshots (each section, dark and light, 1440
and 390 px).
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
NOW = "2026-11-17T10:32:05+08:00"   # a Tuesday, A-share morning session

# (slug, label in the list, what shows once the section has loaded: before any proposal is seeded)
SECTIONS = [
    ("accounts", "账户与费率", lambda page: [
        page.get_by_role("region", name="账户与费率", exact=True).get_by_role("button", name="保存", exact=True)]),
    ("models", "模型", lambda page: [
        page.get_by_role("region", name="对话模型", exact=True).get_by_role("button", name="保存", exact=True)]),
    ("data", "数据与行情", lambda page: [
        page.get_by_role("list", name="节假日表收录的年份"), page.get_by_role("list", name="行情源的检查结果")]),
    ("automation", "自动化与通知", lambda page: [
        page.get_by_role("button", name=re.compile("^立即运行")).first, page.get_by_role("list", name="通知渠道")]),
    ("preferences", "交易偏好", lambda page: [
        page.get_by_role("region", name="交易画像", exact=True).get_by_role("button", name="保存", exact=True),
        page.get_by_text("没有放弃过的提议。", exact=True)]),
    ("backup", "备份与导出", lambda page: [page.get_by_role("button", name="下载桌面数据备份")]),
    ("about", "关于与帮助", lambda page: [page.get_by_text("完整模式", exact=True)]),
]


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


def full_screenshot(page, path):
    """The page scrolls inside <main>: grow the viewport to the content for a whole-page picture."""
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({"width": size["width"], "height": size["height"] + max(0, extra)})
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


def no_sideways_scroll(page, where):
    width = page.viewport_size["width"]
    widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
    assert max(widths) <= width, (where, width, widths)
    main = page.evaluate("(() => { const m = document.getElementById('main'); return m ? [m.scrollWidth, m.clientWidth] : [0, 0] })()")
    assert main[0] <= main[1], (where, main)


def write_quotes(directory):
    (directory / "fake_quotes.json").write_text(json.dumps({
        "now": NOW,
        "quotes": {
            "SHA:DEMO": {"name": "示例宽基", "price": "61.20", "prev_close": "60.80", "quote_time": "2026-11-17T10:32:02+08:00"},
            "NASDAQ:DEMO": {"name": "示例研究标的", "price": "119.60", "prev_close": "118.90",
                            "quote_time": "2026-11-17T05:00:00+08:00"},
        },
        "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90", "age_seconds": 5}},
    }, ensure_ascii=False), encoding="utf-8")


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="settings-browser-")))
    portfolio = out / "portfolio"
    subprocess.run([sys.executable, str(ROOT / "engine/scripts/create_demo.py"), "--output", str(portfolio), "--ledger",
                    "--today", NOW[:10]], check=True, stdout=subprocess.DEVNULL)
    write_quotes(portfolio)
    memory_file = out / "memory.json"
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, "PORTFOLIO_DIR": str(portfolio), "TRACKER_CONFIG_FILE": str(out / "config.json"),
                "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_QUOTE_SOURCE": "fake", "TRACKER_DEMO_MODE": "0",
                "TRACKER_LAB_ONLY": "0", "SETTINGS_SMOKE_MEMORY": str(memory_file), "PYTHONPATH": str(ROOT / "dashboard"),
                "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    log = stack.enter_context((out / "preview.log").open("w"))
    capture = Path(os.environ["TRACKER_SCREENSHOT_DIR"]) if os.environ.get("TRACKER_SCREENSHOT_DIR") else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def seed(code):
        """Runs ``code`` against the portfolio's desk store, as the assistant would."""
        subprocess.run([sys.executable, "-c", code], cwd=out, env=base_env, check=True)

    def backend(**overrides):
        """A backend on the fixed port (Vite proxies to it); ``app`` picks the application module."""
        app = overrides.pop("app", "browser_settings_app:app")
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
        page = browser.new_page(color_scheme=SCHEME, viewport={"width": 1440, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai",
                                accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        toasts = page.get_by_role("region", name="通知", exact=True)

        def open_section(slug, label, loaded, timeout=20000):
            page.goto(f"{url}/settings/{slug}")
            for locator in loaded(page):
                expect(locator).to_be_visible(timeout=timeout)
            expect(page.get_by_role("heading", level=1, name="设置")).to_be_visible()
            if page.viewport_size["width"] >= 768:
                nav = page.get_by_role("navigation", name="设置分区")
                expect(nav.get_by_role("link", name=label, exact=True)).to_have_attribute("aria-current", "page")
            else:
                expect(page.get_by_role("combobox", name="设置分区")).to_have_value(slug)
            page.wait_for_timeout(300)   # the section's other panels answer
            no_sideways_scroll(page, f"{slug} at {page.viewport_size['width']}")

        # -- every section, at each width ---------------------------------------------------------------
        first = True
        for width, height in ((1440, 900), (1100, 800), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            for slug, label, loaded in SECTIONS:
                open_section(slug, label, loaded, timeout=60000 if first else 20000)
                first = False
                if capture and width != 1100:
                    full_screenshot(page, capture / f"settings-{slug}-{'desktop' if width > 1000 else 'mobile'}.png")
            if width >= 768:
                links = page.get_by_role("navigation", name="设置分区").get_by_role("link").all_inner_texts()
                assert links == [label for _, label, _ in SECTIONS], links
        # On a phone the select moves between sections.
        page.get_by_role("combobox", name="设置分区").select_option("backup")
        expect(page).to_have_url(f"{url}/settings/backup")
        expect(page.get_by_role("button", name="下载桌面数据备份")).to_be_visible()
        if capture:
            page.evaluate("localStorage.setItem('pdt.theme', 'light')")
            for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
                page.set_viewport_size({"width": width, "height": height})
                for slug, label, loaded in SECTIONS:
                    open_section(slug, label, loaded)
                    full_screenshot(page, capture / f"settings-{slug}-{name}-light.png")
            page.evaluate("localStorage.removeItem('pdt.theme')")
        page.set_viewport_size({"width": 1440, "height": 900})
        results["sections"] = [slug for slug, _, _ in SECTIONS]

        # -- old paths land on their new place, query and hash kept --------------------------------------
        for old, new in (("/memory?from=old#lessons", "/settings/preferences?from=old#lessons"),
                         ("/help#keys", "/settings/about#keys"),
                         ("/dashboard?x=1", "/?x=1"),
                         ("/settings/cash", "/ledger"),
                         ("/settings/memory", "/settings/preferences"),
                         ("/settings?tab=1", "/settings/accounts?tab=1"),
                         ("/settings/nothing-here", "/settings/accounts")):
            page.goto(url + old)
            expect(page).to_have_url(url + new, timeout=20000)
        results["redirects"] = "passed"

        # -- 交易偏好: the profile saved and read back; a trigger and a lesson added and deleted ------------
        open_section("preferences", "交易偏好", SECTIONS[4][2])
        profile = page.get_by_role("region", name="交易画像")
        profile.get_by_label("交易经验").select_option(label="中级（1-5年）")
        profile.get_by_label("交易风格").select_option(label="中线持股")
        profile.get_by_label("风险偏好").select_option(label="保守（追求稳定）")
        profile.get_by_role("checkbox", name="半导体", exact=True).check()
        profile.get_by_label("备注").fill("虚构备注：单只不超过两成仓位。")
        profile.get_by_role("button", name="保存", exact=True).click()
        expect(toasts.get_by_text("已保存交易偏好")).to_be_visible()
        stored = page.request.get(f"{api}/memory").json()["profile"]
        assert (stored["experience_level"], stored["trading_style"], stored["risk_tolerance"]) == \
            ("intermediate", "position", "conservative"), stored
        assert stored["preferred_sectors"] == ["半导体"] and stored["notes"] == "虚构备注：单只不超过两成仓位。", stored
        assert json.loads(memory_file.read_text(encoding="utf-8"))["profile"]["trading_style"] == "position"
        page.reload()
        profile = page.get_by_role("region", name="交易画像")
        expect(profile.get_by_label("交易风格")).to_have_value("position", timeout=20000)
        expect(profile.get_by_label("风险偏好")).to_have_value("conservative")
        expect(profile.get_by_role("checkbox", name="半导体", exact=True)).to_be_checked()
        expect(profile.get_by_label("备注")).to_have_value("虚构备注：单只不超过两成仓位。")
        # Saving again with nothing changed sends nothing.
        profile.get_by_role("button", name="保存", exact=True).click()
        expect(toasts.get_by_text("没有需要保存的改动")).to_be_visible()

        triggers = page.get_by_role("region", name="情绪触发点")
        triggers.get_by_label("新的情绪触发点").fill("连续亏损后加仓")
        triggers.get_by_role("button", name="添加").click()
        expect(triggers.get_by_role("list", name="情绪触发点").get_by_text("连续亏损后加仓", exact=True)).to_be_visible()
        triggers.get_by_role("button", name="删除「连续亏损后加仓」").click()
        expect(toasts.get_by_text("已删除「连续亏损后加仓」")).to_be_visible()
        expect(triggers.get_by_text("连续亏损后加仓")).to_have_count(0)

        lessons = page.get_by_role("region", name="交易教训")
        lessons.get_by_label("新的交易教训").fill("虚构心得：开盘半小时不追高。")
        lessons.get_by_role("button", name="添加").click()
        expect(lessons.get_by_role("list", name="交易教训").get_by_text("虚构心得：开盘半小时不追高。", exact=True)).to_be_visible()
        lessons.get_by_role("button", name=re.compile("^删除教训：虚构心得")).click()
        confirm = page.get_by_role("dialog", name="删除这条教训？")
        confirm.get_by_role("button", name="删除", exact=True).click()
        expect(toasts.get_by_text("已删除这条教训")).to_be_visible()
        expect(lessons.get_by_text("虚构心得：开盘半小时不追高。")).to_have_count(0)

        # Two tabs on one list of lessons 甲 乙 丙: the first deletes 甲, then the second, still showing the old
        # list, deletes 乙. 丙 has moved into 乙's old place: the second tab is refused and told to refresh,
        # and nothing but 甲 is gone.
        triple = ("虚构甲：止损不挪。", "虚构乙：不满仓。", "虚构丙：不追涨停。")
        for text in triple:
            lessons.get_by_label("新的交易教训").fill(text)
            lessons.get_by_role("button", name="添加").click()
            expect(lessons.get_by_role("list", name="交易教训").get_by_text(text, exact=True)).to_be_visible()
        other = browser.new_page(color_scheme=SCHEME, viewport={"width": 1440, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai")
        other.on("pageerror", lambda error: errors.append(str(error)))
        other.goto(f"{url}/settings/preferences")
        other_lessons = other.get_by_role("region", name="交易教训")
        expect(other_lessons.get_by_text(triple[2], exact=True)).to_be_visible(timeout=60000)
        lessons.get_by_role("button", name=re.compile("^删除教训：虚构甲")).click()
        page.get_by_role("dialog", name="删除这条教训？").get_by_role("button", name="删除", exact=True).click()
        expect(lessons.get_by_text(triple[0], exact=True)).to_have_count(0)
        other_lessons.get_by_role("button", name=re.compile("^删除教训：虚构乙")).click()
        other.get_by_role("dialog", name="删除这条教训？").get_by_role("button", name="删除", exact=True).click()
        expect(other.get_by_role("region", name="通知", exact=True).get_by_text("没有删除", exact=False)).to_be_visible()
        other.close()
        page.reload()
        for text in triple[1:]:
            expect(lessons.get_by_text(text, exact=True)).to_be_visible(timeout=20000)
        memory_lessons = [item.get("lesson") for item in json.loads(memory_file.read_text(encoding="utf-8"))["history"]["lessons"]]
        assert [text for text in triple if text in memory_lessons] == list(triple[1:]), memory_lessons
        for text in triple[1:]:
            lessons.get_by_role("button", name=re.compile(f"^删除教训：{text[:3]}")).click()
            page.get_by_role("dialog", name="删除这条教训？").get_by_role("button", name="删除", exact=True).click()
            expect(lessons.get_by_text(text, exact=True)).to_have_count(0)
        results["preferences_round_trip"] = "passed"

        # -- the assistant's proposals: confirm one, reject one, forget a rejected one ------------------
        seed("""
from core import lessons, proposals
lessons.propose({'description': '虚构：追高买入后回撤', 'lesson': '虚构教训：等回踩再买。', 'type': 'failure'})
lessons.propose_profile('risk_tolerance', 'moderate')
old = lessons.propose({'description': '虚构心得：分批建仓', 'type': 'insight'})
proposals.reject(old['id'])
""")
        page.reload()
        pending = page.get_by_role("region", name="待确认的提议")
        rejected = page.get_by_role("region", name="已放弃的提议")
        lesson_item = pending.get_by_role("listitem").filter(has_text="虚构教训：等回踩再买。")
        profile_item = pending.get_by_role("listitem").filter(has_text="风险偏好")
        expect(lesson_item).to_be_visible(timeout=20000)
        expect(profile_item).to_be_visible()
        expect(rejected.get_by_role("listitem").filter(has_text="虚构心得：分批建仓")).to_be_visible()
        if capture:
            full_screenshot(page, capture / "settings-preferences-proposals.png")
        lesson_item.get_by_role("button", name="确认", exact=True).click()
        expect(toasts.get_by_text("已确认：", exact=False)).to_be_visible()
        expect(lessons.get_by_role("list", name="交易教训").get_by_text("虚构教训：等回踩再买。", exact=True)).to_be_visible()
        profile_item.get_by_role("button", name="放弃", exact=True).click()
        expect(rejected.get_by_role("listitem").filter(has_text="风险偏好")).to_be_visible()
        expect(page.get_by_role("region", name="交易画像").get_by_label("风险偏好")).to_have_value("conservative")
        rejected.get_by_role("listitem").filter(has_text="虚构心得：分批建仓").get_by_role("button", name="删除", exact=True).click()
        expect(toasts.get_by_text("已删除这次放弃：助手以后可能再提出这项内容")).to_be_visible()
        expect(rejected.get_by_text("虚构心得：分批建仓")).to_have_count(0)
        expect(pending.get_by_text("没有待确认的提议。")).to_be_visible()
        # A rejected profile suggestion can be forgotten the same way.
        rejected.get_by_role("listitem").filter(has_text="风险偏好").get_by_role("button", name="删除", exact=True).click()
        expect(toasts.get_by_text("已删除这次放弃：助手以后可能再提出这项内容").last).to_be_visible()
        expect(rejected.get_by_text("风险偏好")).to_have_count(0)
        expect(rejected.get_by_text("没有放弃过的提议。")).to_be_visible()
        # Forgotten: the assistant may propose both again.
        seed("""
from core import lessons
assert lessons.propose({'description': '虚构心得：分批建仓', 'type': 'insight'})['status'] == 'pending'
assert lessons.propose_profile('risk_tolerance', 'moderate')['status'] == 'pending'
""")
        results["proposals"] = "passed"

        # -- 自动化与通知: running a routine needs the scheduler, which this backend does not start ---------
        open_section("automation", "自动化与通知", SECTIONS[3][2])
        expect(page.get_by_text("后台调度器未运行", exact=False).first).to_be_visible()
        run = page.get_by_role("button", name=re.compile("^立即运行")).first
        run.hover()
        run.click()
        expect(toasts.get_by_text("没有运行：后台调度器未运行", exact=False)).to_be_visible()
        results["routine_refused"] = "passed"

        # -- 备份与导出: each download starts and is the file it says -------------------------------------
        open_section("backup", "备份与导出", SECTIONS[5][2])
        for label, filename in (("下载账本备份", "ledger-backup.sqlite3"), ("导出流水（JSON）", "transactions.json"),
                                ("下载桌面数据备份", "desk-backup.sqlite3")):
            with page.expect_download() as started:
                page.get_by_role("button", name=label).click()
            download = started.value
            assert download.suggested_filename == filename, download.suggested_filename
            saved = out / "downloads" / filename
            download.save_as(saved)
            expect(toasts.get_by_text(f"已开始下载 {filename}")).to_be_visible()
            if filename.endswith(".sqlite3"):
                assert saved.read_bytes()[:16] == b"SQLite format 3\x00", filename
                with sqlite3.connect(saved) as copy:
                    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    tables = {row[0] for row in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if filename == "desk-backup.sqlite3":
                        assert {"watchlist", "trade_plans", "alert_rules", "proposals"} <= tables, tables
                        assert copy.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] > 0
            else:
                json.loads(saved.read_text(encoding="utf-8"))
        results["downloads"] = "passed"

        # -- 关于与帮助: the theme follows the system until 浅色 or 深色 is chosen, and keeps the choice ------
        theme = "document.documentElement.getAttribute('data-theme')"
        page.evaluate("localStorage.removeItem('pdt.theme')")
        open_section("about", "关于与帮助", SECTIONS[6][2])
        expect(page.get_by_role("region", name="关于").get_by_text("完整模式", exact=False)).to_be_visible()
        expect(page.get_by_role("radio", name="跟随系统")).to_have_attribute("aria-checked", "true")
        expect(page.get_by_role("radiogroup").filter(has=page.get_by_role("radio", name="跟随系统")).get_by_role("radio")) \
            .to_have_text(["跟随系统", "浅色", "深色"])
        # Nothing chosen: the page follows the system, also when it changes while the page is open.
        page.emulate_media(color_scheme="light")
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        page.emulate_media(color_scheme="dark")
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.get_by_role("radio", name="浅色").click()
        assert page.evaluate(theme) == "light"
        page.reload()   # the system is dark: the choice wins
        expect(page.get_by_role("radio", name="浅色")).to_have_attribute("aria-checked", "true", timeout=20000)
        assert page.evaluate(theme) == "light"
        page.get_by_role("radio", name="深色").click()
        assert page.evaluate(theme) == "dark"
        page.emulate_media(color_scheme="light")
        assert page.evaluate(theme) == "dark", "an explicit choice does not follow the system"
        page.get_by_role("radio", name="跟随系统").click()
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        page.evaluate("localStorage.removeItem('pdt.theme')")
        page.emulate_media(color_scheme=SCHEME)
        # No flash of the wrong theme: index.html sets it before any of the app's code runs (held back here).
        for system, saved, expected in (("light", None, "light"), ("dark", None, "dark"), ("light", "dark", "dark"), ("dark", "light", "light")):
            probe = browser.new_page(color_scheme=system, viewport={"width": 1440, "height": 900})
            if saved:
                probe.add_init_script(f"localStorage.setItem('pdt.theme', '{saved}')")
            probe.route("**/src/main.tsx", lambda route: route.abort())
            probe.goto(url + "/settings/about")
            assert probe.evaluate(theme) == expected, (system, saved)
            assert probe.evaluate("document.getElementById('root').childElementCount") == 0, "the app did not run"
            probe.close()
        results["theme"] = "passed"

        # -- the restyled pages: 组合走势, the workbench, the assistant and its history --------------------
        for theme in ("dark", "light"):
            page.evaluate(f"localStorage.setItem('pdt.theme', '{theme}')")
            suffix = "" if theme == "dark" else "-light"
            for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(url + "/tracker")
                expect(page.get_by_role("heading", name="投资组合跟踪")).to_be_visible(timeout=30000)
                expect(page.get_by_label("快照日期")).to_be_visible()
                page.wait_for_timeout(800)   # the charts draw
                no_sideways_scroll(page, f"tracker at {width}")
                if capture:
                    full_screenshot(page, capture / f"tracker-{name}{suffix}.png")
                page.goto(url + "/lab")
                expect(page.get_by_role("heading", name="股票研究工作台", exact=True)).to_be_visible(timeout=30000)
                page.wait_for_timeout(300)
                no_sideways_scroll(page, f"lab at {width}")
                if capture:
                    full_screenshot(page, capture / f"lab-{name}{suffix}.png")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(url + "/")
            page.get_by_role("banner").get_by_role("button", name="助手").click()
            dock = page.get_by_role("complementary", name="助手")
            expect(dock.locator("form input[type=text]")).to_be_visible(timeout=20000)
            if capture:
                page.screenshot(path=str(capture / f"assistant-desktop{suffix}.png"))
            dock.get_by_role("button", name="历史", exact=True).click()
            history = page.get_by_role("dialog", name="对话历史")
            expect(history).to_be_visible()
            page.wait_for_timeout(300)
            if capture:
                page.screenshot(path=str(capture / f"assistant-history{suffix}.png"))
            history.get_by_role("button", name="关闭", exact=True).click()
            expect(history).to_have_count(0)
        page.evaluate("localStorage.removeItem('pdt.theme')")
        results["restyled_pages"] = "passed"
        stop(server)

        # -- demo mode: every save disabled with its reason; downloads still work -------------------------
        server = backend(TRACKER_DEMO_MODE="1")
        page.goto(url + "/settings")
        expect(page).to_have_url(f"{url}/settings/accounts", timeout=30000)
        expect(page.get_by_text("数据为虚构，只读")).to_be_visible(timeout=30000)
        for slug, region in (("accounts", "账户与费率"), ("models", "对话模型")):
            page.goto(f"{url}/settings/{slug}")
            save = page.get_by_role("region", name=region).get_by_role("button", name="保存", exact=True)
            expect(save).to_have_attribute("aria-disabled", "true", timeout=20000)
            reason = page.locator(f"[id='{save.get_attribute('aria-describedby')}']")
            assert reason.text_content() == "演示模式只读", reason.text_content()
            if capture and slug == "accounts":
                full_screenshot(page, capture / "settings-accounts-demo.png")
        page.goto(f"{url}/settings/automation")
        expect(page.get_by_role("button", name=re.compile("^立即运行")).first).to_have_attribute("aria-disabled", "true", timeout=20000)
        page.goto(f"{url}/settings/preferences")
        expect(page.get_by_text("助手的记忆不可用")).to_be_visible(timeout=20000)
        expect(page.get_by_role("region", name="已放弃的提议")).to_be_visible()
        if capture:
            full_screenshot(page, capture / "settings-preferences-demo.png")
        page.goto(f"{url}/settings/backup")
        with page.expect_download() as started:
            page.get_by_role("button", name="下载桌面数据备份").click()
        assert started.value.suggested_filename == "desk-backup.sqlite3"
        results["demo"] = "passed"
        stop(server)

        # -- research-only mode: its three sections; a desk section goes to the first one -------------------
        server = backend(TRACKER_LAB_ONLY="1", app="backend.main:app")
        page.goto(url + "/settings")
        expect(page).to_have_url(f"{url}/settings/models", timeout=30000)
        nav = page.get_by_role("navigation", name="设置分区")
        expect(nav.get_by_role("link")).to_have_count(3)
        assert nav.get_by_role("link").all_inner_texts() == ["模型", "数据与行情", "关于与帮助"]
        # This launcher starts no assistant: the chat model cannot be saved here, and the page says why.
        chat_save = page.get_by_role("region", name="对话模型").get_by_role("button", name="保存", exact=True)
        expect(chat_save).to_have_attribute("aria-disabled", "true", timeout=20000)
        reason = page.locator(f"[id='{chat_save.get_attribute('aria-describedby')}']")
        assert "研究模式" in reason.text_content(), reason.text_content()
        expect(page.get_by_role("region", name="对话模型").get_by_text("研究模式", exact=False).first).to_be_visible()
        page.goto(url + "/settings/backup#x")
        expect(page).to_have_url(f"{url}/settings/models#x", timeout=20000)
        results["lab_mode"] = "passed"

        assert not errors, errors
        browser.close()
    print(json.dumps({**results, "screenshots": str(capture) if capture else None, "page_errors": errors},
                     ensure_ascii=False))
