"""Run the local server without a console and keep startup errors on disk."""

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    log_dir = root / "artifacts"
    log_dir.mkdir(exist_ok=True)
    log = (log_dir / "server.background.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log

    import uvicorn

    uvicorn.run(
        "prodog_gmail.app:app",
        host="127.0.0.1",
        port=8765,
        access_log=False,
    )


if __name__ == "__main__":
    main()
