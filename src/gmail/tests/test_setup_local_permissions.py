import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def test_new_secret_file_is_private_and_existing_configuration_is_preserved(tmp_path):
    root = tmp_path / "gmail"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "setup_local.py"
    shutil.copy2(Path(__file__).parents[1] / "scripts/setup_local.py", script)
    (root / ".env.example").write_text("TOKEN_ENCRYPTION_KEY=\nAPP_MODE=demo\n")
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    )
    target = root / ".env"
    first = target.read_bytes()
    key = next(
        line.split("=", 1)[1]
        for line in first.decode().splitlines()
        if line.startswith("TOKEN_ENCRYPTION_KEY=")
    )
    assert key not in result.stdout + result.stderr
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    subprocess.run([sys.executable, str(script)], capture_output=True, check=True)
    assert target.read_bytes() == first
