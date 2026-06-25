#!/usr/bin/env python3
"""Emit remaining unemitted docs individually (one at a time)."""
import json
import subprocess
import os
import time
import sys

registry = json.load(open('./data/registry.json'))

# Get unemitted doc_ids from tree idx 4 onwards
remaining = []
for i in range(4, 604):
    doc = registry['documents'][i]
    if not doc.get('emitted_testnet', {}).get('tx_hash'):
        remaining.append(doc['doc_id'])

print(f'Emitting {len(remaining)} docs individually...')
sys.stdout.flush()

# Read ACTIVE_NETWORK from .env to select correct contract
_active = os.environ.get('ACTIVE_NETWORK', 'testnet').strip().lower()
if _active == 'mainnet':
    _contract = os.environ.get('MAINNET_CONTRACT_ADDRESS', '0x462fc86E28c07798BD4656451611FE4E0A6D7760')
    _rpc     = os.environ.get('MAINNET_RPC_URL',       'https://horizen.calderachain.xyz/http')
else:
    _contract = os.environ.get('TESTNET_CONTRACT_ADDRESS', '0x83166A340c0A61bc836BD6383aD4acB23a3E3176')
    _rpc     = os.environ.get('TESTNET_RPC_URL',        'https://horizen-testnet.rpc.caldera.xyz')

env = os.environ.copy()
env['DEPLOYER_KEY'] = os.environ.get('DEPLOYER_KEY', '')
env['CONTRACT_ADDRESS'] = _contract
env['RPC_URL'] = _rpc
env['PATH'] = os.path.expanduser('~/.foundry/bin') + ':' + env.get('PATH', '')

ok = 0
fail = 0
for i, doc_id in enumerate(remaining):
    print(f'[{i+1}/{len(remaining)}] {doc_id[:16]}... ', end='', flush=True)
    result = subprocess.run(
        ['python3', 'emit_all.py', '--doc-id', doc_id, '--batch'],
        env=env,
        capture_output=True, text=True, timeout=120
    )
    # Check for success: exit code 0 and either EMIT in output or tx_hash in stdout
    success = (result.returncode == 0 and (
        'EMIT' in result.stdout or
        'tx=' in result.stdout or
        'Registry saved' in result.stdout
    ))
    if success:
        print('OK')
        ok += 1
    else:
        print(f'FAIL (rc={result.returncode})')
        if result.stderr:
            print(f'  stderr: {result.stderr[:200]}')
        fail += 1
    time.sleep(0.5)

print(f'\nDone: {ok} ok, {fail} failed of {len(remaining)} total')
