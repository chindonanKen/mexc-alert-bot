"""Grok / xAI continuous voice + tool agent for the desk.

Uses:
- STT: browser WAV (preferred) or ffmpeg convert → POST /v1/stt
- LLM tools: multi-turn chat/completions with full desk tool graph
- Optional TTS: POST /v1/tts

Conversation history is client-maintained and passed each turn so the agent
keeps context across continuous voice exchanges.

Live exchange orders stay off unless DESK_ALLOW_LIVE_ORDERS (no placement tool).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import certifi

    _CA = certifi.where()
except Exception:
    _CA = True

from .actions import TOOL_DEFS, run_tool
from .audio_convert import to_wav_16k_mono

logger = logging.getLogger(__name__)

SYSTEM = """You are the AD agent on AD Desk for Kenneth (MEXC AD / average-drop trader).
He is the TEACHER. You are the STUDENT. There is no separate coach product.
Voice controls the desk and stores what he teaches.

ALWAYS use tools — never invent fills, PnL, or lessons:

Sensors: list_alerts, add/update/delete_alert, list_watchlist, add/remove_watch, set_movers, get_overview
Positions: list_positions (exchange money truth when available)
Learning (primary for teach/recall):
  what_have_you_learned — lessons + stats + teach_ok trade cites
  teach — durable lesson from his words (always pass symbol when about a trade)
  delete_lesson — remove a lesson by lesson_id (unteach)
  list_pending_questions / answer_question — max 1–2 open questions
  list_fires / list_trade_reviews (teach_ok only for $ claims)
  learning_stats / agent_ask
Optional: judge_fire, read_chart when he asks about a fire or chart (not the core V1 loop)

When he asks "what have you learned" → call what_have_you_learned first.
When he teaches about a trade: call teach with text AND symbol (and entity_key
or event_id if known from list_trade_reviews / list_fires). Never teach
floating lessons without a symbol when he named a coin.

behaviors (closed set ONLY — never invent tags):
  plan_ok, greed, fomo, hesitant, pride, rule_break, process_skip, false_panic
AD zone (also allowed on behaviors list): ad_met, ad_missed
If he says panic was fine but price never reached his buy area: behaviors
  ["plan_ok","ad_missed"] (or process_skip only if he broke process) and put
  intended AD prices in text, e.g. "wanted 13.0-13.2, low only 13.73 — skipped".
Do NOT invent tags like skip_no_ad / panic_needs_ad_zone / selection_discipline.

When he answers took/skip → answer_question if a pending id is known.

AD discipline from his strategy: panic + breadth; layers; process over lucky green.

Speech: short after tools. No markdown. No live orders. Not financial advice.
"""


def _api_key() -> str:
    return (
        os.getenv("XAI_API_KEY")
        or os.getenv("GROK_API_KEY")
        or os.getenv("VOICE_STT_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()


def _base() -> str:
    return (os.getenv("XAI_API_BASE") or "https://api.x.ai/v1").rstrip("/")


def _stt_post_wav(wav_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    key = _api_key()
    if not key:
        return None, "XAI_API_KEY not set"

    url = f"{_base()}/stt"
    model = os.getenv("XAI_STT_MODEL", "grok-stt")
    files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
    data = {"model": model}

    # Long voice teaches: scale STT timeout with payload (min 90s, max 5 min)
    stt_timeout = max(90, min(300, 60 + len(wav_bytes) // 8000))
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files=files,
            data=data,
            timeout=stt_timeout,
            verify=_CA,
        )
    except Exception as e:
        logger.warning("STT request error: %s", e)
        return None, f"STT request failed: {e}"

    if r.status_code == 200:
        try:
            j = r.json()
        except Exception:
            return None, "STT returned non-JSON body"
        text = (j.get("text") or j.get("transcript") or "").strip()
        if text:
            return text, None
        return None, (
            "STT empty text — speak clearly for ~1–2 seconds (silence yields nothing)"
        )

    body = (r.text or "")[:400]
    url2 = f"{_base()}/audio/transcriptions"
    try:
        r2 = requests.post(
            url2,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": os.getenv("VOICE_STT_MODEL", "whisper-1")},
            timeout=stt_timeout,
            verify=_CA,
        )
        if r2.status_code == 200:
            text = (r2.json().get("text") or "").strip()
            if text:
                return text, None
            return None, "Whisper returned empty text"
        logger.warning(
            "STT failed %s %s / %s %s",
            r.status_code,
            body[:200],
            r2.status_code,
            r2.text[:200],
        )
        return None, f"STT HTTP {r.status_code}: {body}"
    except Exception as e:
        logger.warning("STT failed %s %s (fallback err %s)", r.status_code, body[:200], e)
        return None, f"STT HTTP {r.status_code}: {body}"


def stt_transcribe(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    wav, conv_err = to_wav_16k_mono(
        audio_bytes, filename=filename, content_type=content_type
    )
    if conv_err or not wav:
        return None, conv_err or "Audio conversion failed"
    return _stt_post_wav(wav)


def tts_speak(text: str) -> Optional[bytes]:
    """xAI TTS: POST /v1/tts with text + voice_id + language → audio/mpeg bytes."""
    key = _api_key()
    if not key or not text:
        return None
    # Prefer short spoken replies
    spoken = " ".join(text.strip().split())
    if len(spoken) > 1200:
        spoken = spoken[:1197] + "…"
    voice_id = (
        os.getenv("XAI_TTS_VOICE_ID")
        or os.getenv("XAI_TTS_VOICE")
        or "eve"
    ).strip()
    # legacy env used "ara" / model field — map common aliases
    alias = {
        "ara": "ara",
        "eve": "eve",
        "leo": "leo",
        "rex": "rex",
        "sal": "sal",
    }
    voice_id = alias.get(voice_id.lower(), voice_id)
    language = (os.getenv("XAI_TTS_LANGUAGE") or "en").strip() or "en"
    try:
        r = requests.post(
            f"{_base()}/tts",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "text": spoken,
                "voice_id": voice_id,
                "language": language,
            },
            timeout=60,
            verify=_CA,
        )
        if r.status_code == 200 and r.content:
            ct = (r.headers.get("content-type") or "").lower()
            if "json" in ct:
                j = r.json()
                b64 = j.get("audio") or j.get("data")
                if b64:
                    return base64.b64decode(b64)
                return None
            return r.content
        logger.warning("TTS HTTP %s %s", r.status_code, (r.text or "")[:200])
    except Exception as e:
        logger.warning("TTS failed: %s", e)
    return None


def _sanitize_history(history: Optional[List[dict]]) -> List[dict]:
    """Keep only role/content user+assistant turns for multi-turn continuity."""
    out: List[dict] = []
    if not history:
        return out
    for m in history[-24:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content.strip()[:4000]})
    return out


def chat_with_tools(
    user_text: str,
    history: Optional[List[dict]] = None,
    *,
    voice: bool = False,
) -> Dict[str, Any]:
    """
    Multi-round tool loop. voice=True: fewer rounds, shorter history, snappier replies.
    """
    import time as _time

    t0 = _time.time()
    key = _api_key()
    model = (
        os.getenv("XAI_VOICE_CHAT_MODEL")
        or os.getenv("XAI_CHAT_MODEL")
        or os.getenv("DESK_AGENT_MODEL")
        or "grok-4.5"
    )
    # Voice: keep last 8 turns only (faster prompts)
    hist_cap = 8 if voice else 24
    prior = _sanitize_history(history)[-hist_cap:]
    messages: List[dict] = [{"role": "system", "content": SYSTEM}]
    messages.extend(prior)
    messages.append({"role": "user", "content": user_text})

    tools_run: List[dict] = []
    if not key:
        from ..coach.engine import format_coach_reply

        reply = (
            format_coach_reply(user_text, recent_events=[])
            + "\n\n(No XAI_API_KEY — tools disabled.)"
        )
        hist = prior + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ]
        return {
            "reply": reply,
            "tools_run": [],
            "model": None,
            "history": hist[-24:],
            "timing_ms": {"total": int((_time.time() - t0) * 1000)},
        }

    final_reply = ""
    default_rounds = "4" if voice else "8"
    max_rounds = int(os.getenv("DESK_AGENT_TOOL_ROUNDS", default_rounds) or default_rounds)
    max_rounds = max(2, min(max_rounds, 12 if not voice else 6))
    tool_json_cap = 2500 if voice else 8000
    chat_timeout = 45 if voice else 120

    for _ in range(max_rounds):
        payload = {
            "model": model,
            "messages": messages,
            "tools": TOOL_DEFS,
            "tool_choice": "auto",
            "temperature": 0.2 if voice else 0.25,
        }
        # Nudge model toward single-tool-batch for voice
        if voice:
            payload["messages"] = list(messages)
            # soft instruction via last user (already spoken) — no extra round
        r = requests.post(
            f"{_base()}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=chat_timeout,
            verify=_CA,
        )
        if r.status_code != 200:
            logger.warning("chat %s %s", r.status_code, r.text[:300])
            final_reply = f"Agent API error {r.status_code}. Check XAI_API_KEY / model."
            break
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final_reply = (msg.get("content") or "").strip() or "(empty)"
            break
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = run_tool(name, args)
            tools_run.append({"name": name, "args": args, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": json.dumps(result)[:tool_json_cap],
                }
            )
    else:
        final_reply = (
            "Done."
            if tools_run
            else "Timed out before a final reply."
        )

    if not final_reply and tools_run:
        final_reply = "Done — desk updated."

    # Voice: clamp spoken length
    if voice and final_reply and len(final_reply) > 500:
        final_reply = final_reply[:497] + "…"

    hist = prior + [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": final_reply},
    ]
    return {
        "reply": final_reply,
        "tools_run": tools_run,
        "model": model,
        "history": hist[-24:],
        "timing_ms": {"chat_total": int((_time.time() - t0) * 1000)},
    }


def handle_voice_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "",
    history: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    import time as _time

    t0 = _time.time()
    text, stt_err = stt_transcribe(
        audio_bytes, filename=filename, content_type=content_type
    )
    t_stt = _time.time()
    if not text:
        return {
            "ok": False,
            "error": stt_err
            or "STT failed — set XAI_API_KEY; send WAV (browser encodes if no ffmpeg)",
            "transcript": None,
            "reply": None,
            "tools_run": [],
            "history": _sanitize_history(history),
            "timing_ms": {"stt": int((t_stt - t0) * 1000)},
        }
    out = chat_with_tools(text, history=history, voice=True)
    t_chat = _time.time()
    audio_out = None
    if os.getenv("DESK_VOICE_TTS", "true").lower() in ("1", "true", "yes"):
        raw = tts_speak(out["reply"][:800])
        if raw:
            audio_out = base64.b64encode(raw).decode("ascii")
    t_end = _time.time()
    timing = {
        "stt_ms": int((t_stt - t0) * 1000),
        "chat_ms": int((t_chat - t_stt) * 1000),
        "tts_ms": int((t_end - t_chat) * 1000),
        "total_ms": int((t_end - t0) * 1000),
    }
    logger.info(
        "voice turn stt=%sms chat=%sms tts=%sms total=%sms tools=%s",
        timing["stt_ms"],
        timing["chat_ms"],
        timing["tts_ms"],
        timing["total_ms"],
        len(out.get("tools_run") or []),
    )
    return {
        "ok": True,
        "transcript": text,
        "reply": out["reply"],
        "tools_run": out["tools_run"],
        "model": out.get("model"),
        "history": out.get("history") or [],
        "audio_b64": audio_out,
        "timing_ms": timing,
    }
