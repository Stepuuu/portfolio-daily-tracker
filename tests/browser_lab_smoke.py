"""Real offline HTTP + browser + compute journey, with disposable synthetic data."""
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

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


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


def ready(url, process):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Isolated preview exited")
        try:
            opener.open(url, timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("Preview did not become ready")


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="research-browser-")))
    backend_port, frontend_port = port(), port()
    subprocess.run([sys.executable, str(ROOT / "engine/scripts/create_demo.py"), "--output", str(out / "portfolio")], check=True)
    env = {**os.environ, "PORTFOLIO_DIR": str(out / "portfolio"), "TRACKER_CONFIG_FILE": str(out / "config.json"),
           "TRACKER_LAB_DIR": str(out / "lab"), "TRACKER_LAB_ONLY": "1", "TRACKER_DEMO_MODE": "0",
           "PYTHONPATH": str(ROOT / "dashboard"), "BACKEND_PORT": str(backend_port), "FRONTEND_PORT": str(frontend_port)}
    log = stack.enter_context((out / "preview.log").open("w"))
    backend = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(backend_port)],
                               cwd=out, env=env, stdout=log, stderr=log)
    stack.callback(stop, backend)
    ready(f"http://127.0.0.1:{backend_port}/health", backend)
    frontend = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--strictPort"],
                                cwd=ROOT / "dashboard/frontend", env=env, stdout=log, stderr=log)
    stack.callback(stop, frontend)
    ready(f"http://127.0.0.1:{frontend_port}", frontend)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                                              headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{frontend_port}/lab")
        expect(page.get_by_role("heading", name="股票研究工作台", exact=True)).to_be_visible()
        page.get_by_label("研究模板", exact=True).select_option("volatility")
        page.get_by_role("button", name="运行手动实验", exact=True).click()
        expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_role("heading", name="测试期净值与基准", exact=True)).to_be_visible()
        runs = page.request.get(f"http://127.0.0.1:{backend_port}/api/lab/runs").json()
        assert len(runs) == 1 and runs[0]["status"] == "completed"
        identifier = runs[0]["id"]
        result = page.request.get(f"http://127.0.0.1:{backend_port}/api/lab/runs/{identifier}").json()["result"]
        assert result["execution"]["model_calls"] == 0
        assert result["dataset"]["synthetic"]
        exported = page.request.get(f"http://127.0.0.1:{backend_port}/api/lab/runs/{identifier}/export")
        assert exported.status == 200 and identifier in exported.headers["content-disposition"]
        cli = subprocess.run([sys.executable, str(ROOT / "scripts/research.py"), "--url",
                              f"http://127.0.0.1:{backend_port}", "status", identifier],
                             capture_output=True, text=True, check=True, timeout=10)
        assert json.loads(cli.stdout)["data"]["status"] == "completed"
        bridge = subprocess.run([sys.executable, "-m", "core.lab_mcp", "--url", f"http://127.0.0.1:{backend_port}"],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "research_status", "arguments": {"run_id": identifier}}}) + "\n",
            capture_output=True, text=True, check=True, timeout=10, env=env, cwd=out)
        assert json.loads(bridge.stdout)["result"]["isError"] is False
        # Invalid data cannot create a misleading success record.
        rejected = page.request.post(f"http://127.0.0.1:{backend_port}/api/lab/datasets/import", multipart={
            "file": {"name": "bad.csv", "mimeType": "text/csv", "buffer": b"date,symbol,open,high,low,close,volume\n2024-01-02,DEMO,inf,10,9,10,1\n"}})
        assert rejected.status == 422
        capture = os.environ.get("TRACKER_SCREENSHOT_DIR")
        if capture:
            Path(capture).mkdir(parents=True, exist_ok=True)
            page.locator("main").evaluate("node => node.scrollTo(0, 0)")
            page.screenshot(path=str(Path(capture) / "research-lab-desktop.png"), full_page=True)
        page.reload()
        expect(page.get_by_role("heading", name="测试期净值与基准", exact=True)).to_be_visible(timeout=15000)
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("heading", name="股票研究工作台", exact=True)).to_be_visible()
        assert page.evaluate("document.body.scrollWidth") == 390
        if capture:
            page.locator("main").evaluate("node => node.scrollTo(0, 0)")
            page.screenshot(path=str(Path(capture) / "research-lab-mobile.png"), full_page=True)
        assert not errors, errors
        print(json.dumps({"manual_compute": "passed", "cli_mcp": "passed", "reload": "passed", "export": "passed", "invalid_data": "rejected",
                          "mobile_width": 390, "page_errors": errors}))
        browser.close()
