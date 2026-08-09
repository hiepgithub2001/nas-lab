from __future__ import annotations

import re
import unicodedata


TAG_PATTERNS = (
    re.compile(r"\[[^\]]*\]"),
    re.compile(r"\([^)]*\)"),
    re.compile(r"♪[^♪]*♪"),
    re.compile(r"♪"),
    re.compile(r"^\s*[A-ZÀ-Ỹ][A-ZÀ-Ỹ\s.'-]{1,24}:\s*", re.MULTILINE),
)
MARKUP_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
DIALOGUE_DASH_RE = re.compile(r"^\s*[-–—]\s*", re.MULTILINE)
REPEATED_EXCLAMATION_RE = re.compile(r"[!?]{2,}")
LONG_ELLIPSIS_RE = re.compile(r"\.{3,}")
SPACE_RE = re.compile(r"[ \t]{2,}")


def normalize_text(text: str, allow_emotion_tags: bool = False) -> str:
    """Return only neutral, speakable text from one subtitle cue."""
    # Some nominal SRT files retain ASS hard-line-break escapes. They are
    # formatting, not characters the model should try to pronounce.
    output = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    output = MARKUP_RE.sub("", output)
    if not allow_emotion_tags:
        for pattern in TAG_PATTERNS:
            output = pattern.sub(" ", output)
    output = DIALOGUE_DASH_RE.sub("", output)
    output = output.replace("…", ".").replace("“", '"').replace("”", '"')
    output = REPEATED_EXCLAMATION_RE.sub(".", output)
    output = LONG_ELLIPSIS_RE.sub(".", output)
    output = unicodedata.normalize("NFC", output)
    output = SPACE_RE.sub(" ", output)
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return " ".join(lines).strip()
