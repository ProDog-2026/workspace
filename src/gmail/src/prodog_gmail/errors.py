class ConnectorError(Exception):
    """Safe error with a fixed message; never include Google's response or a token."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status
