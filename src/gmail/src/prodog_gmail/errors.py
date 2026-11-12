import sqlite3


def database_busy(exc):
    return isinstance(exc, sqlite3.OperationalError) and (
        getattr(exc, "sqlite_errorcode", 0) & 0xFF
    ) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}


class ConnectorError(Exception):
    """Safe error with a fixed message; never include Google's response or a token."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status
