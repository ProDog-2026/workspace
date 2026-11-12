"""Metadata/result outbox only; encrypted mail inputs/logs belong to the local pipeline."""

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from collections import defaultdict

from .errors import ConnectorError

logger = logging.getLogger(__name__)
WORKER_ERROR = "클라우드 동기화 오류. 서버가 자동으로 다시 시도합니다."


class CloudSync:
    def __init__(self, store, sink):
        self.store, self.sink = store, sink
        self.worker = None
        self.worker_error = None
        self.changed = asyncio.Event()
        self.locks = defaultdict(asyncio.Lock)
        with store.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cloud_outbox (
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    last_error TEXT, PRIMARY KEY(account_id,message_id)
                );
                CREATE TABLE IF NOT EXISTS cloud_deliveries (
                    job_id TEXT PRIMARY KEY REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                    data_json TEXT NOT NULL
                );
            """)

    def mark(self, account_id, message_id, *, conn=None):
        transaction = contextlib.nullcontext(conn) if conn else self.store.connection()
        with transaction as conn:
            conn.execute(
                "INSERT INTO cloud_outbox(account_id,message_id) "
                "SELECT ?,? WHERE EXISTS(SELECT 1 FROM account_owners WHERE account_id=?) "
                "AND NOT EXISTS(SELECT 1 FROM account_deletions WHERE account_id=?) "
                "ON CONFLICT(account_id,message_id) DO UPDATE SET "
                "revision=revision+1,next_attempt=0",
                (account_id, message_id, account_id, account_id),
            )
        self.changed.set()

    def status(self, account_id):
        try:
            if self.store.deletion_pending(account_id):
                return {
                    "configured": self.sink is not None,
                    "pending": None,
                    "error": "연결 해제가 끝나지 않았습니다. 연동 해제를 다시 시도하세요.",
                }
            with self.store.connection() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS pending,MAX(last_error) AS error "
                    "FROM cloud_outbox WHERE account_id=?",
                    (account_id,),
                ).fetchone()
        except sqlite3.Error:
            return {"configured": self.sink is not None, "pending": None, "error": WORKER_ERROR}
        return {
            "configured": self.sink is not None,
            "pending": row["pending"],
            "error": self.worker_error or row["error"],
        }

    async def flush_once(self):
        if self.sink is None:
            return False
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM cloud_outbox WHERE next_attempt<=? "
                "AND NOT EXISTS(SELECT 1 FROM account_deletions d "
                "WHERE d.account_id=cloud_outbox.account_id) ORDER BY next_attempt LIMIT 1",
                (time.time(),),
            ).fetchone()
        if not row:
            return False
        account_id, message_id = row["account_id"], row["message_id"]
        async with self.locks[account_id]:
            if self.store.deletion_pending(account_id):
                return False
            with self.store.connection() as conn:
                account = conn.execute(
                    "SELECT a.email,o.user_id FROM accounts a JOIN account_owners o "
                    "ON o.account_id=a.id WHERE a.id=?",
                    (account_id,),
                ).fetchone()
                mail = conn.execute(
                    "SELECT metadata_json FROM mails WHERE account_id=? AND gmail_message_id=?",
                    (account_id, message_id),
                ).fetchone()
                analyses = conn.execute(
                    "SELECT j.id,j.data_json FROM analysis_jobs j "
                    "LEFT JOIN cloud_deliveries d ON d.job_id=j.id "
                    "WHERE j.account_id=? AND j.message_id=? "
                    "AND (d.job_id IS NULL OR d.data_json<>j.data_json) "
                    "ORDER BY j.created_at,j.id",
                    (account_id, message_id),
                ).fetchall()
                live = conn.execute(
                    "SELECT 1 FROM cloud_outbox WHERE account_id=? AND message_id=?",
                    (account_id, message_id),
                ).fetchone()
            if not account or not live:
                return True
            try:
                if mail:
                    for analysis in analyses or [None]:
                        await self.sink.save_page(
                            account["email"],
                            [json.loads(mail[0])],
                            user_id=account["user_id"],
                            analyses={message_id: json.loads(analysis["data_json"])}
                            if analysis
                            else {},
                        )
                        if analysis:
                            with self.store.connection() as conn:
                                conn.execute(
                                    "INSERT INTO cloud_deliveries VALUES (?,?) "
                                    "ON CONFLICT(job_id) DO UPDATE SET "
                                    "data_json=excluded.data_json",
                                    (analysis["id"], analysis["data_json"]),
                                )
                with self.store.connection() as conn:
                    conn.execute(
                        "DELETE FROM cloud_outbox WHERE account_id=? AND message_id=? "
                        "AND revision=?",
                        (account_id, message_id, row["revision"]),
                    )
            except (ConnectorError, ValueError, KeyError, TypeError):
                with self.store.connection() as conn:
                    conn.execute(
                        "UPDATE cloud_outbox SET attempts=attempts+1,next_attempt=?,last_error=? "
                        "WHERE account_id=? AND message_id=? AND revision=?",
                        (
                            time.time() + min(60, 2 ** min(row["attempts"] + 1, 6)),
                            "Supabase 저장 실패. 서버가 자동으로 다시 시도합니다.",
                            account_id,
                            message_id,
                            row["revision"],
                        ),
                    )
        return True

    async def open(self):
        if self.worker and not self.worker.done():
            return

        async def work():
            while True:
                try:
                    self.changed.clear()
                    flushed = await self.flush_once()
                    self.worker_error = None
                    if flushed:
                        continue
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.changed.wait(), timeout=1)
                except Exception:
                    self.worker_error = WORKER_ERROR
                    logger.error(WORKER_ERROR)
                    await asyncio.sleep(1)

        self.worker = asyncio.create_task(work())

    async def close(self):
        if self.worker:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
