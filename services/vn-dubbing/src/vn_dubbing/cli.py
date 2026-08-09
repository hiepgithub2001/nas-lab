from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import time
import uuid
from pathlib import Path

from .config import Profile, Settings
from .db import Database
from .discovery import discover_once
from .job_runner import execute_job
from .models import ConfigurationError, JobState, PipelineError
from .supervisor import healthcheck, run_supervisor, write_heartbeat
from .tts import create_backend
from .verification import verify_output


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="vn-dub")
    root.add_argument("--log-level", default=os.getenv("VN_DUB_LOG_LEVEL", "INFO"))
    commands = root.add_subparsers(dest="command", required=True)

    discover = commands.add_parser("discover", help="reconcile Radarr into SQLite")
    discover.add_argument("--once", action="store_true", help="compatibility flag")

    scheduler = commands.add_parser("scheduler", help="run hourly discovery")
    scheduler.add_argument("--interval", type=int)

    commands.add_parser("supervisor", help="lease jobs and spawn one model child")

    run = commands.add_parser("run", help="run one already discovered job")
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job")
    selection.add_argument("--movie-id", type=int)
    run.add_argument("--owner")

    smoke = commands.add_parser("smoke-test", help="load the pinned voice, write one WAV, exit")
    smoke.add_argument("--text", required=True)
    smoke.add_argument("--output", type=Path, required=True)
    smoke.add_argument("--engine", choices=("vieneu-v2", "voxcpm2"))
    smoke.add_argument("--temperature", type=float, help="Phase 0 VieNeu override")
    smoke.add_argument("--top-k", type=int, help="Phase 0 VieNeu override")

    status = commands.add_parser("status", help="show queue state")
    status.add_argument("--job")
    status.add_argument("--json", action="store_true")

    retry = commands.add_parser("retry", help="reset a failed job")
    retry.add_argument("--job", required=True)
    cancel = commands.add_parser("cancel", help="cooperatively cancel a job")
    cancel.add_argument("--job", required=True)
    stale = commands.add_parser("mark-stale", help="mark the latest movie job stale")
    stale.add_argument("--movie-id", type=int, required=True)

    verify = commands.add_parser("verify", help="verify a job's work AAC")
    verify.add_argument("--job", required=True)

    health = commands.add_parser("healthcheck", help="check SQLite and process heartbeat")
    health.add_argument("--role", choices=("scheduler", "supervisor"), required=True)
    health.add_argument("--max-age", type=int, default=180)
    commands.add_parser("init-db", help="initialize SQLite schema")
    return root


def _runtime() -> tuple[Settings, Profile, Database]:
    settings = Settings.from_env()
    settings.ensure_state_dirs()
    profile = Profile.load(settings.profile_path)
    database = Database(settings.db_path)
    database.migrate()
    return settings, profile, database


def _row_dict(row: object) -> dict[str, object]:
    return dict(row)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings, profile, database = _runtime()
        if args.command == "init-db":
            print(settings.db_path)
            return 0
        if args.command == "discover":
            print(json.dumps(discover_once(settings, profile, database), sort_keys=True))
            return 0
        if args.command == "scheduler":
            interval = args.interval or settings.scan_interval
            while True:
                try:
                    stats = discover_once(settings, profile, database)
                    logging.getLogger(__name__).info("discovery: %s", stats)
                    write_heartbeat(settings, "scheduler", stats=stats)
                except Exception:
                    logging.getLogger(__name__).exception("scheduled discovery failed")
                    write_heartbeat(settings, "scheduler", error="discovery failed")
                time.sleep(interval)
        if args.command == "supervisor":
            run_supervisor(settings, profile, database)
            return 0
        if args.command == "run":
            job = database.get_job(args.job) if args.job else database.latest_job_for_movie(args.movie_id)
            if job is None:
                raise ConfigurationError("job was not found; run discovery first")
            owner = args.owner or f"manual:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
            if job["state"] != JobState.RUNNING:
                if not database.lease_job(job["id"], owner, settings.lease_seconds, settings.max_attempts):
                    raise ConfigurationError(f"job {job['id']} is not leaseable")
            elif job["lease_owner"] != owner:
                raise ConfigurationError(f"job {job['id']} is leased by another worker")
            return execute_job(settings, profile, database, job["id"], owner)
        if args.command == "smoke-test":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.temperature is not None:
                profile.model["temperature"] = args.temperature
            if args.top_k is not None:
                profile.model["top_k"] = args.top_k
            backend = create_backend(settings, profile, args.engine)
            try:
                backend.synthesize(args.text, args.output, seed=42)
            finally:
                backend.close()
            print(args.output)
            return 0
        if args.command == "status":
            rows = [database.get_job(args.job)] if args.job else database.list_jobs()
            rows = [row for row in rows if row is not None]
            if args.json:
                print(json.dumps([_row_dict(row) for row in rows], ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(
                        f"{row['id']}  {row['state']:<18} "
                        f"{row['completed_cues']}/{row['cue_count']}  {row['title']}"
                    )
                if not rows:
                    print("No dubbing jobs.")
            return 0
        if args.command == "retry":
            return 0 if database.retry(args.job) else 2
        if args.command == "cancel":
            return 0 if database.request_cancel(args.job) else 2
        if args.command == "mark-stale":
            job = database.latest_job_for_movie(args.movie_id)
            if job is None:
                return 2
            database.set_job_state(job["id"], JobState.STALE)
            return 0
        if args.command == "verify":
            job = database.get_job(args.job)
            if job is None:
                return 2
            work = settings.jobs_dir / job["id"] / "voice-over.aac"
            from .audio import probe_movie

            result = verify_output(work, probe_movie(Path(job["video_path"])).duration_ms, profile.output)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "healthcheck":
            return 0 if healthcheck(settings, database, args.role, args.max_age) else 1
        return 2
    except PipelineError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
