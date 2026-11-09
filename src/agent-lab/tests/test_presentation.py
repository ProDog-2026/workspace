import shutil
import subprocess
from pathlib import Path

import pytest


def test_dashboard_presentation_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the dashboard presentation tests")
    root = Path(__file__).resolve().parents[1]
    version = subprocess.run([node, "-p", "process.versions.node"],
                             capture_output=True, text=True, timeout=10)
    if version.returncode != 0 or int(version.stdout.split(".")[0]) < 18:
        pytest.skip("Node.js 18 or later is required for dashboard presentation tests")
    result = subprocess.run([node, "--test", str(root / "tests/dashboard_presentation.test.cjs")],
                            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr

