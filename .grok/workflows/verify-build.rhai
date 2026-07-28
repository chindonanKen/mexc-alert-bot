let meta = #{
    name: "verify-build",
    description: "Safety + learning integrity gate for mexc-alert-bot builds",
    when_to_use: "After code changes, before deploy, or when user asks if the bot is safe",
    phases: [
        #{ title: "Tests", detail: "Run unit suites" },
        #{ title: "Safety", detail: "Alerts isolation and flags" },
        #{ title: "Learning", detail: "No false info / coach honesty" },
        #{ title: "Report", detail: "PASS/FAIL verdict" },
    ],
};

let report_schema = #{
    "type": "object",
    "required": ["verdict", "summary", "blockers"],
    "properties": #{
        "verdict": #{ "type": "string" },
        "summary": #{ "type": "string" },
        "blockers": #{ "type": "array", "items": #{ "type": "string" } },
        "notes": #{ "type": "array", "items": #{ "type": "string" } },
    },
};

phase("Tests");
let t = agent(
    "You are verifying mexc-alert-bot. Run the local test gate.\n"
        + "1. Prefer: bash scripts/verify_build.sh from repo root.\n"
        + "2. If that fails on python version, run: python3 tests/test_learning_events.py and try make test.\n"
        + "3. Report exact exit codes and any traceback snippets.\n"
        + "4. List which of these passed: crossing, v3 futures/movers, enrichment, learning.\n"
        + "Use run_terminal_command. Working directory is the repo root.\n"
        + "Return a short factual report of results only.",
    #{ label: "run-tests", capability_mode: "execute" }
);

phase("Safety");
let s = agent(
    "Read-only safety audit of mexc-alert-bot after learning/V4 work.\n"
        + "Use grep and read_file. Do NOT modify files.\n"
        + "Check ALL of:\n"
        + "A) mexc_bot/learning, mexc_bot/coach, mexc_bot/news (if exists) must never DELETE FROM alerts or call remove_alerts.\n"
        + "B) PriceMonitor._last_prices still keyed by (user_id, stable_id) — read monitor.py.\n"
        + "C) FEATURE_LEARNING, FEATURE_NEWS_MONITOR, FEATURE_VOICE default false in config.py and .env.example.\n"
        + "D) MoverScanner still documents never touching AlertStore; event_store log is best-effort after notify.\n"
        + "E) No .env or bot tokens in git status staged files.\n"
        + "Report PASS/FAIL per item with file evidence.",
    #{ label: "safety-audit", capability_mode: "read-only" }
);

phase("Learning");
let l = agent(
    "Audit learning honesty (no false info) in mexc-alert-bot.\n"
        + "Read: docs/V4_TRADING_ASSISTANT.md, docs/VERIFY_BUILD.md, mexc_bot/coach/engine.py, mexc_bot/learning/store.py, mexc_bot/learning/integrity.py.\n"
        + "Verify:\n"
        + "1) Events only from real sources (mover_peak, mover_step, target, manual journal).\n"
        + "2) format_coach_reply does not invent fills; empty events → says none logged.\n"
        + "3) News: if not implemented, coach must not claim confirmed delist/hack as fact.\n"
        + "4) test_learning_events.py covers alerts isolation (learning ops leave alerts rows).\n"
        + "5) Outcome poller soft-fails; does not touch alerts.\n"
        + "Report any path that could invent user trades or news.",
    #{ label: "learning-honesty", capability_mode: "read-only" }
);

phase("Report");
let tests_ok = t != () && t.success;
let safety_ok = s != () && s.success;
let learn_ok = l != () && l.success;

let synth_prompt = "";
synth_prompt += "Synthesize a final verification report for mexc-alert-bot.\n";
synth_prompt += "Tests agent success=" + tests_ok.to_string() + "\n";
if t != () && t.output != () {
    synth_prompt += "Tests output:\n" + json_encode(t.output) + "\n\n";
}
synth_prompt += "Safety agent success=" + safety_ok.to_string() + "\n";
if s != () && s.output != () {
    synth_prompt += "Safety output:\n" + json_encode(s.output) + "\n\n";
}
synth_prompt += "Learning agent success=" + learn_ok.to_string() + "\n";
if l != () && l.output != () {
    synth_prompt += "Learning output:\n" + json_encode(l.output) + "\n\n";
}
synth_prompt += "Rules: verdict PASS only if tests ran green AND no safety/learning blockers.\n";
synth_prompt += "If any agent failed, verdict FAIL and list blockers.\n";
synth_prompt += "Return structured verdict/summary/blockers/notes.";

let r = agent(
    synth_prompt,
    #{ label: "final-verdict", capability_mode: "read-only", output_schema: report_schema }
);

if r != () && r.success && r.output != () {
    let path = write_scratch_file("verify-build-report.md", json_encode(r.output));
    complete(#{ path: path, report: r.output });
}
complete(#{
    summary: "Verification incomplete — one or more agents failed",
    tests_ok: tests_ok,
    safety_ok: safety_ok,
    learn_ok: learn_ok,
});
