import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken

from .config import Settings
from .errors import ConnectorError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    """Single-machine prototype store. Body text is deliberately absent from the schema."""

    def __init__(self, settings: Settings):
        self.path = settings.database_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = settings.session_ttl_seconds
        self.key = settings.token_encryption_key.get_secret_value().encode("ascii")
        self.cipher = Fernet(self.key)
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL, mode TEXT NOT NULL,
                    UNIQUE(email, mode)
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    encrypted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id_hash TEXT PRIMARY KEY,
                    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_flows (
                    state_hash TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL REFERENCES sessions(id_hash) ON DELETE CASCADE,
                    verifier_encrypted TEXT NOT NULL, expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mails (
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    gmail_message_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    updated_at REAL NOT NULL, PRIMARY KEY(account_id, gmail_message_id)
                );
            """)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def new_session(self, account_id: str | None = None, old: str | None = None) -> str:
        secret = secrets.token_urlsafe(32)
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            conn.execute("DELETE FROM oauth_flows WHERE expires_at < ?", (time.time(),))
            if old:
                conn.execute("DELETE FROM sessions WHERE id_hash = ?", (digest(old),))
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (digest(secret), account_id, time.time() + self.ttl),
            )
        return secret

    def session(self, secret: str | None):
        if not secret or len(secret) > 100:
            return None
        with self.connection() as conn:
            row = conn.execute(
                "SELECT s.*, a.email, a.mode FROM sessions s "
                "LEFT JOIN accounts a ON a.id=s.account_id "
                "WHERE s.id_hash=? AND s.expires_at > ?",
                (digest(secret), time.time()),
            ).fetchone()
            return dict(row) if row else None

    def csrf(self, secret: str) -> str:
        return hmac.new(self.key, ("csrf:" + secret).encode(), hashlib.sha256).hexdigest()

    def delete_session(self, secret: str):
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id_hash=?", (digest(secret),))

    def start_flow(self, secret: str) -> tuple[str, str]:
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        encrypted = self.cipher.encrypt(verifier.encode()).decode()
        with self.connection() as conn:
            conn.execute("DELETE FROM oauth_flows WHERE session_hash=?", (digest(secret),))
            conn.execute(
                "INSERT INTO oauth_flows VALUES (?, ?, ?, ?)",
                (digest(state), digest(secret), encrypted, time.time() + 600),
            )
        return state, verifier

    def consume_flow(self, secret: str, state: str) -> str:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM oauth_flows WHERE state_hash=? AND session_hash=?",
                (digest(state), digest(secret)),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM oauth_flows WHERE state_hash=?", (digest(state),))
        if not row or row["expires_at"] < time.time():
            raise ConnectorError(
                "invalid_state", "연결 요청이 만료되었거나 유효하지 않습니다.", 400
            )
        try:
            return self.cipher.decrypt(row["verifier_encrypted"].encode(), ttl=600).decode()
        except InvalidToken as exc:
            raise ConnectorError("invalid_state", "새 연결 요청으로 다시 시도해 주세요.") from exc

    def account(self, email: str, mode: str) -> str:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?)",
                (str(uuid.uuid4()), email.casefold(), mode),
            )
            return conn.execute(
                "SELECT id FROM accounts WHERE email=? AND mode=?", (email.casefold(), mode)
            ).fetchone()["id"]

    def save_tokens(self, account_id: str, bundle: dict):
        encrypted = self.cipher.encrypt(
            json.dumps({"account_id": account_id, "bundle": bundle}).encode()
        ).decode()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_account(conn, account_id)
            conn.execute(
                "INSERT INTO tokens VALUES (?, ?) ON CONFLICT(account_id) "
                "DO UPDATE SET encrypted=excluded.encrypted",
                (account_id, encrypted),
            )

    def tokens(self, account_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT encrypted FROM tokens WHERE account_id=?", (account_id,)
            ).fetchone()
        if not row:
            return None
        return self._decrypt_tokens(row["encrypted"], account_id)

    def _decrypt_tokens(self, encrypted: str, account_id: str) -> dict:
        try:
            payload = json.loads(self.cipher.decrypt(encrypted.encode()))
            if payload["account_id"] != account_id:
                raise ValueError("account binding")
            return payload["bundle"]
        except (InvalidToken, ValueError, KeyError) as exc:
            raise ConnectorError(
                "token_store_error",
                "저장된 연결 정보를 읽을 수 없습니다. 암호화 키를 확인하세요.",
                503,
            ) from exc

    def has_tokens(self, account_id: str) -> bool:
        with self.connection() as conn:
            return (
                conn.execute("SELECT 1 FROM tokens WHERE account_id=?", (account_id,)).fetchone()
                is not None
            )

    def invalidate_tokens(self, account_id: str):
        # Retain an unusable grant only for explicit revocation/cleanup. Never retry
        # it on reads. A successful new OAuth exchange replaces the entire bundle.
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT encrypted FROM tokens WHERE account_id=?", (account_id,)
            ).fetchone()
            if not row:
                return
            bundle = self._decrypt_tokens(row["encrypted"], account_id)
            bundle["needs_reconnect"] = True
            encrypted = self.cipher.encrypt(
                json.dumps({"account_id": account_id, "bundle": bundle}).encode()
            ).decode()
            conn.execute(
                "UPDATE tokens SET encrypted=? WHERE account_id=?", (encrypted, account_id)
            )

    @staticmethod
    def _require_account(conn, account_id: str):
        if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone():
            raise ConnectorError(
                "authentication_required", "연결이 해제되었습니다. 다시 연결해 주세요.", 401
            )

    def complete_oauth(self, secret: str, email: str, response: dict, token_builder) -> str:
        """Commit login only if the initiating session still exists after network I/O.

        Logout/disconnect and this transaction serialize, preventing a late callback
        from restoring a session that the user has already ended.
        """
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT 1 FROM sessions WHERE id_hash=? AND expires_at>?",
                (digest(secret), time.time()),
            ).fetchone()
            if not current:
                raise ConnectorError("invalid_state", "연결 요청이 취소되었거나 만료되었습니다.")
            row = conn.execute(
                "SELECT id FROM accounts WHERE email=? AND mode='google'", (email.casefold(),)
            ).fetchone()
            account_id = row["id"] if row else str(uuid.uuid4())
            saved = conn.execute(
                "SELECT encrypted FROM tokens WHERE account_id=?", (account_id,)
            ).fetchone()
            previous = self._decrypt_tokens(saved["encrypted"], account_id) if saved else None
            bundle = token_builder(response, previous)
            encrypted = self.cipher.encrypt(
                json.dumps({"account_id": account_id, "bundle": bundle}).encode()
            ).decode()
            if not row:
                conn.execute(
                    "INSERT INTO accounts VALUES (?, ?, 'google')", (account_id, email.casefold())
                )
            conn.execute(
                "INSERT INTO tokens VALUES (?, ?) ON CONFLICT(account_id) "
                "DO UPDATE SET encrypted=excluded.encrypted",
                (account_id, encrypted),
            )
            new = secrets.token_urlsafe(32)
            conn.execute("DELETE FROM sessions WHERE id_hash=?", (digest(secret),))
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (digest(new), account_id, time.time() + self.ttl),
            )
            return new

    def save_metadata(self, account_id: str, metadata: dict):
        # Explicit allowlist prevents accidental future persistence of raw/analysis body fields.
        keys = {
            "id",
            "thread_id",
            "subject",
            "sender",
            "received_at",
            "labels",
            "body_hash",
            "trust",
            "warnings",
            "truncated",
            "attachments",
        }
        safe = {k: v for k, v in metadata.items() if k in keys}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_account(conn, account_id)
            conn.execute(
                "INSERT INTO mails VALUES (?, ?, ?, ?) "
                "ON CONFLICT(account_id, gmail_message_id) DO UPDATE SET "
                "metadata_json=excluded.metadata_json, updated_at=excluded.updated_at",
                (account_id, safe["id"], json.dumps(safe, ensure_ascii=False), time.time()),
            )

    def forget_account(self, account_id: str):
        with self.connection() as conn:
            conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
