import time, sys
sys.path.insert(0, '/opt/maxcall-broker')
from playwright.sync_api import sync_playwright
from brokers.mednet import MedNet
from brokers.base import creds_for
from brokers.base import Worker
import logging

creds = creds_for('mednet')
log = logging.getLogger('DIAG'); log.setLevel(logging.INFO)

m = MedNet()

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        '/var/lib/maxcall-broker/profiles/mednet', headless=True,
        args=['--disable-gpu', '--disable-dev-shm-usage', '--no-first-run'],
        accept_downloads=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.on('console', lambda cst: print('CON:', str(cst.text)[:100]))
    page.on('response', lambda r: print('RESP:', r.status,
        (r.headers.get('content-type') or '').split(';')[0][:30], r.url[:120]))
    page.on('download', lambda d: print('DOWNLOAD:', d.suggested_filename, d.url[:150]))
    page.on('framenavigated', lambda f: print('NAV:', (f.url or '')[:130]))

    def popup_hook(pg):
        print('NEW PAGE URL@:', pg.url[:160])
        pg.on('framenavigated', lambda f: print('POP NAV:', (f.url or '')[:160]))
        pg.on('download', lambda d: print('POP DOWNLOAD:', d.suggested_filename, d.url[:150]))
        pg.on('response', lambda r: print('POP RESP:', r.status,
            (r.headers.get('content-type') or '').split(';')[0][:30], r.url[:120]))

    ctx.on('page', popup_hook)

    w = Worker('mednet', m, None, log)
    w.page = page
    w.pw = p
    w._started_at = time.monotonic()

    print('GOTO', creds['url'][:80])
    page.goto(creds['url'], wait_until='domcontentloaded', timeout=45000)
    print('LOADED', page.url[:80])

    if m._safe_eval(w, '() => !!document.querySelector(\'input[type="password"]\')'):
        print('LOGIN required')
        m.do_login(w, creds)
    print('stage after auth:', m.state(w))

    # شاشة التحقق (MainOnline + Member Eligibility)
    inp = m._find(w, '() => document.querySelector(\'input[id$="it5::content"], input[id$=":it5"]\')')
    if not inp:
        nav = m._find(w, '() => [...document.querySelectorAll("a")].find(a => /member\\s*eligib|check\\s*eligib|eligibility\\s*check/i.test((a.textContent||"")+" "+(a.getAttribute("alt")||"")+" "+(a.getAttribute("aria-label")||""))) || document.querySelector(\'a[id$="cil1"]\')')
        print('nav found:', bool(nav))
        if nav:
            nav.evaluate('el => el.click()')
            time.sleep(4)
        inp = m._wait_find(w, '() => document.querySelector(\'input[id$="it5::content"], input[id$=":it5"]\')', 25)
    print('member-input:', bool(inp))

    inp.evaluate('el => el.focus()')
    w.set_value(inp, '919492')
    m._adf_idle(w, 15)
    print('elig input set')

    btn = m._find(w, '() => document.querySelector(\'button[id$="cb1"], input[id$="cb1"]\')')
    print('check-btn:', bool(btn))
    btn.evaluate('el => el.click()')
    print('verdict running...')
    m._adf_idle(w, 45)
    # استطلع الرقم حتى يظهر (10 جولات × 2ث)
    for i in range(15):
        no = m._safe_eval(w, '() => { const el = document.querySelector(\'input[id$="it3::content"], input[id$=":it3"]\'); return el ? String(el.value||"").trim() : null; }')
        if no and no != '0':
            print('ELIG-NO:', no)
            break
        time.sleep(2)
    else:
        print('eligibility-no did not appear — irrelevant for print analysis')

    printb = m._find(w, '() => document.querySelector(\'button[id$="cb2"], input[id$="cb2"], a[id$="cb2"]\')')
    print('print-btn present:', bool(printb), printb and printb.get_attribute('id'))
    if printb:
        print('print-btn outerHTML >>>')
        print((printb.evaluate('el => el.outerHTML') or '')[:600])
    if printb:
        print('>> CLICK PRINT <<')
        printb.evaluate('el => el.click()')
        for i in range(50):
            time.sleep(0.5)
            urls = [pg.url or '(about:blank)' for pg in ctx.pages]
            print(f't+{(i+1)*0.5:.1f}s pages={len(urls)} {urls}')
            if i in (3, 9, 19):
                for pi, pg in enumerate(ctx.pages):
                    if pi == 0:
                        continue
                    print(f'   popup frame urls: {[fr.url for fr in pg.frames]}')
                    print(f'   popup closed: {pg.is_closed()}')

    print('total pages:', len(ctx.pages))
    for pg in ctx.pages:
        print('FINAL PAGE:', (pg.url or '(about:blank)')[:160])
    ctx.close()
