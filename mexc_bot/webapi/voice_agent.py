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

SYSTEM = """You are the AD Desk continuous voice co-pilot for Kenneth (MEXC AD trader).

You fully control the DESK UI data via tools — not just chat:
- Target alarms/alerts: list / add / update / delete
- Mover watchlist: list / add / remove; movers on/off + threshold + lookback
- Journal positions: list / open / close
- Memory: list fires, label took/skip/watch
- Intel: investigations + news
- Overview + propose_trade (paper AD plan)

Strategy:
- Prefer sharp panic dumps with market-wide heat and volume.
- Isolated single-name dumps: bias no-trade until intel is clean.
- Scale in layers; never all-in. Journal before ego.

Conversation style:
- This is a continuous multi-turn conversation — use prior turns.
- When the user asks to change the desk, CALL TOOLS immediately (do not only describe).
- After tools run, confirm briefly what changed.
- Keep spoken replies short (1–3 sentences) unless asked for detail.
- No live exchange order placement. Journal / propose only.
- Not financial advice.
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

    try:
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files=files,
            data=data,
            timeout=90,
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
            timeout=90,
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
    key = _api_key()
    if not key or not text:
        return None
    try:
        r = requests.post(
            f"{_base()}/tts",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "text": text[:4000],
                "voice": os.getenv("XAI_TTS_VOICE", "ara"),
                "model": os.getenv("XAI_TTS_MODEL", "grok-tts"),
            },
            timeout=60,
            verify=_CA,
        )
        if r.status_code == 200:
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                j = r.json()
                b64 = j.get("audio") or j.get("data")
                if b64:
                    return base64.b64decode(b64)
            return r.content
    except Exception as e:
        logger.debug("TTS skip: %s", e)
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


def chat_with_tools(user_text: str, history: Optional[List[dict]] = None) -> Dict[str, Any]:
    """
    Multi-round tool loop (max 8). Returns reply, tools_run, model, history_out.
    history_out is client-safe user/assistant turns including this exchange.
    """
    key = _api_key()
    model = os.getenv("XAI_CHAT_MODEL") or os.getenv("DESK_AGENT_MODEL") or "grok-4.5"
    prior = _sanitize_history(history)
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
        }

    final_reply = ""
    max_rounds = int(os.getenv("DESK_AGENT_TOOL_ROUNDS", "8") or "8")
    max_rounds = max(2, min(max_rounds, 12))

    for _ in range(max_rounds):
        payload = {
            "model": model,
            "messages": messages,
            "tools": TOOL_DEFS,
            "tool_choice": "auto",
            "temperature": 0.25,
        }
        r = requests.post(
            f"{_base()}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
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
                    "content": json.dumps(result)[:8000],
                }
            )
    else:
        final_reply = (
            "Done — applied tool updates."
            if tools_run
            else "Timed out before a final reply."
        )

    if not final_reply and tools_run:
        final_reply = "Done — desk updated."

    hist = prior + [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": final_reply},
    ]
    return {
        "reply": final_reply,
        "tools_run": tools_run,
        "model": model,
        "history": hist[-24:],
    }


def handle_voice_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "",
    history: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    text, stt_err = stt_transcribe(
        audio_bytes, filename=filename, content_type=content_type
    )
    if not text:
        return {
            "ok": False,
            "error": stt_err
            or "STT failed — set XAI_API_KEY; send WAV (browser encodes if no ffmpeg)",
            "transcript": None,
            "reply": None,
            "tools_run": [],
            "history": _sanitize_history(history),
        }
    out = chat_with_tools(text, history=history)
    audio_out = None
    if os.getenv("DESK_VOICE_TTS", "true").lower() in ("1", "true", "yes"):
        raw = tts_speak(out["reply"][:1500])
        if raw:
            audio_out = base64.b64encode(raw).decode("ascii")
    return {
        "ok": True,
        "transcript": text,
        "reply": out["reply"],
        "tools_run": out["tools_run"],
        "model": out.get("model"),
        "history": out.get("history") or [],
        "audio_b64": audio_out,
    }
