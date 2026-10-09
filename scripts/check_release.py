#!/usr/bin/env python3
"""Check publishable files without printing matched secret values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", "__pycache__", ".venv", "work", "dist"}
BAD_NAMES = {"auth.json", "models_cache.json", "config.toml", "installation.json"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "common API token": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "personal macOS path": re.compile(r"/Users/[A-Za-z0-9_.-]+/"),
    "source deployment host": re.compile(r"https?://[^\s/\"']*\.hailiangedu\.com"),
    "URL credentials": re.compile(r"https?://[^\s/\"']+:[^\s/@\"']+@"),
}


def files():
    if (ROOT / ".git").exists():
        listing = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode()
        return [ROOT / p for p in listing.split("\0") if p]
    return [p for p in ROOT.rglob("*") if p.is_file() and not set(p.relative_to(ROOT).parts) & SKIP
            and p.suffix not in (".pyc", ".pyo")]


def main():
    failures = []
    scanned = files()
    for path in scanned:
        name = str(path.relative_to(ROOT))
        if path.is_symlink():
            failures.append((name, "symbolic link"))
            continue
        if path.name in BAD_NAMES or path.name.endswith((".local.json", ".secret", ".log", ".jsonl", ".zip")) or path.name.startswith(".env"):
            failures.append((name, "runtime/private file"))
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            failures.append((name, "unexpected binary file"))
            continue
        for label, pattern in PATTERNS.items():
            # Negative URL validation fixtures intentionally contain dummy credentials.
            if name == "tests/test_http.py" and label == "URL credentials":
                continue
            if pattern.search(content):
                failures.append((name, label))
    for name, label in failures:
        print(f"FAIL {name}: {label}", file=sys.stderr)
    print(f"Scanned {len(scanned)} publishable files; {len(failures)} findings.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
