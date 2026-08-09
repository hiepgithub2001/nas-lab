from __future__ import annotations

import re
from pathlib import Path

from .models import PermanentFailure, SubtitleCue
from .text_normalization import normalize_text


TIMESTAMP_RE = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$"
)


def _milliseconds(groups: tuple[str, ...]) -> int:
    hours, minutes, seconds, milliseconds = (int(value) for value in groups)
    if minutes >= 60 or seconds >= 60:
        raise PermanentFailure("invalid subtitle timestamp")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def parse_srt(path: Path, *, allow_emotion_tags: bool = False) -> list[SubtitleCue]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PermanentFailure(f"subtitle is not UTF-8: {path}") from exc
    except OSError as exc:
        raise PermanentFailure(f"cannot read subtitle {path}: {exc}") from exc

    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise PermanentFailure("subtitle is empty")

    cues: list[SubtitleCue] = []
    previous_start = -1
    for ordinal, block in enumerate(re.split(r"\n\s*\n", content), start=1):
        lines = block.splitlines()
        if not lines:
            continue
        timestamp_position = 1 if lines[0].strip().isdigit() else 0
        if timestamp_position >= len(lines):
            raise PermanentFailure(f"cue {ordinal} is missing a timestamp")
        match = TIMESTAMP_RE.match(lines[timestamp_position].strip())
        if not match:
            raise PermanentFailure(f"cue {ordinal} has an invalid timestamp")
        start_ms = _milliseconds(match.groups()[:4])
        end_ms = _milliseconds(match.groups()[4:])
        if end_ms <= start_ms:
            raise PermanentFailure(f"cue {ordinal} ends before it starts")
        if start_ms < previous_start:
            raise PermanentFailure(f"cue {ordinal} is not monotonic")
        previous_start = start_ms
        source = "\n".join(lines[timestamp_position + 1 :]).strip()
        normalized = normalize_text(source, allow_emotion_tags=allow_emotion_tags)
        cues.append(
            SubtitleCue(
                index=ordinal,
                start_ms=start_ms,
                end_ms=end_ms,
                source_text=source,
                normalized_text=normalized,
            )
        )
    if not cues:
        raise PermanentFailure("subtitle contains no cues")
    return cues


def format_srt(cues: list[SubtitleCue]) -> str:
    def timestamp(milliseconds: int) -> str:
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    blocks = []
    for cue in cues:
        blocks.append(
            f"{cue.index}\n{timestamp(cue.start_ms)} --> {timestamp(cue.end_ms)}\n"
            f"{cue.normalized_text}"
        )
    return "\n\n".join(blocks) + "\n"


EXCLUDED_SUBTITLE_FLAGS = {"forced", "foreign", "sdh", "cc", "hi"}


def select_vietnamese_subtitle(video_path: Path) -> tuple[Path | None, str | None]:
    """Select a Vietnamese SRT deterministically or return an ambiguity reason."""
    candidates: list[tuple[int, Path]] = []
    prefix = video_path.stem + "."
    for path in video_path.parent.glob("*.srt"):
        if not path.name.startswith(prefix):
            continue
        suffix = path.name[len(video_path.stem) : -4]
        tokens = {token.lower() for token in suffix.lstrip(".").split(".")}
        if tokens & EXCLUDED_SUBTITLE_FLAGS:
            continue
        lower = suffix.lower()
        if lower in {".vi", ".vie"}:
            score = 10
        elif lower == ".ai.vi":
            score = 20
        else:
            continue
        candidates.append((score, path.resolve()))
    if not candidates:
        return None, "waiting_subtitle"
    best_score = min(score for score, _ in candidates)
    best = sorted(path for score, path in candidates if score == best_score)
    if len(best) != 1:
        return None, "ambiguous_subtitle"
    return best[0], None
