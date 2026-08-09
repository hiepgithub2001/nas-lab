from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import uuid

from .config import Profile, Settings
from .db import Database


LOGGER = logging.getLogger(__name__)


def write_heartbeat(settings: Settings, role: str, **details: object) -> None:
    settings.health_dir.mkdir(parents=True, exist_ok=True)
    path = settings.health_dir / f"{role}.json"
    temporary = path.with_name(path.name + ".partial")
    payload = {"role": role, "timestamp": int(time.time()), **details}
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_supervisor(settings: Settings, profile: Profile, database: Database) -> None:
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    LOGGER.info("supervisor started as %s", owner)
    while True:
        write_heartbeat(settings, "supervisor", owner=owner)
        job = database.lease_next(owner, settings.lease_seconds, settings.max_attempts)
        if job is None:
            time.sleep(settings.supervisor_poll_interval)
            continue
        LOGGER.info("starting child for job %s", job["id"])
        child = subprocess.Popen(
            [sys.executable, "-m", "vn_dubbing.cli", "run", "--job", job["id"], "--owner", owner],
        )
        while child.poll() is None:
            write_heartbeat(settings, "supervisor", owner=owner, active_job=job["id"])
            time.sleep(min(settings.supervisor_poll_interval, 30))
        LOGGER.info("child for job %s exited %s", job["id"], child.returncode)


def healthcheck(settings: Settings, database: Database, role: str, max_age: int = 180) -> bool:
    try:
        with database.connect() as connection:
            connection.execute("SELECT 1").fetchone()
        path = settings.health_dir / f"{role}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return int(time.time()) - int(payload["timestamp"]) <= max_age
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False
