"""Grok / xAI voice + tool agent for the desk.

Uses:
- STT: POST https://api.x.ai/v1/stt (or OpenAI-compatible fallback)
- LLM tools: POST https://api.x.ai/v1/chat/completions with grok model
- Optional TTS: POST https://api.x.ai/v1/tts

Live exchange orders are NOT enabled here — tools mutate desk/journal/alerts only.
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

logger = logging.getLogger(__name__)

SYSTEM = """You are the AD Desk voice co-pilot for Kenneth, a MEXC day/swing trader.

Strategy (Average Drop / panic):
- Prefer sharp panic dumps with market-wide heat and volume.
- Isolated single-name dumps: check delist/hack risk; bias no-trade.
- Scale in 5–10 exponential layers; never all-in; pride/greed are enemies.
- Workflow: plan levels → alarms wait → engage when tools fire.

You control the DESK terminal via tools: alerts, watchlist, movers settings,
journal positions (paper log), label fires, propose trades.

Rules:
- Prefer propose_trade / open_position (journal) over anything that sounds like live orders.
- There is NO tool to place live exchange orders. If user asks to buy on MEXC live, explain
  they must enable exchange UI or future DESK_ALLOW_LIVE_ORDERS — and still journal the plan.
- Confirm destructive deletes briefly in your spoken/text reply after doing them.
- Be concise, decisive, AD-native. Not financial advice.
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


def stt_transcribe(audio_bytes: bytes, filename: str = "audio.webm") -> Optional[str]:
    key = _api_key()
    if not key:
        return None
    url = f"{_base()}/stt"
    # Try xAI STT; fallback OpenAI whisper path if xAI base is openai
    try:
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        data = {"model": os.getenv("XAI_STT_MODEL", "grok-stt")}
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files=files,
            data=data,
            timeout=90,
            verify=_CA,
        )
        if r.status_code == 200:
            j = r.json()
            return (j.get("text") or j.get("transcript") or "").strip() or None
        # OpenAI-compatible whisper
        url2 = f"{_base()}/audio/transcriptions"
        r2 = requests.post(
            url2,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename, audio_bytes)},
            data={"model": os.getenv("VOICE_STT_MODEL", "whisper-1")},
            timeout=90,
            verify=_CA,
        )
        if r2.status_code == 200:
            return (r2.json().get("text") or "").strip() or None
        logger.warning("STT failed %s %s / %s %s", r.status_code, r.text[:200], r2.status_code, r2.text[:200])
    except Exception as e:
        logger.warning("STT error: %s", e)
    return None


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
            # may be audio bytes or json with b64
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


def chat_with_tools(user_text: str, history: Optional[List[dict]] = None) -> Dict[str, Any]:
    """
    One-shot tool loop (max 4 rounds). Returns {reply, tools_run, transcript}.
    """
    key = _api_key()
    model = os.getenv("XAI_CHAT_MODEL") or os.getenv("DESK_AGENT_MODEL") or "grok-3"
    messages: List[dict] = [{"role": "system", "content": SYSTEM}]
    if history:
        messages.extend(history[-12:])
    messages.append({"role": "user", "content": user_text})

    tools_run: List[dict] = []
    if not key:
        # offline rule path
        from ..coach.engine import format_coach_reply

        return {
            "reply": format_coach_reply(user_text, recent_events=[])
            + "\n\n(No XAI_API_KEY — tools disabled. Set XAI_API_KEY for Grok voice agent.)",
            "tools_run": [],
            "model": None,
        }

    for _ in range(4):
        payload = {
            "model": model,
            "messages": messages,
            "tools": TOOL_DEFS,
            "tool_choice": "auto",
            "temperature": 0.3,
        }
        r = requests.post(
            f"{_base()}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
            verify=_CA,
        )
        if r.status_code != 200:
            logger.warning("chat %s %s", r.status_code, r.text[:300])
            return {
                "reply": f"Agent API error {r.status_code}. Check XAI_API_KEY / model.",
                "tools_run": tools_run,
                "model": model,
            }
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return {
                "reply": (msg.get("content") or "").strip() or "(empty)",
                "tools_run": tools_run,
                "model": model,
            }
        messages.append(msg)
        for tc in tool_calls:
            fn = (tc.get("function") or {})
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
                    "content": json.dumps(result),
                }
            )
    return {
        "reply": "Done — ran tools. Ask if you need a summary.",
        "tools_run": tools_run,
        "model": model,
    }


def handle_voice_audio(audio_bytes: bytes, filename: str = "audio.webm") -> Dict[str, Any]:
    text = stt_transcribe(audio_bytes, filename=filename)
    if not text:
        return {
            "ok": False,
            "error": "STT failed — set XAI_API_KEY and ensure /v1/stt is available",
            "transcript": None,
            "reply": None,
            "tools_run": [],
        }
    out = chat_with_tools(text)
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
        "audio_b64": audio_out,
    }
