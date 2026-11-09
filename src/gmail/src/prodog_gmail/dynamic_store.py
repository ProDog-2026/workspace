"""Durable ownership and deduplication. No email bodies, summaries or event payloads."""

import json
import time
import uuid


class DynamicStore:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS dynamic_settings (
                    owner TEXT PRIMARY KEY, settings_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dynamic_jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    dedupe_key TEXT, run_id TEXT UNIQUE,
                    status TEXT NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, data_json TEXT NOT NULL,
                    UNIQUE(owner, dedupe_key)
                );
                CREATE INDEX IF NOT EXISTS dynamic_jobs_owner ON dynamic_jobs(owner, created_at);
                CREATE TABLE IF NOT EXISTS dynamic_prompt_owners (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL
                );
            """)

    def settings(self, owner):
        with self.store.connection() as db:
            row = db.execute(
                "SELECT settings_json FROM dynamic_settings WHERE owner=?", (owner,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_settings(self, owner, settings):
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO dynamic_settings VALUES (?, ?) ON CONFLICT(owner) "
                "DO UPDATE SET settings_json=excluded.settings_json",
                (owner, json.dumps(settings)),
            )

    @staticmethod
    def unpack(row):
        if row is None:
            return None
        result = json.loads(row["data_json"])
        result.update(
            {k: row[k] for k in ("id", "owner", "run_id", "status", "created_at", "updated_at")}
        )
        return result

    def get(self, job_id, owner=None):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM dynamic_jobs WHERE id=?", (job_id,)).fetchone()
        return self.unpack(row) if row and (owner is None or row["owner"] == owner) else None

    def listing(self, owner=None):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM dynamic_jobs"
                + (" WHERE owner=?" if owner else "")
                + " ORDER BY created_at DESC",
                (owner,) if owner else (),
            ).fetchall()
        return [self.unpack(row) for row in rows]

    def insert(self, owner, data, dedupe_key=None, run_id=None):
        now, job_id = time.time(), uuid.uuid4().hex
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if dedupe_key:
                row = db.execute(
                    "SELECT * FROM dynamic_jobs WHERE owner=? AND dedupe_key=?", (owner, dedupe_key)
                ).fetchone()
                if row:
                    return self.unpack(row), False
            db.execute(
                "INSERT INTO dynamic_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    owner,
                    dedupe_key,
                    run_id,
                    "running" if run_id else "queued",
                    now,
                    now,
                    json.dumps(data, ensure_ascii=False),
                ),
            )
        return self.get(job_id), True

    def update(self, job_id, **changes):
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM dynamic_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            data = json.loads(row["data_json"])
            status = changes.pop("status", row["status"])
            run_id = changes.pop("run_id", row["run_id"])
            data.update(changes)
            db.execute(
                "UPDATE dynamic_jobs SET status=?, run_id=?, updated_at=?, data_json=? WHERE id=?",
                (status, run_id, time.time(), json.dumps(data, ensure_ascii=False), job_id),
            )

    @staticmethod
    def public(job):
        if not job:
            return None
        result = {
            k: v
            for k, v in job.items()
            if k not in {"owner", "options", "prompt_hash", "mode", "key", "base_key"}
        }
        result["configuration"] = job.get("options")
        return result
