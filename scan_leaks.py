#!/usr/bin/env python3
"""
scan_leaks.py — Run this on a cloned copy to verify no private data leaked.

Usage:
    python3 scan_leaks.py ~/zk-rag-public/
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Inline the leak patterns so this script is self-contained
import re

LEAK_PATTERNS = [
    (re.compile(r"DEPLOYER_KEY[=\s]+[a-f0-9]{40,64}"),          "DEPLOYER_KEY value"),
    (re.compile(r"KURIE_API_KEY[=\s]+[a-zA-Z0-9]{20,}"),         "KURIE_API_KEY value"),
    (re.compile(r"/home/blockops/"),                              "real username path"),
    (re.compile(r"/data/military-documents"),                     "real data path"),
    (re.compile(r"b28e65[a-f0-9]+"),                              "Kurier API key fragment"),
    (re.compile(r"blockops"),                                      "hostname 'blockops'"),
    (re.compile(r"10\.120\.60\.\d{1,3}"),                     "internal IP"),
    (re.compile(r"0x[a-f0-9]{40}\.[a-f0-9]"),                    "疑似私钥片段"),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", "target", "rust_out", ".venv", "venv", "scan_leaks.py"}
SCAN_EXTS = {".py", ".sh", ".md", ".yaml", ".yml", ".toml", ".conf", ".service", ".json", ".txt", ".html", ".js", ".css", ".js"}

def scan(root: Path):
    leaks = []
    for ext in SCAN_EXTS:
        for path in root.rglob(f"*{ext}"):
            if any(d in path.parts for d in SKIP_DIRS):
                continue
            if path.name == "scan_leaks.py":
                continue
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    for pat, label in LEAK_PATTERNS:
                        if pat.search(line):
                            leaks.append((str(path.relative_to(root)), i, label, line.strip()[:120]))
            except Exception:
                pass
    return leaks

if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    print(f"Scanning: {root}")
    leaks = scan(root)
    if leaks:
        print(f"\nWARNING: {len(leaks)} potential leaks found:\n")
        for filepath, lineno, label, text in leaks:
            print(f"  {filepath}:{lineno} [{label}]")
            print(f"    {text}")
        sys.exit(1)
    else:
        print("Clean — no leaks detected.")
        sys.exit(0)
