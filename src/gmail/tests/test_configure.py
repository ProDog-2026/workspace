import importlib.util
import json
from pathlib import Path

import pytest
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location(
    "configure_google", Path(__file__).parents[1] / "scripts" / "configure_google.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def client_file(tmp_path, **overrides):
    web = {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "redirect_uris": ["http://127.0.0.1:8765/oauth/google/callback"],
    }
    web.update(overrides)
    path = tmp_path / "synthetic-client.json"
    path.write_text(json.dumps({"web": web}), encoding="utf-8")
    return path


def test_import_preserves_key_and_existing_settings(tmp_path):
    env = tmp_path / ".env"
    original = "APP_MODE=demo\nTOKEN_ENCRYPTION_KEY=original-key\nCUSTOM=retained\n"
    env.write_text(original, encoding="utf-8")
    module.configure(client_file(tmp_path), tmp_path)
    values = dotenv_values(env)
    assert values["APP_MODE"] == "google"
    assert values["GOOGLE_CLIENT_SECRET"] == "test-secret"
    assert values["TOKEN_ENCRYPTION_KEY"] == "original-key"
    assert values["CUSTOM"] == "retained"
    assert (tmp_path / ".env.before-google.bak").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("overrides", [{"redirect_uris": []}, {"client_secret": "secret\nbad"}])
def test_invalid_import_keeps_env_unchanged_and_error_safe(tmp_path, overrides):
    env = tmp_path / ".env"
    original = "TOKEN_ENCRYPTION_KEY=original-key\n"
    env.write_text(original, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        module.configure(client_file(tmp_path, **overrides), tmp_path)
    assert env.read_text(encoding="utf-8") == original
    assert "secret" not in str(caught.value)


def test_installed_client_is_rejected(tmp_path):
    (tmp_path / ".env").write_text("TOKEN_ENCRYPTION_KEY=original-key\n", encoding="utf-8")
    path = tmp_path / "client.json"
    path.write_text('{"installed":{"client_secret":"test-secret"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="Web application"):
        module.configure(path, tmp_path)
