#!/usr/bin/env python3
"""Mandatory AD Desk QA gate for Grok hooks (PostToolUse + Stop).

Tracks when desk-related files are edited and blocks agent stop until
`desk-qa` has been marked passed for that dirty wave.

State lives under .grok/state/ (gitignored).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

DESK_MARKERS = (
    "mexc_bot/webapi/",
    "mexc_bot/learning/",
    "mexc_bot/webapi/static/",
    ".grok/workflows/desk-qa.rhai",
    "scripts/desk_qa_",
)

EDIT_TOOLS = {
    "search_replace",
    "Write",
    "write",
    "Edit",
    "MultiEdit",
    "str_replace",
    "apply_patch",
}


def _repo_root(cwd: str, workspace: str) -> Path:
    for base in (workspace, cwd):
        if not base:
            continue
        p = Path(base)
        if (p / "AGENTS.md").is_file() or (p / "mexc_bot").is_dir():
            return p
        # walk up a few levels
        for parent in p.parents:
            if (parent / "AGENTS.md").is_file() or (parent / "mexc_bot").is_dir():
                return parent
            if parent == parent.parent:
                break
    return Path(cwd or workspace or ".")


def _state_dir(root: Path) -> Path:
    d = root / ".grok" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_desk_path(path: str, root: Path) -> bool:
    if not path:
        return False
    try:
        p = Path(path)
        if not p.is_absolute():
            rel = path.replace("\\", "/")
        else:
            try:
                rel = str(p.resolve().relative_to(root.resolve())).replace("\\", "/")
            except Exception:
                rel = str(p).replace("\\", "/")
    except Exception:
        rel = path.replace("\\", "/")
    # also accept absolute paths containing markers
    candidates = [rel, path.replace("\\", "/")]
    for c in candidates:
        for m in DESK_MARKERS:
            if m in c or c.startswith(m) or c.endswith(m.rstrip("/")):
                return True
        # bare filenames under static
        if c.endswith("desk.js") or c.endswith("desk.css") or c.endswith("index.html"):
            if "webapi" in c or "static" in c or c in ("desk.js", "desk.css", "index.html"):
                return True
    return False


def _paths_from_tool_input(tool_input: Any) -> list:
    if not isinstance(tool_input, dict):
        return []
    keys = (
        "path",
        "file_path",
        "filePath",
        "target_file",
        "targetFile",
        "file",
    )
    out = []
    for k in keys:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    # some tools pass path in command — skip (too noisy)
    return out


def mark_dirty(root: Path, session_id: str, path: str) -> None:
    st = _state_dir(root)
    dirty = {
        "session_id": session_id or "",
        "path": path,
        "ts": time.time(),
    }
    (st / "desk-qa.dirty").write_text(json.dumps(dirty, indent=2) + "\n", encoding="utf-8")


def mark_pass(root: Path, note: str = "") -> Path:
    st = _state_dir(root)
    dirty_p = st / "desk-qa.dirty"
    dirty_ts = 0.0
    if dirty_p.is_file():
        try:
            dirty_ts = float(json.loads(dirty_p.read_text(encoding="utf-8")).get("ts") or 0)
        except Exception:
            dirty_ts = dirty_p.stat().st_mtime
    payload = {
        "ts": time.time(),
        "dirty_ts": dirty_ts,
        "note": note or "desk-qa passed",
    }
    out = st / "desk-qa.pass"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if dirty_p.is_file():
        dirty_p.unlink()
    return out


def needs_qa(root: Path) -> bool:
    st = _state_dir(root)
    dirty_p = st / "desk-qa.dirty"
    if not dirty_p.is_file():
        return False
    pass_p = st / "desk-qa.pass"
    if not pass_p.is_file():
        return True
    try:
        dirty = json.loads(dirty_p.read_text(encoding="utf-8"))
        passed = json.loads(pass_p.read_text(encoding="utf-8"))
        dirty_ts = float(dirty.get("ts") or 0)
        pass_ts = float(passed.get("ts") or 0)
        return pass_ts < dirty_ts
    except Exception:
        return True


def handle_post_tool(event: Dict[str, Any]) -> int:
    tool = str(event.get("toolName") or event.get("tool_name") or "")
    # Claude aliases already mapped; still accept Write/Edit names
    if tool not in EDIT_TOOLS and tool.lower() not in {t.lower() for t in EDIT_TOOLS}:
        return 0
    root = _repo_root(
        str(event.get("cwd") or ""),
        str(event.get("workspaceRoot") or event.get("workspace_root") or ""),
    )
    tin = event.get("toolInput") or event.get("tool_input") or {}
    session = str(event.get("sessionId") or event.get("session_id") or "")
    for path in _paths_from_tool_input(tin):
        if _is_desk_path(path, root):
            mark_dirty(root, session, path)
            break
    return 0


def handle_stop(event: Dict[str, Any]) -> int:
    reason = str(event.get("reason") or "")
    # Only gate genuine end-of-turn completions
    if reason and reason not in ("end_turn", "EndTurn", ""):
        # session shutdown / channel_closed — do not block
        if reason in ("channel_closed", "shutdown", "SessionEnd"):
            return 0

    root = _repo_root(
        str(event.get("cwd") or ""),
        str(event.get("workspaceRoot") or event.get("workspace_root") or ""),
    )
    if not needs_qa(root):
        return 0

    stop_active = bool(event.get("stopHookActive") or event.get("stop_hook_active"))
    msg = (
        "MANDATORY desk-qa gate: AD Desk files were edited this session and "
        "desk-qa has not been marked passed for that change wave.\n"
        "1) Run workflow `desk-qa` with args.focus describing the change "
        "(workflow tool name=desk-qa, or /workflow desk-qa).\n"
        "2) Fix any blockers from the report.\n"
        "3) Mark pass: python3 scripts/desk_qa_gate.py pass --note 'desk-qa PASS'\n"
        "4) Then finish with a short QA summary (verdict + blockers).\n"
        "Do not skip this while fine-tuning AD Desk — saves rework and tokens later."
    )
    if stop_active:
        # Still block until pass; keep reason short after first loop
        msg = (
            "desk-qa still not passed. Run desk-qa workflow, fix blockers, then: "
            "python3 scripts/desk_qa_gate.py pass --note '…'"
        )

    out = {"decision": "block", "reason": msg}
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[1] == "pass":
        note = ""
        if "--note" in argv:
            i = argv.index("--note")
            if i + 1 < len(argv):
                note = argv[i + 1]
        root = _repo_root(os.getcwd(), os.environ.get("GROK_WORKSPACE", ""))
        p = mark_pass(root, note=note)
        print(f"desk-qa marked PASS → {p}")
        return 0

    if len(argv) >= 2 and argv[1] == "status":
        root = _repo_root(os.getcwd(), os.environ.get("GROK_WORKSPACE", ""))
        print("needs_qa=" + str(needs_qa(root)))
        st = _state_dir(root)
        for name in ("desk-qa.dirty", "desk-qa.pass"):
            f = st / name
            print(f"{name}: {'yes' if f.is_file() else 'no'}")
        return 0

    if len(argv) >= 2 and argv[1] == "dirty":
        # manual mark for testing
        root = _repo_root(os.getcwd(), os.environ.get("GROK_WORKSPACE", ""))
        path = argv[2] if len(argv) > 2 else "mexc_bot/webapi/manual"
        mark_dirty(root, "manual", path)
        print("marked dirty")
        return 0

    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    name = str(
        event.get("hookEventName")
        or event.get("hook_event_name")
        or event.get("event")
        or ""
    ).lower()

    if "post" in name and "tool" in name:
        return handle_post_tool(event)
    if name in ("stop", "subagent_stop", "subagentstop") or name.endswith("stop"):
        # Only gate main agent Stop, not every subagent — subagents would thrash
        if "subagent" in name:
            return 0
        return handle_stop(event)
    # mode from env if hook wraps differently
    mode = os.environ.get("DESK_QA_HOOK_MODE", "")
    if mode == "post":
        return handle_post_tool(event)
    if mode == "stop":
        return handle_stop(event)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
