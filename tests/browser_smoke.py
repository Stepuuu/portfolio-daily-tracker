"""Offline browser journey through the ledger: 记一笔, its review, and the 账本 pages.

Fictional data only: the demo's legacy holdings (migrated through the opening preview), scripted quotes
(``fake_quotes.json``, clock pinned to a Tuesday morning session) and invented tickers. Checks: opening
migration; the account fee profile prefilling the fee (按账户费率估算, booked and shown as 估算) for an A-share
buy entered with three typed values and two Enters, where a held Enter (key repeat) never confirms; the
trade-plan callout in the review; a B share refused as soon as it is typed; a US buy whose fee must be
typed, confirmed while the answer is lost (the review reads the outcome back: 已入账); a sale from Today's row
with 可卖 / 全部 and 返回修改 (focus back on its field; the abandoned preview is discarded); the reference FX
rate taken only by 按参考汇率 and cleared with the currency; a dividend with 标的 and 再记一笔 keeping account
and date; the CSV template imported through 导入 and its de-duplication; reversal; the assistant's
proposal reviewed from Today's link with a durable receipt across a reload; a proposal confirmed in place
from the 待确认 tray; a missing link; research notes; the /portfolio redirect; positions at exact cost; the
390 px sheet, form and review.
Install Playwright and its Chromium first. Set TRACKER_SCREENSHOT_DIR to keep screenshots.
"""
from contextlib import ExitStack
from pathlib import Path
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
# The app follows the system theme until one is chosen in 设置: the smoke runs dark, or light with
# TRACKER_SCREENSHOT_THEME=light (screenshots for review in that theme). Theme assertions set the theme themselves.
SCHEME = 'light' if os.environ.get('TRACKER_SCREENSHOT_THEME') == 'light' else 'dark'
NOW = '2026-11-17T10:32:05+08:00'   # a Tuesday, A-share morning session
TODAY = NOW[:10]


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


def ready(url, process, seconds=120):
    # A deadline, not a number of tries: on a loaded machine the backend's imports alone can take tens of seconds.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Preview process exited before readiness')
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=1).close()
            return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Preview service did not become ready')


def activated_plan(env, moment, body):
    """Create and activate a plan as of ``moment`` on the scripted clock; returns its id.

    A plan is in force from the day it was activated (core.plans), and the API stamps the wall clock.
    The trades below are booked on the scripted day, so a plan activated "now" would postdate them as
    soon as the real date is later than that day.
    """
    script = ('import json, sys\nfrom datetime import datetime\nfrom core import desk_store, plans\n'
              'with desk_store.pinned_now(datetime.fromisoformat(sys.argv[1])):\n'
              '    print(plans.activate(plans.create(json.loads(sys.argv[2]))["id"])["id"])\n')
    done = subprocess.run([sys.executable, '-c', script, moment, json.dumps(body, ensure_ascii=False)],
                          env=env, check=True, capture_output=True, text=True)
    return int(done.stdout.split()[-1])


def label(scope, name):
    """The field labelled ``name``; a required field's label ends with an asterisk the screen reader skips."""
    return scope.get_by_label(re.compile('^' + re.escape(name) + r'\s*\*?$'))


QUOTES = {
    'now': NOW,
    'quotes': {
        'SHA:DEMO': {'name': '示例宽基', 'price': '61.20', 'prev_close': '60.80', 'quote_time': '2026-11-17T10:32:02+08:00'},
        'NASDAQ:DEMO': {'name': '示例研究标的', 'price': '119.60', 'prev_close': '118.90', 'quote_time': '2026-11-17T05:00:00+08:00'},
        'SHA:603999': {'name': '示例蓝筹', 'price': '20.00', 'prev_close': '19.80', 'quote_time': '2026-11-17T10:32:00+08:00'},
    },
    'fx': {'USD': {'rate': '7.05', 'age_seconds': 5}, 'HKD': {'rate': '0.90', 'age_seconds': 5}},
}


with ExitStack() as stack:
    out = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='tracker-browser-')))
    backend_port, frontend_port = free_port(), free_port()
    portfolio = out / 'portfolio'
    # Made for 2026-09-18, its holdings end on Friday 2026-08-28, before the opening date typed below.
    subprocess.run([sys.executable, str(root / 'engine/scripts/create_demo.py'), '--output', str(portfolio),
                    '--today', '2026-09-18'], check=True)
    (portfolio / 'fake_quotes.json').write_text(json.dumps(QUOTES, ensure_ascii=False), encoding='utf-8')
    env = {**os.environ, 'PORTFOLIO_DIR': str(portfolio), 'TRACKER_DEMO_MODE': '0', 'TRACKER_QUOTE_SOURCE': 'fake',
           'TRACKER_CONFIG_FILE': str(out / 'config.json'), 'TRACKER_LAB_DIR': str(out / 'lab'),
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
    url = f'http://127.0.0.1:{frontend_port}'
    api = f'http://127.0.0.1:{backend_port}/api'
    capture = Path(os.environ['TRACKER_SCREENSHOT_DIR']) if os.environ.get('TRACKER_SCREENSHOT_DIR') else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True, args=['--no-sandbox'])
        page = browser.new_page(color_scheme=SCHEME, viewport={'width': 1440, 'height': 1000}, locale='zh-CN', timezone_id='Asia/Shanghai')
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        def shot(name, full=False):
            if not capture:
                return
            page.wait_for_timeout(250)
            if full:
                size = page.viewport_size
                extra = page.evaluate("(() => { const m = document.getElementById('main'); return m ? m.scrollHeight - m.clientHeight : 0 })()")
                page.set_viewport_size({'width': size['width'], 'height': size['height'] + max(0, extra)})
                page.wait_for_timeout(150)
                page.screenshot(path=str(capture / f'{name}.png'))
                page.set_viewport_size(size)
            else:
                page.screenshot(path=str(capture / f'{name}.png'))

        def ledger_state():
            return page.request.get(f'{api}/ledger').json()

        def toast():
            return page.get_by_role('region', name='通知')

        dialog = page.locator('dialog[open]')

        # -- opening balances from the legacy holdings file -----------------------------------------
        page.goto(url + '/ledger')
        expect(page.get_by_text('还没有账户', exact=True)).to_be_visible(timeout=60000)
        shot('ledger-empty')
        label(page, '期初日期').fill('2026-09-01')
        page.get_by_role('button', name='预览已有持仓', exact=True).click()
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('2 笔待入账')
        expect(review.get_by_role('table')).to_contain_text('无')   # new accounts: nothing before, never 0
        assert ledger_state()['revision'] == 0
        dialog.get_by_role('button', name='确认入账', exact=True).click()
        expect(toast()).to_contain_text('已入账 2 笔')
        expect(dialog.get_by_role('button', name='已入账', exact=True)).to_be_disabled()
        dialog.get_by_role('button', name='关闭', exact=True).first.click()
        expect(dialog).to_have_count(0)
        expect(page.get_by_role('group', name='账户概况')).to_contain_text('长期账户')
        results['migration'] = 'passed'

        # -- the account's fee profile, set in 设置 ---------------------------------------------------
        page.goto(url + '/settings')
        section = page.get_by_role('region', name='账户与费率')
        expect(label(section, '账户')).to_have_value('长期账户', timeout=30000)
        expect(section).to_contain_text('预设')
        label(section, '佣金率').fill('3')
        section.get_by_role('button', name='保存', exact=True).click()
        expect(toast()).to_contain_text('已保存「长期账户」的费率：万3 · 最低 ¥5.00')
        shot('settings-accounts')
        results['fee_profile'] = 'passed'

        # A plan for the stock bought next (on the scripted day), to see the review compare the trade with it.
        plan_id = activated_plan(env, NOW, {
            'ticker': 'SHA:603999', 'account': '长期账户', 'thesis': '虚构计划：检验入场区间提示。',
            'invalidation': '虚构条件：跌破止损。', 'entry_low': '18', 'entry_high': '19.5', 'stop_price': '17',
            'target_price': '25', 'planned_quantity': '500'})

        # -- an A-share buy: three typed values, Enter, Enter ------------------------------------------
        page.goto(url + '/ledger')
        expect(page.get_by_role('group', name='账户概况')).to_be_visible()
        page.keyboard.press('n')
        form = page.get_by_role('dialog', name='记一笔')
        expect(form).to_be_visible()
        expect(form.get_by_role('radio', name='买入')).to_have_attribute('aria-checked', 'true')
        expect(label(form, '账户')).to_have_value('长期账户')
        expect(label(form, '日期')).to_have_value(TODAY)
        expect(form).to_contain_text('今天')
        # Count what is really typed and pressed: the fields that receive input, and every Enter.
        page.evaluate("""() => {
            window.__typed = new Set(); window.__enters = 0;
            document.addEventListener('input', (e) => {
                const id = e.target.id; const label = id && document.querySelector(`label[for="${CSS.escape(id)}"]`);
                window.__typed.add(label ? label.textContent.replace('*', '').trim() : e.target.getAttribute('aria-label') || e.target.name);
            }, true);
            document.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.repeat) window.__enters += 1 }, true);
        }""")
        for name, value in (('标的', 'SHA:603999'), ('数量', '100'), ('价格', '20')):
            label(form, name).fill(value)
        expect(form).to_contain_text('示例蓝筹 · SHA:603999 · CNY')
        expect(form).to_contain_text('现价 20.00 · 昨收 19.80')
        expect(form).to_contain_text('按账户费率估算：佣金 5.00 · 过户费 0.02')
        expect(label(form, '费用')).to_have_attribute('placeholder', '5.02')
        expect(form).to_contain_text('CNY 现金 ¥20,000.00 → ¥17,994.98')
        shot('entry-buy-form')
        # Enter is held down from the price field: it previews, and its key repeat must not confirm.
        label(form, '价格').focus()
        page.keyboard.down('Enter')
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('1 笔待入账')
        expect(dialog.get_by_role('button', name='确认入账', exact=True)).to_be_focused()
        revision = ledger_state()['revision']
        for _ in range(3):
            page.keyboard.down('Enter')   # repeat: true
        page.wait_for_timeout(400)
        page.keyboard.up('Enter')
        assert ledger_state()['revision'] == revision, 'a held Enter booked the preview'
        expect(dialog.get_by_role('button', name='确认入账', exact=True)).to_be_enabled()
        expect(review).to_contain_text('费用 5.02（估算）')
        expect(review).to_contain_text('费用为估算：按账户费率估算')
        callout = review.get_by_role('region', name='对照交易计划')
        expect(callout).to_contain_text(f'计划 #{plan_id} · 建仓')
        warning = callout.get_by_text('高于计划入场区间', exact=False)
        expect(warning).to_be_visible()
        assert 'text-warn' in warning.evaluate('node => node.closest("li").className')
        expect(review.get_by_role('table')).to_contain_text('示例蓝筹 数量')
        expect(dialog.get_by_role('button', name='确认入账', exact=True)).to_be_focused()
        shot('entry-review-plan')
        page.keyboard.press('Enter')   # a fresh press on 确认入账 books it
        expect(toast()).to_contain_text('已入账 1 笔 · 版本 2')
        expect(page.get_by_role('dialog', name='核对入账')).to_have_count(0)
        typed = sorted(page.evaluate('[...window.__typed]'))
        enters = page.evaluate('window.__enters')
        assert typed == sorted(['标的', '数量', '价格']), typed
        assert enters == 2, enters
        position = ledger_state()['accounts']['长期账户']['positions']['SHA:603999']
        assert position['quantity'] == '100' and position['name'] == '示例蓝筹', position
        results['a_share_buy'] = {'typed_values': len(typed), 'enters': enters, 'fee': '5.02', 'held_enter_booked': False}
        results['plan_check'] = 'passed'

        # -- a US buy: no fee profile for the market, so the fee must be typed --------------------------
        page.get_by_role('main').get_by_role('button', name='记一笔').click()
        form = page.get_by_role('dialog', name='记一笔')
        label(form, '标的').fill('SHA:900901')
        expect(form).to_contain_text('SHA:900901 是 B 股，以 USD 交易；账本按人民币记录沪深标的，暂不支持记入 B 股。')
        label(form, '标的').fill('NASDAQ:EXAMPLE')
        label(form, '数量').fill('0.5')
        label(form, '价格').fill('100')
        expect(form).to_contain_text('未设置该市场费率，请按成交单填写')
        label(form, '备注').fill('虚构交易：验证手续费与碎股')
        label(form, '备注').press('Enter')
        expect(form.get_by_text('请填写成交单上的费用')).to_be_visible()
        label(form, '费用').fill('1')
        form.get_by_role('button', name='预览入账').click()
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('1 笔待入账')
        expect(review).to_contain_text('USD 现金')
        expect(review).to_contain_text('$949.00')
        expect(review.get_by_role('region', name='对照交易计划')).to_contain_text('没有启用中的交易计划')
        shot('entry-review-us')
        # The confirmation reaches the backend but its answer is lost: the review reads the outcome back.
        lost = []

        def lose_answer(route):
            route.fetch()
            lost.append(route.request.url)
            route.abort()
        page.route('**/api/ledger/proposals/*/confirm', lose_answer)
        dialog.get_by_role('button', name='确认入账', exact=True).click()
        expect(toast()).to_contain_text('已入账 1 笔')
        expect(page.get_by_role('dialog', name='核对入账')).to_have_count(0)
        page.unroute('**/api/ledger/proposals/*/confirm')
        assert len(lost) == 1, lost
        assert ledger_state()['accounts']['长期账户']['cash_balances']['USD'] == '949'
        results['trade'] = 'passed'
        results['lost_answer_read_back'] = 'passed'

        # -- a sale from Today's row: 可卖 and 全部; 返回修改 keeps the form; closing books nothing --------
        page.goto(url + '/')
        holdings = page.get_by_role('table', name='持仓')
        row = holdings.locator('tbody tr').filter(has_text='SHA:603999').first
        expect(row).to_be_visible(timeout=30000)
        row.hover()
        row.get_by_role('button', name='卖', exact=True).click()
        form = page.get_by_role('dialog', name='记一笔')
        expect(form.get_by_role('radio', name='卖出')).to_have_attribute('aria-checked', 'true')
        expect(label(form, '标的')).to_have_value('SHA:603999')
        expect(form).to_contain_text('可卖 100 股')
        form.get_by_role('button', name='全部', exact=True).click()
        expect(label(form, '数量')).to_have_value('100')
        form.get_by_role('button', name='按现价', exact=True).click()
        expect(label(form, '价格')).to_have_value('20')
        expect(form).to_contain_text('印花税 1.00')
        shot('entry-sell')
        with page.expect_response(lambda r: r.url.endswith('/api/ledger/proposals') and r.request.method == 'POST') as made:
            form.get_by_role('button', name='预览入账').click()
        sale_id = made.value.json()['id']
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('卖出 示例蓝筹 SHA:603999 100 股')
        sold_row = review.get_by_role('row').filter(has_text='示例蓝筹 数量')
        expect(sold_row.get_by_role('cell').last).to_have_text('无')   # sold out: no position afterwards
        dialog.get_by_role('button', name='返回修改', exact=True).click()
        form = page.get_by_role('dialog', name='记一笔')
        expect(label(form, '数量')).to_have_value('100')
        expect(label(form, '数量')).to_be_focused()   # back where the entry was being made
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        deadline = time.monotonic() + 15
        while sale_id in [p['id'] for p in ledger_state()['pending']]:
            assert time.monotonic() < deadline, 'an abandoned preview does not wait in 待确认'
            page.wait_for_timeout(100)
        assert ledger_state()['accounts']['长期账户']['positions']['SHA:603999']['quantity'] == '100'
        results['sell_all'] = 'passed'

        # -- foreign cash: the reference rate; a dividend with 标的; 再记一笔 keeps account and date ------
        page.goto(url + '/ledger')
        page.get_by_role('main').get_by_role('button', name='记一笔').click()
        form = page.get_by_role('dialog', name='记一笔')
        form.get_by_role('radio', name='入金').click()
        label(form, '币种').select_option('USD')
        rate = label(form, '汇率（1 USD = ? CNY）')
        expect(rate).to_have_value('')   # evidence, not a default: nothing is prefilled
        expect(form).to_contain_text('参考汇率 1 USD = 7.0500 CNY · 模拟行情')
        form.get_by_role('button', name='按参考汇率', exact=True).click()
        expect(rate).to_have_value('7.05')
        expect(form).to_contain_text('已按参考汇率填入 · 模拟行情')
        shot('entry-deposit-fx')
        rate.fill('7.20')
        label(form, '币种').select_option('HKD')
        expect(label(form, '汇率（1 HKD = ? CNY）')).to_have_value('')   # a USD rate never carries over
        label(form, '币种').select_option('USD')
        expect(label(form, '汇率（1 USD = ? CNY）')).to_have_value('')
        form.get_by_role('radio', name='分红').click()
        label(form, '账户').select_option('研究账户')
        label(form, '日期').fill('2026-11-16')
        label(form, '标的（选填）').fill('NASDAQ:DEMO')
        expect(label(form, '币种')).to_have_value('USD')
        label(form, '分红金额').fill('2')
        label(form, '分红金额').press('Enter')
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('分红 示例研究标的 NASDAQ:DEMO $2.00')
        expect(dialog.get_by_role('button', name='确认入账', exact=True)).to_be_focused()
        page.keyboard.press('Enter')
        expect(toast()).to_contain_text('已入账 1 笔')
        toast().get_by_role('button', name='再记一笔').click()
        form = page.get_by_role('dialog', name='记一笔')
        expect(label(form, '账户')).to_have_value('研究账户')
        expect(label(form, '日期')).to_have_value('2026-11-16')
        expect(form.get_by_role('radio', name='分红')).to_have_attribute('aria-checked', 'true')
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        results['dividend_again'] = 'passed'

        # -- the system's CSV template, through 导入 (/ledger/import), de-duplicated -------------------
        content = 'external_id,date,kind,account,currency,amount,note\nexample-001,2026-09-03,deposit,长期账户,CNY,50,虚构入金\n'
        expect(page.get_by_role('button', name=re.compile('CSV 导入'))).to_have_count(0)
        for duplicate in [False, True]:
            page.goto(url + '/ledger')
            page.get_by_role('main').get_by_role('link', name='导入').last.click()
            expect(page).to_have_url(url + '/ledger/import')
            label(page, '导入到账户').select_option('长期账户')
            page.get_by_label('选择券商文件').set_input_files({'name': 'example.csv', 'mimeType': 'text/csv', 'buffer': content.encode()})
            expect(page.get_by_role('region', name='核对列')).to_contain_text('本系统模板', timeout=30000)
            page.get_by_role('button', name='继续：预览与去重').click()
            propose = page.get_by_role('button', name=re.compile('^生成入账预览'))
            if duplicate:
                expect(page.get_by_text('全部已导入', exact=True)).to_be_visible()
                expect(propose).to_be_disabled()
                continue
            propose.click()
            review = page.get_by_role('region', name='核对入账')
            expect(review).to_contain_text('1 笔待入账')
            page.get_by_role('button', name='确认入账', exact=True).click()
            expect(toast()).to_contain_text('已入账 1 笔')
            expect(page.get_by_role('region', name='导入结果')).to_be_visible(timeout=20000)
        results['csv_dedup'] = 'passed'

        # -- the events, and a reversal ----------------------------------------------------------------
        page.goto(url + '/ledger')
        events = page.get_by_role('table', name='账本流水')
        expect(events.get_by_text('NASDAQ:EXAMPLE', exact=True)).to_have_count(1)
        expect(events).to_contain_text('交割单导入')
        shot('ledger-events', full=True)
        events.get_by_role('button', name=re.compile('^冲销 .* NASDAQ:EXAMPLE$')).click()
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('1 笔待入账')
        expect(review).to_contain_text('冲销：')
        dialog.get_by_role('button', name='确认入账', exact=True).click()
        expect(toast()).to_contain_text('已入账 1 笔')
        dialog.get_by_role('button', name='关闭', exact=True).first.click()
        expect(events.get_by_text('已冲销', exact=True)).to_be_visible()
        assert ledger_state()['accounts']['长期账户']['cash_balances']['USD'] == '1000'
        results['reversal'] = 'passed'

        # -- a proposal prepared by the assistant: Today's link, review, durable receipt ---------------
        pending = page.request.post(f'{api}/ledger/proposals', data={'events': [
            {'kind': 'buy', 'date': '2026-09-04', 'account': '长期账户', 'ticker': 'NASDAQ:EXAMPLE', 'currency': 'USD', 'quantity': '0.5', 'price': '100', 'fee': '1'}
        ]}).json()
        page.goto(url + '/')
        expect(page.get_by_role('region', name='待确认账本方案')).to_contain_text('尚未入账')
        page.get_by_role('link', name='核对方案', exact=False).first.click()
        expect(page).to_have_url(f'{url}/ledger?proposal={pending["id"]}')
        review = dialog.get_by_role('region', name='核对入账')
        expect(review).to_contain_text('$949.00')
        dialog.get_by_role('button', name='确认入账', exact=True).click()
        expect(toast()).to_contain_text('已入账 1 笔')
        expect(review).to_contain_text('USD 现金')
        expect(dialog.get_by_role('button', name='已入账', exact=True)).to_be_disabled()
        revision = ledger_state()['revision']
        page.reload()
        expect(dialog.get_by_role('button', name='已入账', exact=True)).to_be_disabled()
        expect(dialog.get_by_role('region', name='核对入账')).to_contain_text('$949.00')
        assert ledger_state()['revision'] == revision
        page.keyboard.press('Escape')
        expect(page).to_have_url(f'{url}/ledger')
        results['chat_proposal_handoff'] = 'passed'
        results['durable_receipt'] = 'passed'

        # -- confirmed in place from the 待确认 tray, without leaving the page -------------------------
        tray_proposal = page.request.post(f'{api}/ledger/proposals', data={'events': [
            {'kind': 'deposit', 'date': TODAY, 'account': '研究账户', 'currency': 'CNY', 'amount': '10'}]}).json()
        page.goto(url + '/ledger/positions')
        page.get_by_role('button', name='待确认 1 项').click()
        tray = page.get_by_role('dialog', name='待确认')
        tray.get_by_role('button', name='核对方案').click()
        in_place = tray.get_by_role('region', name='核对入账')
        expect(in_place).to_contain_text('1 笔待入账')
        expect(in_place).to_contain_text('入金 ¥10.00')
        shot('tray-review')
        in_place.get_by_role('button', name='确认入账', exact=True).click()
        expect(toast()).to_contain_text('已入账 1 笔')
        expect(page).to_have_url(f'{url}/ledger/positions')
        assert page.request.get(f'{api}/ledger/proposals/{tray_proposal["id"]}').json()['receipt']['applied'] == 1
        page.keyboard.press('Escape')
        results['tray_in_place'] = 'passed'

        # -- positions at cost ---------------------------------------------------------------------------
        positions = page.get_by_role('table', name='持仓与成本')
        expect(positions.get_by_text('示例蓝筹', exact=True)).to_be_visible()
        expect(positions).to_contain_text('¥20.0502')   # (2,000 + 5.02) / 100, every digit the ledger has
        shot('ledger-positions', full=True)
        results['positions'] = 'passed'

        # -- an invalid link cannot select another pending transaction ---------------------------------
        page.goto(url + '/ledger?proposal=missing')
        expect(page.get_by_role('alert')).to_contain_text('方案不存在')
        expect(page.get_by_role('button', name='确认入账', exact=True)).to_have_count(0)
        page.keyboard.press('Escape')
        results['missing_link'] = 'passed'

        # -- research notes, now under 计划与复盘 › 笔记 ----------------------------------------------
        page.goto(url + '/notes')
        label(page, '标的').fill('NASDAQ:EXAMPLE')
        page.get_by_label('投资逻辑 / 本次复盘结论').fill('虚构研究示例：检验产品需求是否持续。')
        page.get_by_label('什么情况说明判断失效？').fill('虚构条件：后续证据不支持原假设。')
        page.get_by_label('计划复查日期').fill('2026-09-30')
        page.get_by_role('button', name='保存研究记录').click()
        expect(toast()).to_contain_text('研究记录已保存')
        expect(page.get_by_role('region', name='研究笔记')).to_contain_text('虚构研究示例')
        shot('notes')
        results['journal'] = 'passed'

        # -- phones: the dialog is a full-height sheet; nothing scrolls sideways -----------------------
        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto(url + '/ledger')
        expect(page.get_by_text('已冲销', exact=True).first).to_be_visible()
        page.wait_for_timeout(300)
        assert page.evaluate('document.body.scrollWidth') <= 390
        main = page.evaluate("(() => { const m = document.getElementById('main'); return [m.scrollWidth, m.clientWidth] })()")
        assert main[0] <= main[1], main
        shot('ledger-mobile', full=True)
        page.get_by_role('navigation', name='主导航').get_by_role('button', name='记一笔').click()
        sheet = page.get_by_role('dialog', name='记一笔')
        expect(sheet).to_be_visible()
        sheet.evaluate('node => node.getAnimations().forEach((animation) => animation.finish())')
        box = sheet.bounding_box()
        assert abs(box['width'] - 390) < 1 and abs(box['height'] - 844) < 1 and abs(box['x']) < 1 and abs(box['y']) < 1, box
        sheet.get_by_role('radio', name='买入').click()   # the last kind booked was a dividend
        label(sheet, '标的').fill('SHA:603999')
        label(sheet, '数量').fill('100')
        expect(sheet).to_contain_text('现价 20.00')
        assert page.evaluate("document.querySelector('dialog[open]').scrollWidth <= 390")
        shot('entry-mobile-sheet')
        label(sheet, '价格').fill('20')
        label(sheet, '价格').press('Enter')
        mobile_review = dialog.get_by_role('region', name='核对入账')
        expect(mobile_review).to_contain_text('1 笔待入账')
        assert page.evaluate("document.querySelector('dialog[open]').scrollWidth <= 390")
        shot('entry-mobile-review')
        page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        results['mobile_width'] = 390

        # -- old paths -----------------------------------------------------------------------------------
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.goto(url + '/portfolio')
        expect(page).to_have_url(f'{url}/ledger')
        page.reload()
        expect(page.get_by_text('已冲销', exact=True)).to_be_visible()
        results['legacy_redirect'] = 'passed'
        results['reload'] = 'passed'

        assert not errors, errors
        print(json.dumps({**results, 'screenshots': str(capture) if capture else None, 'page_errors': errors}, ensure_ascii=False))
        browser.close()
