"""
Phase 4: ask an LLM to pick the strongest standalone moments from a transcript.

Fully provider-swappable via config.LLM_PROVIDER (see backend/.env.example):
  - "ollama"    - local, free, runs on your own machine (default)
  - "anthropic" - Claude API (needs ANTHROPIC_API_KEY, paid)
  - "openai"    - OpenAI API (needs OPENAI_API_KEY, paid)
  - "gemini"    - Google Gemini API (needs GEMINI_API_KEY, has a free tier)

All four are asked for the exact same structured JSON shape (a list of
clips with start/end/title/hook/reason/score), each using that provider's
native structured-output mechanism (tool-use, function-calling, or a JSON
schema) rather than just asking a model to "return JSON" in prose, which is
far less reliable. Switching providers is a one-line change in backend/.env
- no code changes needed.
"""
import json
from pathlib import Path
from typing import Any, Literal

from . import config

ContentPreference = Literal[
    "best", "educational", "funny", "storytelling", "controversial", "emotional"
]

CONTENT_PREFERENCE_GUIDANCE: dict[str, str] = {
    "best": "Pick whatever moments are strongest overall - don't filter by category, just find the best standalone content in the video.",
    "educational": "Prioritize clear teachable insights, explanations, and useful takeaways over other kinds of moments.",
    "funny": "Prioritize genuinely funny or humorous moments - jokes, banter, comedic timing, funny stories.",
    "storytelling": "Prioritize self-contained stories and anecdotes with a clear beginning, middle, and payoff.",
    "controversial": "Prioritize bold claims, hot takes, arguments, or opinions likely to spark discussion or disagreement.",
    "emotional": "Prioritize emotionally resonant moments - vulnerability, passion, sincerity, moving stories.",
}

# One canonical description of the shape we want back, reused (in slightly
# different dialects) across every provider below.
CLIP_ITEM_PROPERTIES: dict[str, Any] = {
    "start": {"type": "number", "description": "Clip start time in seconds, matching a transcript timestamp."},
    "end": {"type": "number", "description": "Clip end time in seconds, matching a transcript timestamp."},
    "title": {"type": "string", "description": "A short, punchy title for this clip (like a video title, not a description)."},
    "hook": {"type": "string", "description": "The opening line or hook that grabs attention in the first couple seconds."},
    "reason": {"type": "string", "description": "One sentence on why this works as standalone short-form content."},
    "score": {
        "type": "integer",
        "description": (
            "Confidence score from 0 to 100 for how strong this clip is - NOT a 1-5 or "
            "1-10 rating. Use the full range: a truly excellent, highly shareable clip "
            "should score 85-100, a solid good clip 65-84, a decent-but-not-great clip "
            "45-64, and a weak/filler clip below 45."
        ),
    },
}
CLIP_ITEM_REQUIRED = ["start", "end", "title", "hook", "reason", "score"]

CLIP_SELECTION_TOOL = {
    "name": "propose_clips",
    "description": "Propose the best short-form vertical video clips from this transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {"type": "object", "properties": CLIP_ITEM_PROPERTIES, "required": CLIP_ITEM_REQUIRED},
            }
        },
        "required": ["clips"],
    },
}

# Plain JSON-schema version of the same shape, for providers (Ollama, Gemini)
# that take a response schema directly rather than a "tool".
CLIPS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {"type": "object", "properties": CLIP_ITEM_PROPERTIES, "required": CLIP_ITEM_REQUIRED},
        }
    },
    "required": ["clips"],
}


def _format_transcript(segments: list[dict[str, Any]]) -> str:
    """Compact timestamped transcript, one segment per line: [12.3s] text"""
    return "\n".join(f"[{seg['start']:.1f}s] {seg['text']}" for seg in segments)


def build_prompt(
    segments: list[dict[str, Any]],
    num_clips: int,
    target_length_seconds: int,
    content_preference: ContentPreference,
) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    min_len = max(15, target_length_seconds - 10)
    max_len = target_length_seconds + 15

    system_prompt = (
        "You are an expert short-form video producer who finds the strongest "
        "standalone moments in long-form video transcripts (podcasts, interviews, "
        "talks) and turns them into vertical short-form clips (TikTok/Reels/Shorts "
        "style).\n\n"
        "Look for: strong hooks, interesting statements, surprising information, "
        "stories, arguments, emotional moments, funny moments, useful insights, "
        "clear conclusions - anything that works well on its own without needing "
        "much surrounding context.\n\n"
        "Do NOT pick moments that only make sense with earlier context, that trail "
        "off without a payoff, or that are just filler/small talk. Every clip must "
        "make sense and grab attention within its own boundaries.\n\n"
        "Scoring: each clip needs a 'score' field from 0 to 100 - this is a "
        "percentage-style confidence score, NOT a 1-5 or 1-10 star rating. Spread "
        "your scores across the full 0-100 range based on how strong each clip "
        "really is: 85-100 for a truly excellent, highly shareable clip, 65-84 for "
        "solid and good, 45-64 for decent but unremarkable, and below 45 for weak "
        "or filler content. Do not cluster every score in a narrow band like 1-10."
    )

    guidance = CONTENT_PREFERENCE_GUIDANCE[content_preference]

    user_prompt = (
        f"Find the {num_clips} best standalone clips from this transcript.\n\n"
        f"Target clip length: around {target_length_seconds} seconds "
        f"(acceptable range: {min_len}-{max_len} seconds).\n"
        f"Content preference: {guidance}\n\n"
        "Use exact timestamps from the transcript below for start/end (snap to "
        "segment boundaries where it makes sense, so clips don't cut off mid-sentence). "
        "Clips should not overlap each other.\n\n"
        "--- TRANSCRIPT ---\n"
        f"{_format_transcript(segments)}\n"
        "--- END TRANSCRIPT ---"
    )

    return system_prompt, user_prompt


class ClipSelectionError(RuntimeError):
    pass


def _call_anthropic(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if not config.ANTHROPIC_API_KEY:
        raise ClipSelectionError(
            "No Anthropic API key configured. Get one at "
            "https://console.anthropic.com/settings/keys, then add it to "
            "backend/.env as ANTHROPIC_API_KEY=... and restart the backend."
        )

    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[CLIP_SELECTION_TOOL],
        tool_choice={"type": "tool", "name": "propose_clips"},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "propose_clips":
            clips = block.input.get("clips", [])
            if not isinstance(clips, list):
                raise ClipSelectionError("Model response was malformed (clips is not a list).")
            return clips

    raise ClipSelectionError("Model did not return a clip proposal.")


def _call_openai(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if not config.OPENAI_API_KEY:
        raise ClipSelectionError(
            "No OpenAI API key configured. Get one at "
            "https://platform.openai.com/api-keys, then add it to "
            "backend/.env as OPENAI_API_KEY=... and restart the backend."
        )

    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    tool = {"type": "function", "function": {**CLIP_SELECTION_TOOL, "parameters": CLIP_SELECTION_TOOL["input_schema"]}}
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "propose_clips"}},
    )

    message = response.choices[0].message
    if not message.tool_calls:
        raise ClipSelectionError("Model did not return a clip proposal.")

    args = json.loads(message.tool_calls[0].function.arguments)
    clips = args.get("clips", [])
    if not isinstance(clips, list):
        raise ClipSelectionError("Model response was malformed (clips is not a list).")
    return clips


def _call_gemini(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    if not config.GEMINI_API_KEY:
        raise ClipSelectionError(
            "No Gemini API key configured. Get a free one at "
            "https://aistudio.google.com/apikey, then add it to "
            "backend/.env as GEMINI_API_KEY=... and restart the backend."
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise ClipSelectionError(
            "The 'google-genai' package isn't installed. Run "
            "'pip install google-genai' in backend/ (with the venv active) and restart."
        ) from e

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=CLIPS_RESPONSE_SCHEMA,
        ),
    )

    if not response.text:
        raise ClipSelectionError("Model did not return a clip proposal.")

    data = json.loads(response.text)
    clips = data.get("clips", [])
    if not isinstance(clips, list):
        raise ClipSelectionError("Model response was malformed (clips is not a list).")
    return clips


def _estimate_context_size(system_prompt: str, user_prompt: str) -> int:
    """
    Ollama's default context window (2048-4096 tokens depending on model) is
    nowhere near enough for a 1-2 hour video's transcript, but always
    requesting the max wastes VRAM/RAM on short videos and can crash smaller
    setups. So: roughly estimate tokens from character count (~4 chars/token
    for English is a standard rule of thumb), add headroom for the model's
    output, and round up to a fixed bucket size. Not exact, but doesn't need
    to be - just needs to comfortably fit the actual prompt.
    """
    estimated_tokens = (len(system_prompt) + len(user_prompt)) // 4
    needed = estimated_tokens + 4096  # headroom for the JSON response itself
    for bucket in (4096, 8192, 16384, 32768, 65536):
        if needed <= bucket:
            return bucket
    return 65536  # cap - a longer transcript than this should still mostly work, just tighter


def _call_ollama(system_prompt: str, user_prompt: str) -> list[dict[str, Any]]:
    try:
        import ollama
    except ImportError as e:
        raise ClipSelectionError(
            "The 'ollama' package isn't installed. Run 'pip install ollama' "
            "in backend/ (with the venv active) and restart."
        ) from e

    client = ollama.Client(host=config.OLLAMA_BASE_URL)
    num_ctx = _estimate_context_size(system_prompt, user_prompt)

    try:
        response = client.chat(
            model=config.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            format=CLIPS_RESPONSE_SCHEMA,
            options={"num_ctx": num_ctx},
        )
    except ConnectionError as e:
        raise ClipSelectionError(
            "Couldn't reach Ollama - is it installed and running? Install from "
            "https://ollama.com, make sure it's running, and make sure you've "
            f"pulled the model with: ollama pull {config.OLLAMA_MODEL}"
        ) from e
    except Exception as e:  # noqa: BLE001 - ollama raises its own error types beyond ConnectionError
        msg = str(e)
        if "not found" in msg.lower():
            raise ClipSelectionError(
                f"Model '{config.OLLAMA_MODEL}' isn't pulled yet. Run: "
                f"ollama pull {config.OLLAMA_MODEL}"
            ) from e
        raise ClipSelectionError(f"Ollama error: {msg}") from e

    content = response["message"]["content"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ClipSelectionError(
            "Model didn't return valid JSON. Smaller local models sometimes "
            "struggle with this - try a larger model, or a different provider."
        ) from e

    clips = data.get("clips", [])
    if not isinstance(clips, list):
        raise ClipSelectionError("Model response was malformed (clips is not a list).")
    return clips


_PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "ollama": _call_ollama,
}


def _rescale_undersized_scores(cleaned: list[dict[str, Any]]) -> None:
    """
    Safety net for models (mainly smaller local ones) that ignore the 0-100
    instruction and instead score like a 1-5 or 1-10 star rating. If every
    score in the batch is suspiciously small, stretch them out to use more of
    the 0-100 range so sorting/display still looks sensible. Mutates in place.
    """
    scores = [c["score"] for c in cleaned]
    if not scores:
        return
    max_score = max(scores)
    if max_score > 10:
        return  # looks like a real 0-100 score already, leave it alone

    # Treat the observed max as the top of whatever small scale the model
    # used (1-5, 1-10, etc.) and map it onto a 45-100 band - clips that made
    # it into the "best N" results shouldn't read as weak/filler (<45).
    scale_top = max(max_score, 1)
    for c in cleaned:
        c["score"] = round(45 + (c["score"] / scale_top) * 55)


def _validate_and_clean(
    clips: list[dict[str, Any]], video_duration: float, num_clips: int
) -> list[dict[str, Any]]:
    """Clamp to video bounds, drop malformed entries, sort by score, cap to num_clips."""
    cleaned = []
    for c in clips:
        try:
            start = max(0.0, float(c["start"]))
            end = min(video_duration, float(c["end"]))
            if end <= start:
                continue
            cleaned.append(
                {
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "title": str(c["title"]).strip(),
                    "hook": str(c["hook"]).strip(),
                    "reason": str(c["reason"]).strip(),
                    "score": max(0, min(100, int(c["score"]))),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed entries rather than failing the whole batch

    _rescale_undersized_scores(cleaned)
    cleaned.sort(key=lambda c: c["score"], reverse=True)
    return cleaned[:num_clips]


def select_clips(
    transcript: dict[str, Any],
    num_clips: int,
    target_length_seconds: int,
    content_preference: ContentPreference,
) -> list[dict[str, Any]]:
    system_prompt, user_prompt = build_prompt(
        transcript["segments"], num_clips, target_length_seconds, content_preference
    )

    call_fn = _PROVIDERS.get(config.LLM_PROVIDER)
    if call_fn is None:
        raise ClipSelectionError(
            f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}' in backend/.env. "
            f"Must be one of: {', '.join(_PROVIDERS)}."
        )

    raw_clips = call_fn(system_prompt, user_prompt)
    return _validate_and_clean(raw_clips, transcript["duration_seconds"], num_clips)
