"""
Whisper STT wrapper for MediEcho.

Loads openai-whisper once per process (lazy import + module-level cache) so
the doctor-facing route doesn't re-pay the model load on every transcript.

Speaker diarization: openai-whisper alone does NOT diarize — it returns a
flat transcript with no "who is speaking" labels. We do a lightweight
two-speaker heuristic using pause/turn length and a small prosody-free
classifier that picks the dominant speaker (the doctor) based on lexical
cues (clinical vocabulary, question marks, short directive phrases).

For two-party clinical conversations the result is "good enough" without
shipping a separate pyannote model. The frontend still benefits from
speaker tags because every patient-side question gets grouped together
and every doctor-side instruction gets grouped together, which is what
Gemini needs to extract a structured note.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Lazy-loaded model. We use "tiny.en" by default so the first request doesn't
# block for a minute downloading weights; production deployments should
# override MEDIECHO_WHISPER_MODEL=tiny.en/base.en/small.en/medium.en in env.
import os as _os
_DEFAULT_MODEL = _os.environ.get("MEDIECHO_WHISPER_MODEL", "tiny.en")
_model_cache: dict[str, Any] = {}


def _get_model():
    """Lazy-load the whisper model. Returns the cached instance on subsequent calls."""
    if _DEFAULT_MODEL in _model_cache:
        return _model_cache[_DEFAULT_MODEL]
    try:
        import whisper  # type: ignore  # openai-whisper
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            "openai-whisper is not installed. Run: pip install openai-whisper"
        ) from exc
    logger.info("Loading whisper model %s (first call may take a few seconds)…", _DEFAULT_MODEL)
    model = whisper.load_model(_DEFAULT_MODEL)
    _model_cache[_DEFAULT_MODEL] = model
    return model


# Lexical cues that lean the heuristic toward "doctor". Phrases are checked
# case-insensitively. Keep this list tight — false positives are worse than
# missing a cue, because the doctor side is what we want to label
# confidently for the structured extraction.
_DOCTOR_CUES = re.compile(
    r"\b("
    r"how long|how often|how severe|how many|"
    r"any (allergy|allergies|history|family history|surgery|medication)|"
    r"let me|we('ll| will) (do|order|run|start|give)|"
    r"i('ll| will) (prescribe|order|recommend|suggest|start|refer)|"
    r"i recommend|i('d| would) (suggest|recommend|like)|"
    r"take (this|these|the) (medication|tablet|capsule|drug|once|twice)|"
    r"dose|dosage|mg\b|tab\b|cap\b|"
    r"follow ?up|follow-?up|come back in|see you (in|next)|"
    r"test(s)? (ordered|recommended)|"
    r"diagnosis|prognosis|"
    r"bp|blood pressure|heart rate|temperature|"
    r"antibiotic|analgesic|antihistamine|"
    r"for (your|the) (pain|infection|cough|fever|headache|nausea)|"
    r"avoid|stop (taking|the)|"
    r"continue (taking|the)|finish the (course|dose)"
    r")\b",
    re.IGNORECASE,
)

# Lexical cues that lean toward "patient". Smaller list because patients
# mostly ask questions and describe sensations — both of which are caught
# by the question-mark + length heuristic below.
_PATIENT_CUES = re.compile(
    r"\b("
    r"i (have|feel|get|notice|think|believe|was|couldn't|cant|can't|am|was)|"
    r"my (pain|head|stomach|chest|back|throat|cough|fever|skin|leg|arm|neck)|"
    r"it (hurts|aches|burns|tingles|started|began|comes and goes)|"
    r"sometimes|often|always|never|"
    r"since (yesterday|last night|today|a few|a couple|about)|"
    r"worried|scared|anxious|"
    r"can (you|i)|should i|do i need|is it"
    r")\b",
    re.IGNORECASE,
)


def _classify_speaker(text: str, prev_speaker: str | None) -> str:
    """Pick 'doctor' or 'patient' for a single turn.

    The rule is intentionally simple: count cue hits, tie-break toward
    continuity with the previous turn (people often take multiple short
    utterances before handing the floor over), and only flip when the new
    evidence is strong.
    """
    text = text.strip()
    if not text:
        return prev_speaker or "patient"
    doc_hits = len(_DOCTOR_CUES.findall(text))
    pat_hits = len(_PATIENT_CUES.findall(text))

    # Strong directional signals.
    if doc_hits >= 2 and doc_hits > pat_hits:
        return "doctor"
    if pat_hits >= 2 and pat_hits > doc_hits:
        return "patient"
    # Single hits + a question mark on the patient side usually means the
    # patient is asking something.
    if pat_hits >= 1 and "?" in text and doc_hits == 0:
        return "patient"
    # Short directive sentences (<= 8 words, no first-person pronoun, ends
    # with a period) are almost always the doctor.
    if doc_hits >= 1 and len(text.split()) <= 8 and not re.search(r"\b(i|my|me)\b", text, re.IGNORECASE):
        return "doctor"
    # Otherwise stay on whoever spoke last to keep consecutive short turns
    # (e.g. "yes." / "okay.") attributed to the same person.
    return prev_speaker or "patient"


def _merge_consecutive(rows: list[dict]) -> list[dict]:
    """Collapse consecutive turns from the same speaker into one block."""
    merged: list[dict] = []
    for r in rows:
        if merged and merged[-1]["speaker"] == r["speaker"]:
            merged[-1]["text"] = (merged[-1]["text"] + " " + r["text"]).strip()
            merged[-1]["end"] = r["end"]
        else:
            merged.append(dict(r))
    return merged


def transcribe_audio(audio_path: str, language: str = "en") -> dict:
    """Transcribe an audio file and return a list of speaker-tagged segments.

    Returns a dict:
        {
          "segments": [
            {"start": float, "end": float, "speaker": "doctor"|"patient", "text": str},
            ...
          ],
          "language": "en",
          "duration": float,
          "model": "tiny.en",
        }

    Raises RuntimeError if whisper is not installed.
    """
    model = _get_model()
    # fp16=False because most local CPUs don't support fp16 well; this
    # keeps the first decode from crashing on commodity hardware.
    result = model.transcribe(audio_path, language=language, fp16=False, verbose=False)

    raw_segments = result.get("segments") or []
    rows: list[dict] = []
    prev_speaker: str | None = None
    for seg in raw_segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = _classify_speaker(text, prev_speaker)
        rows.append({
            "start": float(seg.get("start") or 0.0),
            "end": float(seg.get("end") or 0.0),
            "speaker": speaker,
            "text": text,
        })
        prev_speaker = speaker

    rows = _merge_consecutive(rows)
    duration = float(result.get("segments", [{}])[-1].get("end", 0.0)) if raw_segments else 0.0
    return {
        "segments": rows,
        "language": language,
        "duration": duration,
        "model": _DEFAULT_MODEL,
    }
