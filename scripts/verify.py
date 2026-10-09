#!/usr/bin/env python3
"""Run all offline suites in separate processes, keeping module names isolated."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
for directory in (root, root / "skills/multi-model-workflow"):
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=directory)
    if result.returncode:
        raise SystemExit(result.returncode)
