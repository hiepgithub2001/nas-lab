from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .models import JobState, LEASEABLE_STATES, SubtitleCue


SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    identity_hash TEXT NOT NULL UNIQUE,
                    radarr_movie_id INTEGER NOT NULL,
                    radarr_movie_file_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    subtitle_path TEXT NOT NULL,
                    subtitle_sha256 TEXT NOT NULL,
                    profile_sha256 TEXT NOT NULL,
                    engine TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at INTEGER,
                    cue_count INTEGER NOT NULL DEFAULT 0,
                    completed_cues INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT,
                    last_error TEXT,
                    output_path TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER
                );

                CREATE UNIQUE INDEX IF NOT EXISTS jobs_identity_hash
                    ON jobs(identity_hash);
                CREATE INDEX IF NOT EXISTS jobs_state_created
                    ON jobs(state, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS jobs_radarr_movie
                    ON jobs(radarr_movie_id);

                CREATE TABLE IF NOT EXISTS cues (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    cue_index INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    source_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    raw_audio_path TEXT,
                    fitted_audio_path TEXT,
                    raw_duration_ms INTEGER,
                    fitted_duration_ms INTEGER,
                    tempo REAL,
                    warning_code TEXT,
                    artifact_sha256 TEXT,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(job_id, cue_index)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discoveries (
                    radarr_movie_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            if row is None:
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {row['version']} is not supported; expected {SCHEMA_VERSION}"
                )

    def record_event(
        self, connection: sqlite3.Connection, job_id: str | None, event_type: str, **details: Any
    ) -> None:
        connection.execute(
            "INSERT INTO events(job_id,event_type,details_json,created_at) VALUES (?,?,?,?)",
            (job_id, event_type, json.dumps(details, ensure_ascii=False, sort_keys=True), int(time.time())),
        )

    def upsert_discovery(self, movie_id: int, title: str, state: str, detail: str | None = None) -> None:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO discoveries(radarr_movie_id,title,state,detail,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(radarr_movie_id) DO UPDATE SET
                     title=excluded.title,state=excluded.state,detail=excluded.detail,
                     updated_at=excluded.updated_at""",
                (movie_id, title, state, detail, now),
            )

    def insert_job(self, values: dict[str, Any]) -> tuple[str, bool]:
        now = int(time.time())
        job_id = str(uuid.uuid4())
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO jobs(
                     id,identity_hash,radarr_movie_id,radarr_movie_file_id,title,
                     video_path,subtitle_path,subtitle_sha256,profile_sha256,engine,state,
                     created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    values["identity_hash"],
                    values["radarr_movie_id"],
                    values["radarr_movie_file_id"],
                    values["title"],
                    values["video_path"],
                    values["subtitle_path"],
                    values["subtitle_sha256"],
                    values["profile_sha256"],
                    values["engine"],
                    JobState.PENDING,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            if created:
                self.record_event(connection, job_id, "job_discovered")
            else:
                row = connection.execute(
                    "SELECT id FROM jobs WHERE identity_hash=?", (values["identity_hash"],)
                ).fetchone()
                job_id = row["id"]
            return job_id, created

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def latest_job_for_movie(self, movie_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM jobs WHERE radarr_movie_id=? ORDER BY created_at DESC LIMIT 1",
                (movie_id,),
            ).fetchone()

    def list_jobs(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            )

    def lease_next(self, owner: str, lease_seconds: int, max_attempts: int) -> sqlite3.Row | None:
        now = int(time.time())
        states = tuple(str(state) for state in LEASEABLE_STATES)
        placeholders = ",".join("?" for _ in states)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""SELECT * FROM jobs
                    WHERE attempts < ? AND next_attempt_at <= ? AND (
                      state IN ({placeholders}) OR
                      (state=? AND lease_expires_at < ?)
                    )
                    ORDER BY created_at LIMIT 1""",
                (max_attempts, now, *states, JobState.RUNNING, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE jobs SET state=?,attempts=attempts+1,lease_owner=?,
                   lease_expires_at=?,updated_at=?,last_error_code=NULL,last_error=NULL
                   WHERE id=?""",
                (JobState.RUNNING, owner, now + lease_seconds, now, row["id"]),
            )
            self.record_event(connection, row["id"], "job_leased", owner=owner)
            return connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def lease_job(self, job_id: str, owner: str, lease_seconds: int, max_attempts: int) -> bool:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["attempts"] >= max_attempts:
                return False
            if row["state"] == JobState.RUNNING and row["lease_expires_at"] >= now:
                return row["lease_owner"] == owner
            if row["state"] not in {str(s) for s in LEASEABLE_STATES} | {JobState.RUNNING}:
                return False
            connection.execute(
                """UPDATE jobs SET state=?,attempts=attempts+1,lease_owner=?,
                   lease_expires_at=?,updated_at=? WHERE id=?""",
                (JobState.RUNNING, owner, now + lease_seconds, now, job_id),
            )
            self.record_event(connection, job_id, "job_leased", owner=owner)
            return True

    def heartbeat(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        now = int(time.time())
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET lease_expires_at=?,updated_at=?
                   WHERE id=? AND state=? AND lease_owner=?""",
                (now + lease_seconds, now, job_id, JobState.RUNNING, owner),
            )
            return cursor.rowcount == 1

    def set_job_state(
        self,
        job_id: str,
        state: JobState,
        *,
        error_code: str | None = None,
        error: str | None = None,
        output_path: str | None = None,
        retry_after: int = 0,
    ) -> None:
        now = int(time.time())
        completed_at = now if state == JobState.COMPLETED else None
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET state=?,last_error_code=?,last_error=?,output_path=COALESCE(?,output_path),
                   next_attempt_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?,completed_at=?
                   WHERE id=?""",
                (
                    state,
                    error_code,
                    error,
                    output_path,
                    now + retry_after,
                    now,
                    completed_at,
                    job_id,
                ),
            )
            self.record_event(
                connection, job_id, "job_state", state=state, error_code=error_code, error=error
            )

    def defer_job(self, job_id: str, error_code: str, error: str, retry_after: int) -> None:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """UPDATE jobs SET state=?,attempts=CASE WHEN attempts>0 THEN attempts-1 ELSE 0 END,
                   last_error_code=?,last_error=?,next_attempt_at=?,lease_owner=NULL,
                   lease_expires_at=NULL,updated_at=? WHERE id=?""",
                (
                    JobState.WAITING_RESOURCES,
                    error_code,
                    error,
                    now + retry_after,
                    now,
                    job_id,
                ),
            )
            self.record_event(
                connection, job_id, "job_deferred", error_code=error_code, error=error
            )

    def request_cancel(self, job_id: str) -> bool:
        now = int(time.time())
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET state=?,updated_at=?
                   WHERE id=? AND state IN (?,?,?,?)""",
                (
                    JobState.CANCEL_REQUESTED,
                    now,
                    job_id,
                    JobState.PENDING,
                    JobState.WAITING_RESOURCES,
                    JobState.RETRYABLE_FAILED,
                    JobState.RUNNING,
                ),
            )
            if cursor.rowcount:
                self.record_event(connection, job_id, "cancel_requested")
            return cursor.rowcount == 1

    def retry(self, job_id: str) -> bool:
        now = int(time.time())
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET state=?,attempts=0,next_attempt_at=0,
                   last_error_code=NULL,last_error=NULL,updated_at=?
                   WHERE id=? AND state IN (?,?,?)""",
                (
                    JobState.PENDING,
                    now,
                    job_id,
                    JobState.RETRYABLE_FAILED,
                    JobState.PERMANENT_FAILED,
                    JobState.NEEDS_REVIEW,
                ),
            )
            if cursor.rowcount:
                self.record_event(connection, job_id, "operator_retry")
            return cursor.rowcount == 1

    def mark_superseded_jobs(self, movie_id: int, current_file_id: int) -> int:
        now = int(time.time())
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET state=?,updated_at=?
                   WHERE radarr_movie_id=? AND radarr_movie_file_id<>?
                     AND state IN (?,?,?)""",
                (
                    JobState.SUPERSEDED,
                    now,
                    movie_id,
                    current_file_id,
                    JobState.PENDING,
                    JobState.WAITING_RESOURCES,
                    JobState.RETRYABLE_FAILED,
                ),
            )
            return cursor.rowcount

    def cancel_untagged_pending(self, tagged_movie_ids: set[int]) -> int:
        now = int(time.time())
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,radarr_movie_id FROM jobs WHERE state IN (?,?,?)",
                (JobState.PENDING, JobState.WAITING_RESOURCES, JobState.RETRYABLE_FAILED),
            ).fetchall()
            ids = [row["id"] for row in rows if row["radarr_movie_id"] not in tagged_movie_ids]
            for job_id in ids:
                connection.execute(
                    "UPDATE jobs SET state=?,updated_at=? WHERE id=?",
                    (JobState.CANCELLED, now, job_id),
                )
                self.record_event(connection, job_id, "tag_removed")
            return len(ids)

    def upsert_cues(self, job_id: str, cues: Iterable[SubtitleCue]) -> None:
        import hashlib

        now = int(time.time())
        rows = list(cues)
        with self.connect() as connection:
            for cue in rows:
                text_hash = hashlib.sha256(cue.normalized_text.encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO cues(job_id,cue_index,start_ms,end_ms,source_text,
                       normalized_text,text_sha256,updated_at)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(job_id,cue_index) DO UPDATE SET
                         start_ms=excluded.start_ms,end_ms=excluded.end_ms,
                         source_text=excluded.source_text,normalized_text=excluded.normalized_text,
                         text_sha256=excluded.text_sha256,updated_at=excluded.updated_at
                       WHERE cues.state<>'completed'""",
                    (
                        job_id,
                        cue.index,
                        cue.start_ms,
                        cue.end_ms,
                        cue.source_text,
                        cue.normalized_text,
                        text_hash,
                        now,
                    ),
                )
            connection.execute(
                "UPDATE jobs SET cue_count=?,updated_at=? WHERE id=?", (len(rows), now, job_id)
            )

    def cues_for_job(self, job_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    "SELECT * FROM cues WHERE job_id=? ORDER BY cue_index", (job_id,)
                ).fetchall()
            )

    def complete_cue(self, job_id: str, cue_index: int, **values: Any) -> None:
        now = int(time.time())
        with self.connect() as connection:
            connection.execute(
                """UPDATE cues SET state='completed',attempts=?,
                   raw_audio_path=?,fitted_audio_path=?,raw_duration_ms=?,
                   fitted_duration_ms=?,tempo=?,warning_code=?,artifact_sha256=?,updated_at=?
                   WHERE job_id=? AND cue_index=?""",
                (
                    values.get("synthesis_attempts", 1),
                    values["raw_audio_path"],
                    values["fitted_audio_path"],
                    values["raw_duration_ms"],
                    values["fitted_duration_ms"],
                    values["tempo"],
                    values.get("warning_code"),
                    values["artifact_sha256"],
                    now,
                    job_id,
                    cue_index,
                ),
            )
            completed = connection.execute(
                "SELECT count(*) AS n FROM cues WHERE job_id=? AND state='completed'", (job_id,)
            ).fetchone()["n"]
            connection.execute(
                "UPDATE jobs SET completed_cues=?,updated_at=? WHERE id=?",
                (completed, now, job_id),
            )

    def cue_artifact_valid(self, row: sqlite3.Row) -> bool:
        if row["state"] != "completed" or not row["artifact_sha256"]:
            return False
        path = Path(row["fitted_audio_path"] or "")
        if not path.is_file():
            return False
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == row["artifact_sha256"]
