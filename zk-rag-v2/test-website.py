#!/usr/bin/env python3
"""Playwright smoke tests for militarymanuals.ai"""
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1'
errors = []

def safe_click(page, selector, timeout=2000):
    """Click only if element is visible. Return True if clicked, False otherwise."""
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            el.click(timeout=timeout)
            return True
    except Exception:
        pass
    return False

def safe_close_modal(page, selector):
    """Close modal only if it's open/visible."""
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            close = page.query_selector(selector + 'Close')
            if close and close.is_visible():
                close.click()
                page.wait_for_timeout(200)
    except Exception:
        pass

def run():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
        page.on('pageerror', lambda err: errors.append(str(err)))

        # ── CATALOG.HTML ──────────────────────────────────────────────────────────
        print('=== CATALOG.HTML ===')
        page.goto(f'{BASE}/catalog.html')
        page.wait_for_load_state('load', timeout=10000)

        tabs = page.query_selector_all('.tab')
        print(f'[1] Tabs: {len(tabs)} (want 4) {"PASS" if len(tabs)==4 else "FAIL"}')

        page.wait_for_selector('.doc-card', timeout=8000)
        cards = page.query_selector_all('.doc-card')
        print(f'[2] Doc cards: {len(cards)} (want 587) {"PASS" if len(cards)==587 else "FAIL"}')

        btns = page.query_selector_all('.download-pdf-btn')
        print(f'[3] Download buttons: {len(btns)} (want 587) {"PASS" if len(btns)==587 else "FAIL"}')

        if btns:
            btns[0].click()
            page.wait_for_timeout(600)
            modal = page.query_selector('#paymentModal')
            modal_display = modal.evaluate('el => el.style.display') if modal else 'none'
            print(f'[4] Payment modal on click: {"PASS" if modal_display != "none" else "FAIL (display=" + modal_display + ")"}')
            safe_close_modal(page, '#paymentModal')

        if len(tabs) > 1:
            tabs[1].click()
            page.wait_for_timeout(200)
            active = page.query_selector('.collection-panel.active')
            print(f'[5] Tab switch: {"PASS" if active else "FAIL"}')

        # ── INDEX.HTML ───────────────────────────────────────────────────────────
        print('\n=== INDEX.HTML ===')
        errors.clear()
        page.goto(f'{BASE}/index.html')
        page.wait_for_load_state('load', timeout=10000)

        search_box = page.query_selector('#searchInput')
        print(f'[6] Search input: {"PASS" if search_box else "FAIL"}')

        page.fill('#searchInput', 'tactics')
        page.click('#searchBtn')
        page.wait_for_timeout(3000)

        passages = page.query_selector_all('.passage-card')
        print(f'[7] Passage cards after search: {len(passages)} {"PASS" if len(passages)>0 else "FAIL"}')

        # Download button in .document-header
        header_btns = page.query_selector_all('.document-header .download-pdf-btn')
        print(f'[8] Download buttons in document-header: {len(header_btns)} {"PASS" if len(header_btns)>0 else "FAIL"}')

        if header_btns:
            header_btns[0].click()
            page.wait_for_timeout(600)
            modal = page.query_selector('#paymentModal')
            modal_display = modal.evaluate('el => el.style.display') if modal else 'none'
            print(f'[9] Payment modal on header click: {"PASS" if modal_display != "none" else "FAIL (display=" + modal_display + ")"}')
            safe_close_modal(page, '#paymentModal')

        # Chunk nav buttons
        nav_btns = page.query_selector_all('.chunk-nav-btn')
        print(f'[10] Chunk nav buttons: {len(nav_btns)} {"PASS" if len(nav_btns)>0 else "FAIL"}')

        if nav_btns:
            nav_btns[0].click()
            page.wait_for_timeout(1500)
            print(f'[11] Chunk nav click: PASS (no crash)')

        # ZK Provenance search
        prov_btn = page.query_selector('#searchProvenanceBtn')
        if prov_btn:
            page.fill('#searchInput', 'military doctrine')
            prov_btn.click()
            page.wait_for_timeout(5000)
            prov_passages = page.query_selector_all('.passage-card')
            print(f'[12] ZK Provenance cards: {len(prov_passages)} {"PASS" if len(prov_passages)>0 else "FAIL"}')
            if errors:
                print(f'     Console errors: {errors[-1]}')
        else:
            print(f'[12] ZK Provenance button: SKIP (not found)')

        # Direct API test for query-provable
        page.evaluate('''(async () => {
            try {
                const r = await fetch('/api/query-provable', {
                    method: 'POST',
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({query: "tactics", top_k: 2})
                });
                const d = await r.json();
                window.__prov_ok = !d.error && !d.detail && Array.isArray(d.chunks);
                window.__prov_err = d.error || d.detail || null;
            } catch(e) { window.__prov_err = e.message; }
        })()''')
        page.wait_for_timeout(2000)
        prov_ok = page.evaluate('window.__prov_ok')
        prov_err = page.evaluate('window.__prov_err')
        print(f'[13] /api/query-provable: {"PASS" if prov_ok else "FAIL (" + str(prov_err) + ")"}')

        # Direct API test for query (non-provenance)
        page.evaluate('''(async () => {
            try {
                const r = await fetch('/api/query', {
                    method: 'POST',
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({query: "tactics", top_k: 2, collection: "army"})
                });
                const d = await r.json();
                window.__query_ok = !d.error && !d.detail && (Array.isArray(d.results) || Array.isArray(d.chunks));
                window.__query_err = d.error || d.detail || null;
            } catch(e) { window.__query_err = e.message; }
        })()''')
        page.wait_for_timeout(2000)
        query_ok = page.evaluate('window.__query_ok')
        query_err = page.evaluate('window.__query_err')
        print(f'[14] /api/query: {"PASS" if query_ok else "FAIL (" + str(query_err) + ")"}')

        print('\n=== CONSOLE ERRORS ===')
        if errors:
            for e in errors:
                print('ERROR:', e)
        else:
            print('None — all clean')

        browser.close()
        print('Done.')

if __name__ == '__main__':
    run()
