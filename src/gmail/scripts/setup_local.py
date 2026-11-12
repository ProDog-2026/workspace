"""Create local settings without printing secrets or replacing an existing .env."""

import os
from pathlib import Path

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    print("Existing .env preserved. Edit it locally if Google settings need to change.")
else:
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace(
        "TOKEN_ENCRYPTION_KEY=", "TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode()
    )
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    print("Created .env with a new encryption key. No secrets printed.")
