"""Create local settings without printing secrets or replacing an existing .env."""

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
    with target.open("x", encoding="utf-8", newline="\n") as file:
        file.write(content)
    print("Created .env with a new encryption key. No secrets printed.")
