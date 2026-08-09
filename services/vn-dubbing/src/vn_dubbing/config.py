from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import ConfigurationError


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    media_root: Path
    profile_path: Path
    db_path: Path
    model_cache: Path
    jobs_dir: Path
    health_dir: Path
    radarr_url: str
    radarr_api_key: str
    jellyfin_url: str
    jellyfin_api_key: str
    tag: str
    tts_engine: str
    scan_interval: int
    supervisor_poll_interval: int
    lease_seconds: int
    min_media_free_gb: int
    stop_publish_free_gb: int
    min_work_free_gb: int
    min_free_vram_mb: int
    require_gpu: bool
    jellyfin_refresh: bool
    max_attempts: int

    @classmethod
    def from_env(cls) -> "Settings":
        state_dir = Path(os.getenv("VN_DUB_STATE_DIR", "/state")).resolve()
        media_root = Path(os.getenv("VN_DUB_MEDIA_ROOT", "/data/media")).resolve()
        default_profile = Path("/app/profiles/voice-over-v1.yaml")
        profile_path = Path(os.getenv("VN_DUB_PROFILE", str(default_profile))).resolve()
        return cls(
            state_dir=state_dir,
            media_root=media_root,
            profile_path=profile_path,
            db_path=state_dir / "dubbing.sqlite3",
            model_cache=state_dir / "model-cache",
            jobs_dir=state_dir / "jobs",
            health_dir=state_dir / "health",
            radarr_url=os.getenv("RADARR_URL", "http://radarr:7878").rstrip("/"),
            radarr_api_key=os.getenv("RADARR_API_KEY", "").strip(),
            jellyfin_url=os.getenv("JELLYFIN_URL", "http://jellyfin:8096").rstrip("/"),
            jellyfin_api_key=os.getenv("JELLYFIN_API_KEY", "").strip(),
            tag=os.getenv("VN_DUB_TAG", "vn-dub"),
            tts_engine=os.getenv("VN_DUB_ENGINE", "vieneu-v2").strip(),
            scan_interval=_env_int("VN_DUB_SCAN_INTERVAL", 3600, 60),
            supervisor_poll_interval=_env_int("VN_DUB_SUPERVISOR_POLL_INTERVAL", 30, 1),
            lease_seconds=_env_int("VN_DUB_LEASE_SECONDS", 180, 30),
            min_media_free_gb=_env_int("VN_DUB_MIN_MEDIA_FREE_GB", 20),
            stop_publish_free_gb=_env_int("VN_DUB_STOP_PUBLISH_FREE_GB", 10),
            min_work_free_gb=_env_int("VN_DUB_MIN_WORK_FREE_GB", 10),
            min_free_vram_mb=_env_int("VN_DUB_MIN_FREE_VRAM_MB", 7000),
            require_gpu=_env_bool("VN_DUB_REQUIRE_GPU", True),
            jellyfin_refresh=_env_bool("VN_DUB_JELLYFIN_REFRESH", True),
            max_attempts=_env_int("VN_DUB_MAX_ATTEMPTS", 3, 1),
        )

    def ensure_state_dirs(self) -> None:
        for path in (self.state_dir, self.model_cache, self.jobs_dir, self.health_dir):
            path.mkdir(parents=True, exist_ok=True)

    def require_radarr(self) -> None:
        if not self.radarr_api_key:
            raise ConfigurationError("RADARR_API_KEY is required")


@dataclass(frozen=True)
class Profile:
    path: Path
    data: dict[str, Any]
    sha256: str

    @classmethod
    def load(cls, path: Path) -> "Profile":
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ConfigurationError(f"cannot read profile {path}: {exc}") from exc
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"invalid profile YAML {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigurationError("profile root must be a mapping")
        for section in ("model", "speech", "mix", "output"):
            if not isinstance(data.get(section), dict):
                raise ConfigurationError(f"profile section {section!r} is required")
        model = data["model"]
        for key in ("engine", "repository", "revision", "voice_id"):
            if not model.get(key):
                raise ConfigurationError(f"profile model.{key} is required")
        if model["engine"] != "vieneu-v2":
            raise ConfigurationError("primary profile model.engine must be vieneu-v2")
        return cls(path=path, data=data, sha256=hashlib.sha256(raw).hexdigest())

    def validate_engine(self, engine: str) -> None:
        if engine == "vieneu-v2":
            return
        if engine == "voxcpm2":
            fallback = self.data.get("fallback_model") or {}
            if not fallback.get("enabled"):
                raise ConfigurationError(
                    "VN_DUB_ENGINE=voxcpm2 requires fallback_model.enabled=true"
                )
            return
        raise ConfigurationError(f"unknown VN_DUB_ENGINE {engine!r}")

    @property
    def model(self) -> dict[str, Any]:
        return self.data["model"]

    @property
    def speech(self) -> dict[str, Any]:
        return self.data["speech"]

    @property
    def mix(self) -> dict[str, Any]:
        return self.data["mix"]

    @property
    def output(self) -> dict[str, Any]:
        return self.data["output"]
