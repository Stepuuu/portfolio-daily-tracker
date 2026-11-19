"""UI kit gallery (/__ui) in a real browser: both themes, dialog focus, sticky header, phone width.

The gallery makes no API calls, so only the Vite dev server runs (the route exists in development builds).
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
# The tokens' --bg and --up in each theme (src/styles/tokens.css).
BACKGROUND = {"dark": "rgb(15, 18, 23)", "light": "rgb(246, 247, 249)"}
UP = {"dark": "rgb(249, 112, 102)", "light": "rgb(184, 36, 26)"}


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
            raise RuntimeError("Vite exited")
        try:
            opener.open(url, timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("Vite did not become ready")


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="ui-browser-")))
    frontend_port = port()
    env = {**os.environ, "FRONTEND_PORT": str(frontend_port), "BACKEND_PORT": str(port())}
    log = stack.enter_context((out / "vite.log").open("w"))
    frontend = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--strictPort"],
                                cwd=ROOT / "dashboard/frontend", env=env, stdout=log, stderr=log)
    stack.callback(stop, frontend)
    ready(f"http://127.0.0.1:{frontend_port}", frontend)
    capture = os.environ.get("TRACKER_SCREENSHOT_DIR")
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                                              headless=True, args=["--no-sandbox"])
        for theme in ("dark", "light"):
            page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(f"console: {message.text}") if message.type == "error" else None)
            page.goto(f"http://127.0.0.1:{frontend_port}/__ui?theme={theme}")
            expect(page.get_by_role("heading", name="组件库", exact=True)).to_be_visible(timeout=60000)

            # The theme comes from data-theme and the tokens follow it.
            assert page.evaluate("document.documentElement.dataset.theme") == theme
            assert page.locator("main").evaluate("node => getComputedStyle(node.parentElement).backgroundColor") == BACKGROUND[theme]
            assert page.locator("main .text-up").first.evaluate("node => getComputedStyle(node).color") == UP[theme]

            # Dialog: focus moves inside, Esc closes it, focus returns to the button that opened it.
            trigger = page.get_by_role("button", name="在对话框中打开", exact=True)
            trigger.click()
            dialog = page.get_by_role("dialog", name="记一笔")
            expect(dialog).to_be_visible()
            assert page.evaluate("document.activeElement.closest('dialog') !== null"), "focus is inside the dialog"
            page.keyboard.press("Escape")
            expect(dialog).to_be_hidden()
            assert trigger.evaluate("node => node === document.activeElement"), "focus returned to the trigger"
            assert page.evaluate("document.documentElement.style.overflow") == "", "page scroll unlocked"

            # A label points at its control even when the control brings its own id (directly or nested):
            # clicking the label focuses it and the control is named by it.
            controls = page.locator("#controls")
            for label, control_id in (("外部编号", "gallery-external-id"), ("委托编号", "gallery-nested-id"),
                                      ("成交编号", "gallery-wrapped-id")):
                named = controls.get_by_label(label, exact=True)
                expect(named).to_have_attribute("id", control_id)
                controls.locator("label", has_text=label).click()
                assert page.evaluate("document.activeElement.id") == control_id, label
            # A custom wrapper with its own id around a control without one: the label still reaches the
            # control inside, and the wrapper's id is not handed to it as well (ids stay unique).
            controls.locator("label", has_text="合同编号").click()
            assert page.evaluate("document.activeElement.tagName === 'INPUT' && !!document.activeElement.closest('#gallery-row-b')"), \
                "the label focuses the input inside the wrapper"
            assert page.evaluate("document.querySelectorAll('[id=\"gallery-row-b\"]').length") == 1
            expect(controls.get_by_label("合同编号", exact=True)).to_have_attribute("placeholder", "选填")

            # A number field reads one convention: a pasted decimal comma is refused with the reason, and the
            # value stays what it was (never 123); a thousands separator in its place is accepted.
            form = page.locator("#form")
            price = form.get_by_label("价格")   # the label reads "价格*" (required)
            expect(price).to_have_value("168.20")
            price.fill("1,23")
            expect(price).to_have_value("168.20")
            expect(price).to_have_attribute("aria-invalid", "true")
            expect(form.get_by_role("alert").filter(has_text="未接受“1,23”")).to_be_visible()
            price.fill("1,234.5")
            expect(price).to_have_value("1234.5")
            expect(form.get_by_text("未接受“1,23”", exact=False)).to_have_count(0)

            # A disabled button explains why, and stays focusable.
            blocked = page.get_by_role("button", name="确认入账", exact=True).first
            assert blocked.get_attribute("aria-disabled") == "true"
            reason = page.locator(f"[id='{blocked.get_attribute('aria-describedby')}']")
            assert reason.text_content() == "演示模式只读"

            # Toasts land in the polite live region.
            page.get_by_role("button", name="成功提示", exact=True).click()
            expect(page.get_by_role("region", name="通知").get_by_text("已入账 1 笔 · 版本 12")).to_be_visible()

            # Sticky header: scroll the ledger table's top out of view; its header stays under the 48 px bar.
            table = page.get_by_role("table", name="账本流水")
            top = table.evaluate("node => node.getBoundingClientRect().top + window.scrollY")
            page.evaluate("y => window.scrollTo(0, y)", top + 300)
            page.wait_for_timeout(100)
            assert table.evaluate("node => node.getBoundingClientRect().top") < 0, "table top scrolled away"
            header_top = table.locator("thead th").first.evaluate("node => node.getBoundingClientRect().top")
            assert abs(header_top - 48) <= 1, header_top
            # Numeric columns are right-aligned with tabular figures.
            amount = table.locator("tbody td:nth-child(7)").first
            assert amount.evaluate("node => getComputedStyle(node).textAlign") == "right"
            assert "tabular-nums" in amount.evaluate("node => getComputedStyle(node).fontVariantNumeric")
            page.evaluate("window.scrollTo(0, 0)")
            if capture:
                Path(capture).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(capture) / f"ui-gallery-{theme}.png"), full_page=True)

            # Phone width: nothing overflows sideways and holdings become cards.
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            widths = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth]")
            assert max(widths) <= 390, widths
            expect(page.get_by_role("list", name="持仓")).to_be_visible()
            if capture:
                page.screenshot(path=str(Path(capture) / f"ui-gallery-{theme}-mobile.png"), full_page=True)

            assert not errors, errors
            results[theme] = "passed"
            page.close()
        browser.close()
    print(json.dumps({"themes": results, "dialog_focus": "returned", "sticky_header": "passed", "mobile_width": 390,
                      "page_errors": []}))
