"""账本 › 导入 (/ledger/import) in a real browser, with the synthetic broker files of ``tests/fixtures/broker``.

A fictional ledger account 示例账户 with the opening the fixtures reconcile with (``tests/importer_support``:
500,000.00 CNY, 1,000 SHA:600036, 2,000 SHE:000001). Journey: Today's empty state links here; a clean 同花顺
交割单 end to end (upload → 核对列 recognises it, unverified preset → preview with counts → 对账 一致 →
生成入账预览 → the shared review → 确认入账 → summary → the events on 流水); the same file again is all
已导入 with nothing to propose; two 成交价格 columns block 核对列 until one is chosen; a file of two 资金账号
needs the account choice and says how many rows stay 需处理 and why; a discarded import says so when its link
is opened again; the system's template: a row that needs its fee gets the fee field, one that cannot be fixed on
the page offers only 跳过 with the backend's reason; a 资金对账单 with 需处理 rows: an edit the backend refuses (price 0, 422) and one it accepts,
a reload in the middle restores the session, the 对账 mismatch must be acknowledged before proposing, and a
proposal withdrawn by a change elsewhere is refused at 确认入账 and sends the page back to the preview; 390 px
without sideways scrolling; the read-only demo uploads nothing. Every parse, read and PATCH asks for the counts plus
one page of rows (never every row); a price worked out as 成交金额 ÷ 数量 reads ≈ to 8 places with its full digits on
hover and says how it was worked out. Set TRACKER_SCREENSHOT_DIR to keep screenshots.
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

ROOT = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = 'light' if os.environ.get('TRACKER_SCREENSHOT_THEME') == 'light' else 'dark'
FIXTURES = ROOT / 'tests' / 'fixtures' / 'broker'
NOW = '2026-11-17T10:32:05+08:00'   # a Tuesday morning; every fixture date is earlier
ACCOUNT = '示例账户'
OPENING = {'kind': 'opening', 'date': '2026-11-01', 'account': ACCOUNT, 'cash_balances': {'CNY': '500000.00'},
           'contributed_cny': '551000',
           'positions': [{'ticker': 'SHA:600036', 'name': '招商银行', 'quantity': '1000', 'cost_price': '30'},
                         {'ticker': 'SHE:000001', 'name': '平安银行', 'quantity': '2000', 'cost_price': '10.5'}]}


def port():
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


def ready(url, process, seconds=120):
    # A deadline, not a number of tries: on a loaded machine the backend's imports alone can take tens of seconds.
    deadline = time.monotonic() + seconds
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'{url}: process exited')
        try:
            opener.open(url, timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError(f'{url} did not become ready')


def csv(header, *rows):
    return ('\n'.join([','.join(header)] + [','.join(str(c) for c in row) for row in rows]) + '\n').encode('utf-8')


def full_screenshot(page, path):
    """The page scrolls inside <main>: grow the viewport to the content for a whole-page picture."""
    size = page.viewport_size
    extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
    page.set_viewport_size({'width': size['width'], 'height': size['height'] + max(0, extra)})
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))
    page.set_viewport_size(size)


def no_sideways_scroll(page, width):
    page.wait_for_timeout(300)
    widths = page.evaluate('[document.documentElement.scrollWidth, document.body.scrollWidth]')
    assert max(widths) <= width, widths
    main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
    assert main[0] <= main[1], main


def label(scope, name):
    """The field labelled ``name``; a required field's label ends with an asterisk the screen reader skips."""
    return scope.get_by_label(re.compile('^' + re.escape(name) + r'\s*\*?$'))


DUPLICATE = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交价格', '手续费']
ACCOUNTS = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交金额', '手续费', '资金账号', '股东代码']

with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='import-browser-')))
    portfolio = out / 'portfolio'
    portfolio.mkdir()
    (portfolio / 'fake_quotes.json').write_text(json.dumps({'now': NOW, 'quotes': {}, 'fx': {}}), encoding='utf-8')
    backend_port, frontend_port = port(), port()
    base_env = {**os.environ, 'PORTFOLIO_DIR': str(portfolio), 'TRACKER_CONFIG_FILE': str(out / 'config.json'),
                'TRACKER_LAB_DIR': str(out / 'lab'), 'TRACKER_QUOTE_SOURCE': 'fake', 'TRACKER_DEMO_MODE': '0',
                'TRACKER_LAB_ONLY': '0', 'PYTHONPATH': str(ROOT / 'dashboard'),
                'BACKEND_PORT': str(backend_port), 'FRONTEND_PORT': str(frontend_port)}
    log = stack.enter_context((out / 'preview.log').open('w'))
    capture = Path(os.environ['TRACKER_SCREENSHOT_DIR']) if os.environ.get('TRACKER_SCREENSHOT_DIR') else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)

    def backend(**overrides):
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'browser_app:app', '--app-dir', str(ROOT / 'tests'),
                                    '--host', '127.0.0.1', '--port', str(backend_port)],
                                   cwd=out, env={**base_env, **overrides}, stdout=log, stderr=log)
        stack.callback(stop, process)
        ready(f'http://127.0.0.1:{backend_port}/health', process)
        return process

    server = backend()
    frontend = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--strictPort'],
                                cwd=ROOT / 'dashboard/frontend', env=base_env, stdout=log, stderr=log)
    stack.callback(stop, frontend)
    ready(f'http://127.0.0.1:{frontend_port}', frontend)
    url = f'http://127.0.0.1:{frontend_port}'
    api = f'http://127.0.0.1:{backend_port}/api'
    results = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
                                             headless=True, args=['--no-sandbox'])
        page = browser.new_page(color_scheme=SCHEME, viewport={'width': 1440, 'height': 900}, locale='zh-CN', timezone_id='Asia/Shanghai')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        uploads = []
        page.on('request', lambda request: uploads.append(request.url) if '/api/imports/parse' in request.url else None)
        session_requests = []   # every parse, GET and PATCH of a session: (method, url)
        page.on('request', lambda request: session_requests.append((request.method, request.url))
                if re.search(r'/api/imports/(parse|[0-9a-f]{32})(\?|$)', request.url) else None)
        toasts = page.get_by_role('region', name='通知')

        def shot(name, full=False):
            if capture:
                page.wait_for_timeout(250)
                (full_screenshot(page, capture / name) if full else page.screenshot(path=str(capture / name)))

        def upload(name, data):
            page.get_by_label('选择券商文件').set_input_files({'name': name, 'mimeType': 'application/octet-stream', 'buffer': data})
            expect(page.get_by_role('region', name='核对列')).to_be_visible(timeout=60000)
            return re.search(r'session=([0-9a-f]{32})', page.url).group(1)

        def ledger():
            return page.request.get(f'{api}/ledger').json()

        def continue_to_preview():
            page.get_by_role('button', name='继续：预览与去重').click()
            expect(page.get_by_role('region', name='预览与去重')).to_be_visible()

        def tab(name):
            return page.get_by_role('tab', name=re.compile('^' + re.escape(name)))

        def propose_button():
            return page.get_by_role('button', name=re.compile('^生成入账预览'))

        try:
            # -- Today without an account links here; the 账本 tabs list 导入 --------------------------------
            page.goto(url + '/')
            link = page.get_by_role('link', name='导入券商文件')
            # A first visit with no opening balance is sent to the setup wizard once per browser session (as
            # soon as the setup status is read, which can be after Today has drawn); back on Today, the empty
            # state links here.
            expect(page).to_have_url(url + '/setup', timeout=60000)
            page.goto(url + '/')
            expect(link).to_be_visible(timeout=60000)
            page.wait_for_timeout(1000)
            expect(page).to_have_url(url + '/')
            shot('import-today-empty.png')
            link.click()
            expect(page).to_have_url(url + '/ledger/import')
            steps = page.get_by_role('navigation', name='导入步骤')
            expect(steps).to_contain_text('选择文件', timeout=60000)   # the dev server compiles the page on first use
            expect(page.get_by_role('navigation', name='账本').get_by_role('link')).to_have_text(['流水', '持仓与成本', '导入'])
            expect(steps.locator('[aria-current="step"]')).to_contain_text('选择文件')
            results['entry_points'] = 'passed'

            # The opening the fixtures reconcile with, booked like any ledger change.
            opening = page.request.post(f'{api}/ledger/proposals', data={'events': [OPENING]}).json()
            assert page.request.post(f"{api}/ledger/proposals/{opening['id']}/confirm").ok

            # -- a clean 同花顺交割单, end to end ------------------------------------------------------------
            page.goto(url + '/ledger/import')
            expect(label(page, '导入到账户')).to_have_value(ACCOUNT, timeout=30000)
            shot('import-upload.png')
            session = upload('交割单.xls', (FIXTURES / 'ths_trades_gbk.xls').read_bytes())
            assert f'session={session}' in page.url and 'step=2' in page.url
            mapping = page.get_by_role('region', name='核对列')
            expect(mapping).to_contain_text('同花顺交割单')
            expect(mapping).to_contain_text('预设未经你的券商文件验证')
            expect(mapping).to_contain_text('编码 GBK')
            expect(mapping).to_contain_text('分隔符 制表符')
            expect(mapping).to_contain_text('9 行')
            expect(mapping).to_contain_text('2026-11-03 至 2026-11-12')
            expect(mapping.get_by_role('list', name='列对应')).to_contain_text('成交日期')
            expect(page.get_by_role('button', name='继续：预览与去重')).to_be_enabled()
            shot('import-mapping.png', full=True)
            continue_to_preview()
            expect(tab('全部')).to_contain_text('9')
            expect(tab('新')).to_contain_text('5')
            expect(tab('跳过')).to_contain_text('3')
            rows = page.get_by_role('table', name='导入预览')
            buy = rows.locator('tbody tr').filter(has_text='SHA:600519').first
            expect(buy).to_contain_text('贵州茅台')
            expect(buy).to_contain_text('1,680.00')
            expect(buy).to_contain_text('-168,043.68')
            # A price worked out as 成交金额 ÷ 数量 (9,605.00 ÷ 300): ≈ to 8 places on the page, every digit it will be
            # booked with on hover and for screen readers, and how it was worked out under it.
            derived = rows.locator('[title="32.016666666666666666666666667"]')
            expect(derived).to_have_count(1)
            expect(derived.locator('[aria-hidden="true"]')).to_have_text('≈32.01666667')
            expect(derived.locator('.sr-only')).to_have_text('约 32.01666667，完整值 32.016666666666666666666666667')
            expect(rows.locator('tbody tr').filter(has=page.locator('[title="32.016666666666666666666666667"]'))) \
                .to_contain_text('成交金额 ÷ 数量')
            expect(page.get_by_label('跳过的行')).to_contain_text('撤单 / 废单')
            recon = page.get_by_role('region', name='对账')
            expect(recon).to_contain_text('一致')
            expect(recon).not_to_contain_text('不一致')
            expect(recon.get_by_role('checkbox')).to_have_count(0)
            shot('import-preview.png', full=True)
            expect(propose_button()).to_have_text('生成入账预览（5 笔 · 第 1/1 批）')
            assert ledger()['revision'] == 1, 'nothing is booked before 确认入账'
            propose_button().click()
            review = page.get_by_role('region', name='核对入账')
            expect(review).to_contain_text('5 笔待入账')
            assert 'step=4' in page.url
            assert len(ledger()['pending']) == 1 and ledger()['revision'] == 1
            shot('import-review.png', full=True)
            page.get_by_role('button', name='确认入账', exact=True).click()
            expect(toasts).to_contain_text('已入账 5 笔')
            done = page.get_by_role('region', name='导入结果')
            expect(done).to_contain_text('这次导入共入账 5 笔', timeout=20000)
            shot('import-done.png', full=True)
            account = ledger()['accounts'][ACCOUNT]
            assert account['positions']['SHA:600519']['quantity'] == '50', account['positions']
            done.get_by_role('link', name='查看流水').click()
            expect(page).to_have_url(url + '/ledger')
            events = page.get_by_role('table', name='账本流水')
            expect(events).to_contain_text('贵州茅台')
            expect(events).to_contain_text('交割单导入')
            expect(page.get_by_role('button', name=re.compile('CSV 导入'))).to_have_count(0)
            results['clean_file'] = 'passed'

            # -- the same file again: everything 已导入, nothing to propose ------------------------------------
            page.get_by_role('main').get_by_role('link', name='导入').last.click()
            expect(page).to_have_url(url + '/ledger/import')
            upload('再次.xls', (FIXTURES / 'ths_trades_gbk.xls').read_bytes())
            continue_to_preview()
            expect(page.get_by_text('全部已导入', exact=True)).to_be_visible()
            expect(tab('已导入')).to_contain_text('5')
            expect(tab('新')).to_contain_text('0')
            expect(propose_button()).to_be_disabled()
            assert ledger()['pending'] == []
            shot('import-reimport.png')
            results['reimport'] = 'passed'

            # -- two 成交价格 columns that differ: 核对列 is blocked until one is chosen -------------------------
            page.goto(url + '/ledger/import')
            conflict_session = upload('duplicate.csv', csv(DUPLICATE, ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '11.00', '5.00']))
            mapping = page.get_by_role('region', name='核对列')
            expect(mapping).to_contain_text('继续之前需要处理 1 项')
            expect(page.get_by_role('button', name='继续：预览与去重')).to_be_disabled()
            group = page.get_by_role('group', name='列冲突：成交价格')
            expect(group).to_contain_text('第 2 行：10.00')
            expect(group).to_contain_text('第 2 行：11.00')
            shot('import-conflict.png', full=True)
            group.get_by_role('radio', name=re.compile('^第 7 列')).check()
            group.get_by_role('button', name='用这一列').click()
            expect(group).to_contain_text('已确认')
            expect(page.get_by_role('button', name='继续：预览与去重')).to_be_enabled()
            continue_to_preview()
            expect(page.get_by_role('table', name='导入预览')).to_contain_text('11.00')
            # Discard it; its link then says the import is gone and offers to start again.
            page.get_by_role('button', name='放弃这次导入').click()
            page.get_by_role('dialog', name='放弃这次导入？').get_by_role('button', name='放弃导入').click()
            expect(toasts).to_contain_text('已放弃这次导入')
            expect(page.get_by_label('选择券商文件')).to_have_count(1)
            page.goto(url + f'/ledger/import?session={conflict_session}&step=3')
            expect(page.get_by_text('这次导入已过期或已删除')).to_be_visible(timeout=20000)
            shot('import-gone.png')
            page.get_by_role('button', name='重新开始').click()
            expect(page).to_have_url(url + '/ledger/import')
            results['column_conflict'] = 'passed'
            results['deleted_session'] = 'passed'

            # -- two 资金账号: confirm which belong to the account; the rest stay 需处理, with the reason --------
            upload('accounts.csv', csv(ACCOUNTS,
                                       ['2026-11-03', '600519', '贵州茅台', '证券买入', 100, '10.00', '1000.00', '5.00', '111', 'A000000001'],
                                       ['2026-11-04', '600036', '招商银行', '证券买入', 100, '30.00', '3000.00', '5.00', '111', 'A000000001'],
                                       ['2026-11-05', '600000', '浦发银行', '证券买入', 100, '8.00', '800.00', '5.00', '222', 'A000000002']))
            identity = page.get_by_role('region', name='账户识别')
            expect(identity).to_contain_text('还没有确认')
            expect(page.get_by_role('button', name='继续：预览与去重')).to_be_disabled()
            expect(identity).to_contain_text('1 行不属于所选账号')
            expect(identity).to_contain_text('资金账号 222')
            shot('import-accounts.png', full=True)
            for value, on in (('111', True), ('A000000001', True), ('222', False), ('A000000002', False)):
                expect(identity.get_by_role('checkbox', name=re.compile('^' + value + ' '))).to_be_checked(checked=on)
            identity.get_by_role('button', name=re.compile('^确认：勾选的属于')).click()
            expect(identity).to_contain_text('你已确认')
            expect(page.get_by_role('button', name='继续：预览与去重')).to_be_enabled()
            continue_to_preview()
            expect(tab('需处理')).to_contain_text('1')
            expect(tab('新')).to_contain_text('1')
            # Same day, listing and quantity as the 交割单's 贵州茅台 buy, without 成交编号: unchecked until looked at.
            expect(tab('可能重复')).to_contain_text('1')
            duplicate = page.get_by_role('table', name='导入预览').locator('tbody tr').filter(has_text='可能重复').first
            expect(duplicate.get_by_role('checkbox')).not_to_be_checked()
            expect(page.get_by_role('table', name='导入预览')).to_contain_text('已有记录（交割单导入）：2026-11-03 买入 贵州茅台 SHA:600519 100 股')
            page.get_by_role('button', name='放弃这次导入').click()
            page.get_by_role('dialog', name='放弃这次导入？').get_by_role('button', name='放弃导入').click()
            expect(toasts).to_contain_text('已放弃这次导入')
            # A later file holding only the identity excluded for this account: remembered, so it needs a choice.
            upload('accounts-222.csv', csv(ACCOUNTS, ['2026-11-06', '600000', '浦发银行', '证券买入', 100, '8.00', '800.00', '5.00', '222', 'A000000002']))
            identity = page.get_by_role('region', name='账户识别')
            remembered = identity.get_by_role('list', name='记住的账号')
            expect(remembered).to_contain_text('资金账号 222（不属于这个账户）')
            expect(remembered).to_contain_text('资金账号 111（属于这个账户）')
            expect(identity).to_contain_text('1 行不属于所选账号')
            expect(page.get_by_role('region', name='核对列')).to_contain_text('请在账户识别里确认')
            expect(page.get_by_role('button', name='继续：预览与去重')).to_be_disabled()
            expect(identity.get_by_role('button', name=re.compile('^确认：勾选的属于'))).to_be_disabled()
            shot('import-accounts-remembered.png', full=True)
            page.get_by_role('button', name='放弃这次导入').click()
            page.get_by_role('dialog', name='放弃这次导入？').get_by_role('button', name='放弃导入').click()
            expect(toasts.get_by_text('已放弃这次导入', exact=False).last).to_be_visible()
            results['account_identity'] = 'passed'

            # -- the system's template: the fee field for a row without its fee; 跳过 only for one not fixable here ----
            template = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee', 'amount']
            upload('模板.csv', csv(template, ['tpl-1', '2026-11-14', 'buy', ACCOUNT, 'SHA:600036', 'CNY', 100, '31.00', '', ''],
                                   ['tpl-2', '2026-11-14', 'deposit', '其他账户', '', 'CNY', '', '', '', '1000']))
            continue_to_preview()
            expect(tab('需处理')).to_contain_text('2')
            tab('需处理').click()
            rows = page.get_by_role('table', name='导入预览')
            expect(rows).to_contain_text('这一行无法在这里修改')
            expect(page.get_by_role('button', name='跳过第 3 行')).to_have_count(1)
            expect(page.get_by_role('button', name='处理第 3 行')).to_have_count(0)
            page.get_by_role('button', name='处理第 2 行').click()
            dialog = page.get_by_role('dialog', name='处理第 2 行')
            expect(label(dialog, '费用')).to_have_count(1)
            for field in ('类型', '代码', '数量', '价格'):
                expect(label(dialog, field)).to_have_count(0)
            expect(dialog).to_contain_text('SHA:600036')   # the template row's cells, under the template's field names
            expect(dialog).to_contain_text('买入')
            shot('import-template-fee.png')
            label(dialog, '费用').fill('5')
            dialog.get_by_role('button', name='保存修改').click()
            expect(toasts).to_contain_text('已保存第 2 行的修改')
            expect(tab('需处理')).to_contain_text('1')
            expect(tab('新')).to_contain_text('1')
            page.get_by_role('button', name='跳过第 3 行').click()
            expect(tab('需处理')).to_contain_text('0')
            page.get_by_role('button', name='放弃这次导入').click()
            page.get_by_role('dialog', name='放弃这次导入？').get_by_role('button', name='放弃导入').click()
            expect(toasts.get_by_text('已放弃这次导入', exact=False).last).to_be_visible()
            results['template_rows'] = {'fee': 'field offered and saved', 'not_here': 'only 跳过'}

            # -- a 资金对账单 with rows to handle: edits, a reload, 对账 to acknowledge, a withdrawn proposal ------
            statement = upload('对账单.csv', (FIXTURES / 'eastmoney_statement_bom.csv').read_bytes())
            continue_to_preview()
            expect(tab('需处理')).to_contain_text('5')   # 送股 after the 交割单's later trades included
            tab('需处理').click()
            expect(page).to_have_url(re.compile('status=review'))
            rows = page.get_by_role('table', name='导入预览')
            ipo = rows.locator('tbody tr').filter(has=page.get_by_role('button', name='处理第 9 行'))
            expect(ipo).to_contain_text('待填写')
            page.get_by_role('button', name='处理第 9 行').click()
            dialog = page.get_by_role('dialog', name='处理第 9 行')
            expect(dialog).to_contain_text('中签缴款')
            for field in ('类型', '代码', '价格', '费用'):
                expect(label(dialog, field)).to_have_count(1)
            expect(label(dialog, '数量')).to_have_count(0)   # only what the backend lists in needs
            label(dialog, '价格').fill('0')
            dialog.get_by_role('button', name='保存修改').click()
            expect(dialog).to_contain_text('没有保存这项修改')
            expect(dialog).to_contain_text('价格必须大于 0')
            shot('import-edit-refused.png')
            dialog.get_by_role('button', name='取消').click()
            expect(dialog).to_have_count(0)
            page.get_by_role('button', name='处理第 13 行').click()
            dialog = page.get_by_role('dialog', name='处理第 13 行')
            label(dialog, '类型').select_option('interest')
            dialog.get_by_role('button', name='保存修改').click()
            expect(toasts).to_contain_text('已保存第 13 行的修改')
            expect(tab('需处理')).to_contain_text('4')
            # A reload in the middle: the session, the filter and the edit come back.
            page.reload()
            expect(page.get_by_role('region', name='预览与去重')).to_be_visible(timeout=30000)
            assert f'session={statement}' in page.url and 'status=review' in page.url
            expect(tab('需处理')).to_contain_text('4')
            tab('新').click()
            edited = page.get_by_role('table', name='导入预览').locator('tbody tr').filter(has=page.get_by_role('checkbox', name='第 13 行入账'))
            expect(edited).to_contain_text('利息')
            expect(edited).to_contain_text('+0.50')
            tab('全部').click()
            recon = page.get_by_role('region', name='对账')
            expect(recon).to_contain_text('不一致')
            expect(recon).to_contain_text('差 CNY')
            acknowledge = recon.get_by_role('checkbox', name=re.compile('^我已核对这些差异'))
            expect(propose_button()).to_be_disabled()
            shot('import-mismatch.png', full=True)
            acknowledge.check()
            expect(propose_button()).to_be_enabled()
            propose_button().click()
            expect(page.get_by_role('region', name='核对入账')).to_contain_text('笔待入账')
            # Changed elsewhere after the preview: the backend withdraws it, so 确认入账 is refused.
            assert page.request.patch(f'{api}/imports/{statement}', data={'rows': [{'row': 2, 'include': False}]}).ok
            page.get_by_role('button', name='确认入账', exact=True).click()
            expect(page.get_by_text('没有入账：这份入账预览已作废')).to_be_visible(timeout=20000)
            expect(page.get_by_role('region', name='预览与去重')).to_be_visible()
            shot('import-withdrawn.png')
            revision = ledger()['revision']
            recon = page.get_by_role('region', name='对账')
            recon.get_by_role('checkbox', name=re.compile('^我已核对这些差异')).check()
            propose_button().click()
            page.get_by_role('button', name='确认入账', exact=True).click()
            expect(toasts.get_by_text(re.compile('^已入账 \\d+ 笔')).last).to_be_visible()
            expect(page.get_by_role('region', name='导入结果')).to_be_visible(timeout=20000)
            assert ledger()['revision'] == revision + 1
            results['row_edits'] = {'refused': '价格必须大于 0', 'accepted': 'row 13 → 利息'}
            results['reload'] = 'passed'
            results['reconciliation_ack'] = 'passed'
            results['confirm_refused'] = 'passed'

            # -- 390 px: upload, 核对列 and the preview as cards, nothing scrolls sideways ---------------------
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(url + '/ledger/import')
            expect(page.get_by_label('选择券商文件')).to_have_count(1, timeout=20000)
            no_sideways_scroll(page, 390)
            shot('import-mobile-upload.png', full=True)
            page.goto(url + f'/ledger/import?session={statement}&step=2')
            expect(page.get_by_role('region', name='核对列')).to_be_visible(timeout=20000)
            no_sideways_scroll(page, 390)
            shot('import-mobile-mapping.png', full=True)
            page.goto(url + f'/ledger/import?session={statement}&step=3')
            expect(page.get_by_role('list', name='导入预览')).to_be_visible(timeout=20000)
            no_sideways_scroll(page, 390)
            shot('import-mobile-preview.png', full=True)
            results['mobile_width'] = 390
            # Never every row: parse, GET and PATCH all asked for one page (the account choice's count is the backend's).
            assert session_requests, 'requests were seen'
            for method, address in session_requests:
                if method in ('POST', 'GET', 'PATCH') and '/propose' not in address:
                    assert re.search(r'[?&]limit=50(&|$)', address), (method, address)
            assert any(method == 'PATCH' for method, _ in session_requests) and any('/parse?' in a for _, a in session_requests)
            results['rows_window'] = len(session_requests)
            # 1100 px: the sidebar stays, the preview table and 核对列 still fit without a sideways page scroll.
            page.set_viewport_size({'width': 1100, 'height': 900})
            page.goto(url + f'/ledger/import?session={statement}&step=2')
            expect(page.get_by_role('region', name='核对列')).to_be_visible(timeout=20000)
            no_sideways_scroll(page, 1100)
            page.goto(url + f'/ledger/import?session={statement}&step=3')
            expect(page.get_by_role('table', name='导入预览')).to_be_visible(timeout=20000)
            no_sideways_scroll(page, 1100)
            # ... and the table itself does not hide its 处理 / 跳过 column behind a sideways scroll.
            clipped = page.get_by_role('table', name='导入预览').evaluate(
                "t => { let box = t.parentElement; while (box && !['auto', 'scroll'].includes(getComputedStyle(box).overflowX))"
                " box = box.parentElement; return box ? [box.scrollWidth, box.clientWidth] : [0, 0] }")
            assert clipped[0] <= clipped[1], clipped
            shot('import-1100-preview.png', full=True)
            results['narrow_width'] = 1100
            page.set_viewport_size({'width': 1440, 'height': 900})
            stop(server)

            # -- the demo: the page explains why, and nothing is uploaded --------------------------------------
            server = backend(TRACKER_DEMO_MODE='1')
            before = len(uploads)
            page.goto(url + '/ledger/import')
            expect(page.get_by_text('数据为虚构，只读')).to_be_visible(timeout=30000)
            notice = page.get_by_role('status').filter(has_text='导入已停用')
            expect(notice).to_contain_text('演示模式只读')
            expect(page.locator('input[type=file]')).to_have_count(0)
            shot('import-demo.png')
            assert len(uploads) == before, uploads
            results['demo'] = 'passed'
        except Exception:
            if capture:
                page.screenshot(path=str(capture / 'failure.png'))
            raise
        assert not errors, errors
        browser.close()
    print(json.dumps({**results, 'screenshots': str(capture) if capture else None, 'page_errors': errors}, ensure_ascii=False))
