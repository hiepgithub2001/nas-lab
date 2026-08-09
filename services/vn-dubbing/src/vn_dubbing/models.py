from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobState(StrEnum):
    PENDING = "pending"
    WAITING_RESOURCES = "waiting_resources"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    RETRYABLE_FAILED = "retryable_failed"
    PERMANENT_FAILED = "permanent_failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    STALE = "stale"
    COMPLETED = "completed"


LEASEABLE_STATES = (
    JobState.PENDING,
    JobState.WAITING_RESOURCES,
    JobState.RETRYABLE_FAILED,
)

# At most one of these states may exist for one concrete Radarr movie file.
# STALE/SUPERSEDED/CANCELLED are intentionally absent so an operator or file
# replacement can authorize a new immutable job identity.
BLOCKING_JOB_STATES = (
    JobState.PENDING,
    JobState.WAITING_RESOURCES,
    JobState.RUNNING,
    JobState.NEEDS_REVIEW,
    JobState.RETRYABLE_FAILED,
    JobState.PERMANENT_FAILED,
    JobState.CANCEL_REQUESTED,
    JobState.COMPLETED,
)


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    source_text: str
    normalized_text: str

    @property
    def window_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class DiscoveredMovie:
    radarr_movie_id: int
    radarr_movie_file_id: int
    title: str
    video_path: str
    subtitle_path: str
    subtitle_sha256: str
    identity_hash: str


@dataclass(frozen=True)
class ProbeResult:
    duration_ms: int
    audio_stream_index: int
    audio_codec: str


class PipelineError(RuntimeError):
    code = "pipeline_error"
    exit_code = 10


class ConfigurationError(PipelineError):
    code = "configuration"
    exit_code = 2


class AdmissionDeferred(PipelineError):
    code = "waiting_resources"
    exit_code = 10


class NeedsReview(PipelineError):
    code = "needs_review"
    exit_code = 20


class PermanentFailure(PipelineError):
    code = "permanent_failure"
    exit_code = 20
