#!/usr/bin/env python3
"""Playwright test: ZK Prove button auto-polls Kurier without manual button click."""
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1'

def run():
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()

        # Capture all console messages
        console_logs = []
        errors = []
        page.on('console', lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on('pageerror', lambda err: errors.append(str(err)))

        # ── Load index, search ───────────────────────────────────────────────────
        print('=== ZK AUTO-POLL TEST ===')
        page.goto(f'{BASE}/index.html')
        page.wait_for_load_state('load', timeout=10000)

        search_box = page.query_selector('#searchInput')
        if not search_box:
            print('[FAIL] No search input found')
            browser.close()
            return
        print('[PASS] Page loaded')

        # Use ZK Provenance search to get results with ZK buttons
        prov_btn = page.query_selector('#searchProvenanceBtn')
        if prov_btn:
            page.fill('#searchInput', 'military doctrine')
            prov_btn.click()
            page.wait_for_timeout(5000)
            print('[PASS] ZK Provenance search completed')
        else:
            # Fallback to regular search
            page.fill('#searchInput', 'army tactics')
            page.click('#searchBtn')
            page.wait_for_timeout(4000)
            print('[INFO] Used regular search')

        passage_cards = page.query_selector_all('.passage-card')
        print(f'[INFO] Passage cards found: {len(passage_cards)}')

        # Find a ZK Prove button
        zk_btns = page.query_selector_all('.zk-expand-btn')
        print(f'[INFO] ZK expand buttons: {len(zk_btns)}')

        if not zk_btns:
            print('[FAIL] No ZK expand buttons found')
            browser.close()
            return

        # Click the first ZK Prove button
        console_logs.clear()
        zk_btns[0].click()
        print('[INFO] Clicked first ZK Prove button')

        # Wait for the submenu to appear
        page.wait_for_timeout(1000)

        # Check for toast — should appear within ~2 seconds of clicking
        toast_found = False
        for _ in range(10):  # wait up to 10s for toast
            page.wait_for_timeout(1000)
            toast = page.query_selector('#zk-toast')
            if toast:
                toast_text = toast.inner_text()
                print(f'[INFO] Toast appeared: {toast_text}')
                toast_found = True
                break

        if not toast_found:
            print('[FAIL] No toast notification appeared after clicking ZK Prove')

        # Check console logs for auto-poll signals
        auto_poll_logs = [l for l in console_logs if '_autoPollKurier' in l or '_handleNav' in l]
        print(f'[INFO] Auto-poll console logs: {auto_poll_logs}')

        # Check for kurier_job_id in API response
        proof_logs = [l for l in console_logs if 'kurier_job_id' in l or 'ZK proof fetched' in l]
        print(f'[INFO] Proof fetch logs: {proof_logs}')

        # Look for verification status changes in the submenu
        page.wait_for_timeout(3000)
        status_els = page.query_selector_all('[id^="verif-status"]')
        for sel in status_els:
            txt = sel.inner_text()
            print(f'[INFO] Status element: {txt}')

        # Report console errors
        if errors:
            print(f'\n[FAIL] Page errors: {errors}')
        else:
            print('[PASS] No page errors')

        print('\n=== ALL CONSOLE LOGS ===')
        for l in console_logs:
            print(l)

        browser.close()
        print('\nDone.')

if __name__ == '__main__':
    run()
