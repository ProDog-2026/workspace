"""Import a downloaded Web OAuth client locally; never print client secrets."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def configure(credentials_path: Path, root: Path = ROOT):
    env_path = root / ".env"
    if not env_path.exists():
        raise ValueError("Run scripts/setup_local.py first.")
    if credentials_path.stat().st_size > 65536:
        raise ValueError("OAuth client JSON is too large.")
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8-sig"))
        web = data["web"]
        client_id, client_secret = web["client_id"], web["client_secret"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Select a downloaded Web application OAuth client JSON.") from exc
    for value in (client_id, client_secret):
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or "\n" in value
            or "\r" in value
        ):
            raise ValueError("The OAuth client JSON has an invalid client field.")
    config = dotenv_values(env_path)
    if not config.get("TOKEN_ENCRYPTION_KEY"):
        raise ValueError("The existing .env needs a token encryption key.")
    redirect = (
        config.get("APP_BASE_URL", "http://127.0.0.1:8765").rstrip("/") + "/oauth/google/callback"
    )
    if redirect not in web.get("redirect_uris", []):
        raise ValueError(
            "Register the local callback URI in this Web client, then download its JSON again."
        )
    # Preserve a prior local configuration without printing or uploading it.
    backup = root / ".env.before-google.bak"
    if not backup.exists():
        with backup.open("xb") as output:
            output.write(env_path.read_bytes())
    set_key(env_path, "GOOGLE_CLIENT_ID", client_id)
    set_key(env_path, "GOOGLE_CLIENT_SECRET", client_secret)
    set_key(env_path, "APP_MODE", "google")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("credentials", type=Path, help="Path to the downloaded Web client JSON")
    args = parser.parse_args()
    try:
        configure(args.credentials)
    except OSError:
        parser.exit(1, "Could not read/write the local configuration files.\n")
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    print("Google settings imported into local .env. Restart the server. No secrets printed.")


if __name__ == "__main__":
    main()
