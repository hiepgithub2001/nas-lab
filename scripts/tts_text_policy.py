#!/usr/bin/env python3
"""
Text policy for subtitle lines on their way into a speech synthesiser.

Two settings, and both exist because a dubbed episode is 1,500 lines that have
to sound like one narrator reading steadily, not an actor performing:

  tone_policy: neutral_steady   sampling is pinned low so delivery does not
                                wander between lines
  allow_emotion_tags: false     bracketed cues are stripped, never forwarded

Why the tag rule is a security-shaped concern rather than a tidiness one:
VieNeu reads "[cười]" as an instruction to laugh, not as text (its own README
opens with that example), and v3 Turbo advertises emotion cues outright. A
subtitle is untrusted input — it arrives from an indexer, an OCR pass or a model
translation. Forwarding a bracket verbatim hands whoever wrote it control of the
delivery. So tags are removed here, before any engine sees them, and the policy
is off by default rather than opt-out.

The AI subtitles in this library carried no tags at all when this was written
(0 brackets, 0 parens, 0 music marks across 14,493 lines). This is a guard
against the ones that have not arrived yet, not a cleanup of the ones that have.

Sampling values are per engine because the knobs are not the same shape:

  VieNeu v2   temperature 1.0 -> 0.8, top_k 50 (unchanged)
  VoxCPM2     cfg_value 2.0 -> 3.0   (higher = adheres harder to the prompt)

Do not "turn the temperature down for a steadier read" on VieNeu v2. It is an
autoregressive codec model, and starving the sampler makes it fail to emit an
end-of-speech token: it runs to the length cap and emits 33.32s of babble for a
4s line. Measured 2026-08-10, isolating the two knobs over 4 takes each:

  temp 0.3, top_k 20   3/4 takes runaway
  temp 0.3, top_k 50   2/4 takes runaway     <- temperature is the cause,
  temp 0.5, top_k 20   0/4                      not top_k
  temp 0.5, top_k 50   0/4

The failure is intermittent, which makes it worse than a hard break: a short
smoke test can pass and a 1,500-line episode still ends up with a few minutes of
garbage buried in it. Treat anything below 0.6 as unusable. A sweep of the same
line put the steadiest region in the middle rather than the bottom:

  temp  0.5    26.2% / 20.9% duration CV — erratic, near the cliff
  temp  0.8    3.4% CV  <- steadiest
  temp  1.0    5.1-12.5% CV (the default)

Usage:
  from tts_text_policy import POLICY, clean_for_tts, engine_params
  text = clean_for_tts(line)
  kwargs = engine_params("vieneu-v2")
"""
import re
import unicodedata

POLICY = {
    "tone_policy": "neutral_steady",
    "allow_emotion_tags": False,
}

# --- what counts as a tag -------------------------------------------------
# Square brackets  [cười] [LAUGHS] [MUSIC PLAYING]  — the engine-directive form.
# Parentheses      (thở dài) (sighs)                — the SDH form.
# Music marks      ♪ ... ♪ and lone ♪               — a lyric line with no lyric.
# Speaker labels   "NAM:" / "MINH ĐỨC:" at line start, an artefact of SDH tracks.
#
# Parentheses became safe to strip wholesale once the pronoun gloss stopped
# emitting "I (TÔI)" on 2026-08-09; before that this rule would have eaten the
# pronouns. See fix-pronoun-gloss.py.
TAG_RES = (
    re.compile(r"\[[^\]]*\]"),
    re.compile(r"\([^)]*\)"),
    re.compile(r"♪[^♪]*♪"),
    re.compile(r"♪"),
    re.compile(r"^\s*[A-ZÀ-Ỹ][A-ZÀ-Ỹ\s.'-]{1,24}:\s*", re.M),
)
MARKUP_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
# A leading "-" marks a second speaker in the cue. It is punctuation for the
# eye; spoken, it is nothing. Dropped so the synthesiser does not pause on it.
DIALOGUE_DASH_RE = re.compile(r"^\s*[-–—]\s*", re.M)
WS_RE = re.compile(r"[ \t]{2,}")


def clean_for_tts(text, allow_emotion_tags=None):
    """Return `text` reduced to what should actually be spoken.

    Returns an empty string when nothing speakable survives — a cue that was
    only "♪" or only "[MUSIC]" has no audio to generate, and the caller should
    skip it rather than synthesise silence.
    """
    allow = POLICY["allow_emotion_tags"] if allow_emotion_tags is None else allow_emotion_tags

    out = MARKUP_RE.sub("", text)
    if not allow:
        for rx in TAG_RES:
            out = rx.sub(" ", out)
    out = DIALOGUE_DASH_RE.sub("", out)
    # Curly quotes and ellipsis characters read badly through some normalisers.
    out = out.replace("…", "...").replace("“", '"').replace("”", '"')
    out = unicodedata.normalize("NFC", out)
    out = WS_RE.sub(" ", out)
    return "\n".join(l.strip() for l in out.splitlines() if l.strip()).strip()


# --- per-engine sampling --------------------------------------------------

_NEUTRAL_STEADY = {
    # Nudged down from 1.0, not starved — see the runaway note in the module
    # docstring. top_k stays at the default; tightening it bought nothing.
    "vieneu-v2": {"temperature": 0.8, "top_k": 50},
    # VoxCPM2 has no temperature, and cfg_value turned out NOT to be a
    # steadiness lever — measured 2026-08-10, a sweep at 5 takes/cell produced
    # 8.7-12.7% duration CV across cfg 1.5-3.0 with no clean trend, and two runs
    # of cfg 2.0 disagreed (5.1% vs 10.1%). The effect, if any, is under the
    # noise floor. Only one result replicated: cfg 3.0 was worst in both runs.
    # So this holds the default rather than pretending to an optimum.
    #
    # What actually controls VoxCPM2 consistency is the pinned reference clip:
    # unconditioned, five takes of one line spanned 2.40-3.68s (53%); with a
    # reference pinned, CV drops to single digits. Pin the reference and leave
    # cfg alone.
    "voxcpm2": {"cfg_value": 2.0, "inference_timesteps": 10},
}
_DEFAULTS = {
    "vieneu-v2": {"temperature": 1.0, "top_k": 50},
    "voxcpm2": {"cfg_value": 2.0, "inference_timesteps": 10},
}


def engine_params(engine, tone_policy=None):
    """Sampling kwargs for `engine` under the active tone policy."""
    policy = tone_policy or POLICY["tone_policy"]
    table = _NEUTRAL_STEADY if policy == "neutral_steady" else _DEFAULTS
    if engine not in table:
        raise ValueError(f"unknown engine {engine!r}; known: {sorted(table)}")
    return dict(table[engine])
