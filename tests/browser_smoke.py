"""Offline browser journey. Install Playwright and its Chromium browser first."""
from contextlib import ExitStack
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def ready(url, process):
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Preview process exited before readiness')
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Preview service did not become ready')


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='tracker-browser-')))
    backend_port, frontend_port = free_port(), free_port()
    subprocess.run([sys.executable, str(root / 'engine/scripts/create_demo.py'), '--output', str(out / 'portfolio')], check=True)
    env = {**os.environ, 'PORTFOLIO_DIR': str(out / 'portfolio'), 'TRACKER_DEMO_MODE': '0',
           'PYTHONPATH': str(root / 'dashboard'), 'BACKEND_PORT': str(backend_port), 'FRONTEND_PORT': str(frontend_port)}
    backend_log = stack.enter_context((out / 'backend.log').open('w'))
    backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'browser_app:app', '--app-dir', str(root / 'tests'),
                                '--host', '127.0.0.1', '--port', str(backend_port)], cwd=out, env=env, stdout=backend_log, stderr=backend_log)
    stack.callback(stop, backend)
    ready(f'http://127.0.0.1:{backend_port}/health', backend)
    frontend_log = stack.enter_context((out / 'frontend.log').open('w'))
    frontend = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--strictPort'],
                               cwd=root / 'dashboard/frontend', env=env, stdout=frontend_log, stderr=frontend_log)
    stack.callback(stop, frontend)
    ready(f'http://127.0.0.1:{frontend_port}', frontend)

    with sync_playwright() as p:
     browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True, args=['--no-sandbox'])
     page=browser.new_page(viewport={'width':1440,'height':1100})
     errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
     page.goto(f'http://127.0.0.1:{frontend_port}/ledger')
     expect(page.get_by_role('heading',name='交易账本',exact=True)).to_be_visible()
     page.get_by_label('期初日期',exact=True).fill('2026-09-01')
     page.get_by_role('button',name='预览已有持仓',exact=True).click()
     expect(page.get_by_text('2 笔待入账',exact=False)).to_be_visible()
     assert page.request.get(f'http://127.0.0.1:{backend_port}/api/ledger').json()['revision']==0
     page.get_by_role('button',name='确认入账',exact=True).click()
     expect(page.get_by_role('status')).to_contain_text('已确认 2 笔')
     page.get_by_label('类型',exact=True).select_option('buy')
     page.get_by_label('账户',exact=True).select_option('长期账户')
     page.get_by_label('发生日期',exact=True).fill('2026-09-02')
     page.get_by_label('交易所:股票代码',exact=True).fill('NASDAQ:EXAMPLE')
     page.get_by_label('币种',exact=True).select_option('USD')
     page.get_by_label('成交数量（支持碎股）',exact=True).fill('0.5')
     page.get_by_label('成交价（原币）',exact=True).fill('100')
     page.get_by_label('手续费（同币种）',exact=True).fill('1')
     page.get_by_label('交易理由或备注',exact=False).fill('虚构交易：验证手续费与碎股')
     page.get_by_role('button',name='预览变化',exact=True).click()
     expect(page.get_by_text('1 笔待入账',exact=False)).to_be_visible()
     expect(page.get_by_label('确认预览')).to_contain_text('949')
     page.screenshot(path=str(out/'public-ledger-desktop.png'),full_page=True)
     page.get_by_role('button',name='确认入账',exact=True).click()
     expect(page.get_by_role('status')).to_contain_text('已确认 1 笔')
     assert page.request.get(f'http://127.0.0.1:{backend_port}/api/ledger').json()['accounts']['长期账户']['cash_balances']['USD']=='949'
     content='external_id,date,kind,account,currency,amount,note\nexample-001,2026-09-03,deposit,长期账户,CNY,50,虚构入金\n'
     for duplicate in [False,True]:
      page.get_by_label('导入 CSV',exact=True).set_input_files({'name':'example.csv','mimeType':'text/csv','buffer':content.encode()})
      expect(page.get_by_text('跳过 1 笔重复记录' if duplicate else '1 笔待入账',exact=False)).to_be_visible()
      page.get_by_role('button',name='确认入账',exact=True).click()
      expect(page.get_by_role('status')).to_contain_text('已确认 0 笔' if duplicate else '已确认 1 笔')
     page.get_by_role('tab',name='流水与持仓').click()
     expect(page.get_by_text('NASDAQ:EXAMPLE',exact=True)).to_have_count(1)
     page.get_by_role('button',name='预览冲销').first.click()
     expect(page.get_by_text('1 笔待入账',exact=False)).to_be_visible()
     page.get_by_role('button',name='确认入账',exact=True).click()
     expect(page.get_by_role('status')).to_contain_text('已确认 1 笔')
     page.get_by_role('tab',name='研究复盘').click()
     page.get_by_label('标的',exact=True).fill('NASDAQ:EXAMPLE')
     page.get_by_label('投资逻辑 / 本次复盘结论').fill('虚构研究示例：检验产品需求是否持续。')
     page.get_by_label('什么情况说明判断失效？').fill('虚构条件：后续证据不支持原假设。')
     page.get_by_label('计划复查日期').fill('2026-09-30')
     page.get_by_role('button',name='保存研究记录').click()
     expect(page.get_by_role('status')).to_contain_text('研究记录已保存')
     page.set_viewport_size({'width':390,'height':844})
     page.screenshot(path=str(out/'public-ledger-mobile.png'),full_page=True)
     assert page.evaluate('document.body.scrollWidth')==390
     page.goto(f'http://127.0.0.1:{frontend_port}/portfolio')
     expect(page).to_have_url(f'http://127.0.0.1:{frontend_port}/ledger')
     page.goto(f'http://127.0.0.1:{frontend_port}/ledger')
     expect(page.get_by_role('heading',name='交易账本',exact=True)).to_be_visible()
     page.get_by_role('tab',name='流水与持仓').click()
     expect(page.get_by_text('已冲销',exact=True)).to_be_visible()
     page.screenshot(path=str(out/'ledger-reloaded-mobile.png'),full_page=True)
     assert page.evaluate('document.body.scrollWidth')==390
     assert not errors,errors
     print(json.dumps({'desktop':'passed','mobile_width':390,'migration':'passed','trade':'passed','csv_dedup':'passed','reversal':'passed','journal':'passed','legacy_redirect':'passed','reload':'passed','page_errors':errors}))
     browser.close()
