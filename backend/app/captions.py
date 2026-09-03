"""
Phase 8: ask an LLM to design captions for a clip as a sequence of short
phrases ("beats"), each tagged with a ROLE from a fixed, disciplined
three-tier typographic system - not a different font/color/size/animation
combo chosen freely per beat.

This replaced an earlier version where the LLM chose weight/tone/font/fill/
color/rotation/animation per beat from a wide menu ("kinetic typography").
That produced captions that looked busy and inconsistent rather than
designed - chasing variety instead of a repeatable hierarchy. Analyzing a
real reference video's captions (a UMD student's own reference footage)
found the opposite: a small, disciplined THREE-ROLE system used
consistently throughout - a quiet serif "setup" line, a large bold "punch"
line, and an occasional blue-script "accent" word. The renderer
(caption_render.py) now owns the ENTIRE visual treatment for each role;
the LLM's only job is text + role assignment, i.e. editorial judgment
("which words are the setup, which is the punch"), not design judgment.

Mirrors clip_selection.py's provider-swappable pattern exactly (same four
providers, same "ask for structured JSON via each provider's native
mechanism" approach) - see that file's docstring for the provider list.

Design choice worth calling out: the LLM is asked for TEXT split into beats
plus a role, but NEVER for timestamps or position. Timestamps are derived
deterministically afterward by walking the clip's word-level transcript
(from faster-whisper) and consuming words positionally, one beat's word
count at a time. Position is a fixed default (caption_render.py's
_DEFAULT_ANCHOR) unless the user drags a beat in the editor - see
anchor_x/anchor_y below.
"""
import json
import re
from pathlib import Path
from typing import Any, Optional

from . import config

# --- The "beat" schema -----------------------------------------------------
#
# A beat is a short run of words that appears together, tagged with which
# of the three fixed roles it plays. Sequential beats are how a sentence
# "builds" as it's spoken; the LLM decides how the words are grouped (a
# meaningful phrase stays together, e.g. "observe carefully" is one beat,
# not two) and which role each beat plays. Everything else - font, size,
# color, position, entrance animation - is fixed per role in
# caption_render.py, not chosen here.

BEAT_PROPERTIES: dict[str, Any] = {
    "text": {
        "type": "string",
        "description": (
            "The exact words for this beat, in the same order they appear in the "
            "transcript below - do not paraphrase, reorder, or invent words. Punctuation "
            "can be cleaned up slightly (e.g. drop filler 'um'/'uh') but the words "
            "themselves must match the transcript."
        ),
    },
    "role": {
        "type": "string",
        "enum": ["setup", "punch", "accent"],
        "description": (
            "Which of the three fixed roles this beat plays - see the system prompt for "
            "the full definition of each. 'setup' = quiet context-setting words (most "
            "beats). 'punch' = the key emphasis word/phrase, bigger and bolder. 'accent' = "
            "RARE - a single standout word or very short phrase in a completely different "
            "elegant script treatment; use sparingly, most clips have zero to two of these."
        ),
    },
}
BEAT_REQUIRED = ["text", "role"]

CAPTIONS_TOOL = {
    "name": "propose_captions",
    "description": "Split this clip's transcript into caption beats and assign each a fixed typographic role.",
    "input_schema": {
        "type": "object",
        "properties": {
            "beats": {"type": "array", "items": {"type": "object", "properties": BEAT_PROPERTIES, "required": BEAT_REQUIRED}}
        },
        "required": ["beats"],
    },
}
CAPTIONS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "beats": {"type": "array", "items": {"type": "object", "properties": BEAT_PROPERTIES, "required": BEAT_REQUIRED}}
    },
    "required": ["beats"],
}


SYSTEM_PROMPT = """You are captioning short-form vertical video (TikTok/Reels/Shorts) using a fixed, disciplined three-tier typographic system - not free-form per-beat style choices. Every caption beat gets exactly one ROLE; the visual treatment for each role (font, size, color, position, animation) is entirely fixed by the renderer. Your only job is editorial: decide how the transcript breaks into short beats, and which role each one plays.

The three roles:
- "setup": quiet, smaller, context-setting words that lead into the point - the connective tissue of the sentence (e.g. "so you really have to", "and also through", "Hello,"). Most beats in a typical clip are "setup".
- "punch": the key word or short phrase that IS the point - the emphasis, the payoff, the thing the viewer should walk away remembering (e.g. "watch closely.", "observe carefully.", "should have."). Rendered bigger and bolder. Usually the last beat of a sentence or thought, though a short declarative clip can be entirely "punch" beats with no setup at all.
- "accent": RARE. Reserved for a single standout word or very short phrase (1-3 words) that deserves a completely different, elegant script treatment instead of its normal setup/punch look - a name, a warm aside, or the one word the whole clip has been building to (e.g. "friend.", "sir.", "designer"). Use this for at most 1 in every 4-5 beats, never twice in a row, and only when a specific word genuinely earns it - most clips should have zero, one, or two accent beats total, not more. When in doubt, don't use it.

Group words into beats the way a real editor breaks a sentence into caption cards - a beat is usually 1-6 words that read as one phrase, not individual words timed like karaoke. Use the exact words from the transcript, in order, covering the whole clip; it's fine to drop pure filler ("um", "uh", false starts) if it makes the captions cleaner. Do not invent any style choice beyond the role - no fonts, colors, sizes, rotation, or positions to decide; the renderer owns all of that."""


def _format_words_for_prompt(words: list[dict[str, Any]]) -> str:
    return " ".join(w["word"] for w in words)


def build_prompt(words: list[dict[str, Any]]) -> tuple[str, str]:
    user_prompt = (
        "Split this clip's transcript into caption beats and assign each a role.\n\n"
        "--- TRANSCRIPT (exact words, in order) ---\n"
        f"{_format_words_for_prompt(words)}\n"
        "--- END TRANSCRIPT ---"
    )
    return SYSTEM_PROMPT, user_prompt


class CaptionError(RuntimeError):
    pass


def _call_anthropic(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if not config.ANTHROPIC_API_KEY:
        raise CaptionError(
            "No Anthropic API key configured. Add ANTHROPIC_API_KEY to backend/.env and restart."
        )
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[CAPTIONS_TOOL],
        tool_choice={"type": "tool", "name": "propose_captions"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "propose_captions":
            beats = block.input.get("beats", [])
            if not isinstance(beats, list):
                raise CaptionError("Model response was malformed (beats is not a list).")
            return beats
    raise CaptionError("Model did not return a caption proposal.")


def _call_openai(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if not config.OPENAI_API_KEY:
        raise CaptionError("No OpenAI API key configured. Add OPENAI_API_KEY to backend/.env and restart.")
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    tool = {"type": "function", "function": {**CAPTIONS_TOOL, "parameters": CAPTIONS_TOOL["input_schema"]}}
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "propose_captions"}},
    )
    message = response.choices[0].message
    if not message.tool_calls:
        raise CaptionError("Model did not return a caption proposal.")
    args = json.loads(message.tool_calls[0].function.arguments)
    beats = args.get("beats", [])
    if not isinstance(beats, list):
        raise CaptionError("Model response was malformed (beats is not a list).")
    return beats


def _call_gemini(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if not config.GEMINI_API_KEY:
        raise CaptionError("No Gemini API key configured. Add GEMINI_API_KEY to backend/.env and restart.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise CaptionError("The 'google-genai' package isn't installed. Run 'pip install google-genai'.") from e

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=CAPTIONS_RESPONSE_SCHEMA,
        ),
    )
    if not response.text:
        raise CaptionError("Model did not return a caption proposal.")
    data = json.loads(response.text)
    beats = data.get("beats", [])
    if not isinstance(beats, list):
        raise CaptionError("Model response was malformed (beats is not a list).")
    return beats


def _estimate_context_size(system_prompt: str, user_prompt: str) -> int:
    estimated_tokens = (len(system_prompt) + len(user_prompt)) // 4
    needed = estimated_tokens + 8192
    for bucket in (4096, 8192, 16384, 32768, 65536):
        if needed <= bucket:
            return bucket
    return 65536


def _call_ollama(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    try:
        import ollama
    except ImportError as e:
        raise CaptionError("The 'ollama' package isn't installed. Run 'pip install ollama'.") from e

    client = ollama.Client(host=config.OLLAMA_BASE_URL)
    num_ctx = _estimate_context_size(system_prompt, user_prompt)
    try:
        response = client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            format=CAPTIONS_RESPONSE_SCHEMA,
            options={"num_ctx": num_ctx},
        )
    except ConnectionError as e:
        raise CaptionError("Couldn't reach Ollama - is it installed and running?") from e
    except Exception as e:  # noqa: BLE001
        raise CaptionError(f"Ollama error: {e}") from e

    content = response["message"]["content"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise CaptionError("Model didn't return valid JSON. Try a larger model or a different provider.") from e
    beats = data.get("beats", [])
    if not isinstance(beats, list):
        raise CaptionError("Model response was malformed (beats is not a list).")
    return beats


_PROVIDERS = {"anthropic": _call_anthropic, "openai": _call_openai, "gemini": _call_gemini, "ollama": _call_ollama}

_ENUMS = {
    "role": {"setup", "punch", "accent"},
}


def _word_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def _clamped_unit(value: Any) -> Optional[float]:
    """Parses an optional 0.0-1.0 anchor coordinate; None if absent/invalid."""
    if value is None:
        return None
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return None


def _clamped_scale(value: Any) -> Optional[float]:
    """
    Parses an optional manual size-multiplier override (0.4-2.5x); None if
    absent/invalid. Set only by the editor's on-canvas resize handle - the
    LLM never sets this or anchor_x/anchor_y, both are purely a manual
    override on top of the fixed default position/size for a beat's role.
    """
    if value is None:
        return None
    try:
        return round(max(0.4, min(2.5, float(value))), 3)
    except (TypeError, ValueError):
        return None


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clamped_color(value: Any) -> Optional[str]:
    """
    Parses an optional manual text-color override - a 6-digit hex string
    (exactly what a native <input type="color"> produces), lowercased for
    consistency. None (absent/invalid) means "use this beat's role color" -
    same manual-override-only pattern as anchor_x/anchor_y/size_scale; the
    LLM never sets this.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.lower() if _HEX_COLOR_RE.match(value) else None


def _clamped_opacity(value: Any) -> Optional[float]:
    """
    Parses an optional manual opacity override (0.1-1.0); None if
    absent/invalid means "fully opaque" (the default). Floored at 0.1 rather
    than 0 so a caption can never be dragged into being completely invisible
    by accident.
    """
    if value is None:
        return None
    try:
        return round(max(0.1, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return None


def _clean_beat(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Validate + clamp one raw beat. Returns None if unusable (e.g. no text)."""
    try:
        text = str(raw["text"]).strip()
        if not text:
            return None
        beat = {
            "text": text,
            "role": raw.get("role") if raw.get("role") in _ENUMS["role"] else "punch",
            # Always absent for a freshly-LLM-generated beat (the LLM never
            # sets these) - present only once a user drags/resizes this beat
            # in the editor and re-saves the plan. Parsed here too so a
            # manually-edited plan re-run through this validator (if ever)
            # keeps its overrides.
            "anchor_x": _clamped_unit(raw.get("anchor_x")),
            "anchor_y": _clamped_unit(raw.get("anchor_y")),
            "size_scale": _clamped_scale(raw.get("size_scale")),
            "color": _clamped_color(raw.get("color")),
            "opacity": _clamped_opacity(raw.get("opacity")),
        }
        return beat
    except (KeyError, TypeError, ValueError):
        return None


def _align_beats_to_words(beats: list[dict[str, Any]], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Derives each beat's start/end from real ASR word timing by consuming
    `words` positionally, one beat's word count at a time - see module
    docstring for why we don't trust LLM-generated timestamps at all.
    """
    timed: list[dict[str, Any]] = []
    cursor = 0
    n = len(words)
    for beat in beats:
        count = len(_word_tokens(beat["text"]))
        if count == 0 or cursor >= n:
            continue
        end_idx = min(cursor + count, n)
        span = words[cursor:end_idx]
        if not span:
            continue
        timed.append({**beat, "start": span[0]["start"], "end": span[-1]["end"]})
        cursor = end_idx
    return timed


def generate_caption_plan(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    words: this clip's slice of word-level transcript entries, already
    clip-relative (start/end measured from the clip's own start, not the
    source video's).
    """
    if not words:
        raise CaptionError("This clip has no transcribed words to caption.")

    system_prompt, user_prompt = build_prompt(words)
    call_fn = _PROVIDERS.get(config.LLM_PROVIDER)
    if call_fn is None:
        raise CaptionError(f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}' in backend/.env.")

    raw_beats = call_fn(system_prompt, user_prompt)
    cleaned = [b for b in (_clean_beat(r) for r in raw_beats) if b is not None]
    if not cleaned:
        raise CaptionError("Model didn't return any usable caption beats.")

    return _align_beats_to_words(cleaned, words)
