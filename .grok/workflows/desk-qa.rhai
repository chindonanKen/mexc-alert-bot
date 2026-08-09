let meta = #{
    name: "desk-qa",
    description: "After AD Desk edits: new features work, UI/UX quality, no regressions",
    when_to_use: "After any AD Desk feature or bugfix — run before deploy or right after",
    phases: [
        #{ title: "Scope", detail: "Diff + what changed" },
        #{ title: "QA panel", detail: "New functions · UI/UX · regressions in parallel" },
        #{ title: "Report", detail: "PASS/FAIL synthesis" },
    ],
};

let panel_schema = #{
    "type": "object",
    "required": ["verdict", "summary", "findings"],
    "properties": #{
        "verdict": #{ "type": "string" },
        "summary": #{ "type": "string" },
        "findings": #{
            "type": "array",
            "maxItems": 12,
            "items": #{
                "type": "object",
                "required": ["area", "severity", "issue", "evidence"],
                "properties": #{
                    "area": #{ "type": "string" },
                    "severity": #{ "type": "string" },
                    "issue": #{ "type": "string" },
                    "evidence": #{ "type": "string" },
                },
            },
        },
    },
};

let report_schema = #{
    "type": "object",
    "required": ["verdict", "summary", "blockers", "follow_ups"],
    "properties": #{
        "verdict": #{ "type": "string" },
        "summary": #{ "type": "string" },
        "blockers": #{ "type": "array", "items": #{ "type": "string" } },
        "follow_ups": #{ "type": "array", "items": #{ "type": "string" } },
        "notes": #{ "type": "array", "items": #{ "type": "string" } },
    },
};

// Optional args.focus: short description of the change under test
let focus = if args == () { () } else { args.focus };
let focus_line = if focus == () {
    "Inspect git diff HEAD~1 (or unstaged/staged) for the latest AD Desk change."
} else {
    "Focus of this run: " + focus + ". Also read git diff for that work."
};

phase("Scope");
let scope_prompt = "";
scope_prompt += "You map the AD Desk change under review. Repo is mexc-alert-bot / mexc-bot.\n";
scope_prompt += focus_line + "\n";
scope_prompt += "Use run_terminal_command for: git status -sb; git log -5 --oneline; git diff HEAD~3 --stat; ";
scope_prompt += "git diff HEAD~1 -- mexc_bot/webapi/ or broader if needed.\n";
scope_prompt += "Use grep/read_file on changed paths under mexc_bot/webapi/, static assets, learning.\n";
scope_prompt += "Return a compact inventory: files changed, new endpoints/UI controls, risk areas ";
scope_prompt += "(alerts table, money_truth, teach_ok, nested FastAPI body models, desk.js handlers).\n";
scope_prompt += "Do not edit files.";

let scope = agent(
    scope_prompt,
    #{ label: "scope-diff", capability_mode: "execute" }
);

let scope_blob = "";
if scope != () && scope.success && scope.output != () {
    scope_blob = json_encode(scope.output);
} else {
    scope_blob = "Scope agent failed or empty — still audit desk code paths from focus.";
}

phase("QA panel");

let p_new = "";
p_new += "ROLE: New-function verifier for AD Desk.\n";
p_new += "CONTEXT (from scope agent):\n" + scope_blob + "\n\n";
p_new += "Use read_file, grep, and run_terminal_command (tests if cheap). Do NOT modify production data or deploy.\n";
p_new += "Verify NEW or CHANGED behavior actually works as designed:\n";
p_new += "1) Trace UI → fetch/api → FastAPI route → store for each new control.\n";
p_new += "2) Nested Pydantic models inside create_app() with future annotations cause body=query 422 — flag any remaining.\n";
p_new += "3) PATCH/POST bodies: module-level models; dataset handlers match data-* attributes.\n";
p_new += "4) Learning edit Save → PATCH /api/learning/lessons/{id}; position flags; teach path if touched.\n";
p_new += "5) If tests exist for the change, run them (python3 -m pytest or make test subset).\n";
p_new += "Verdict PASS only if new paths look wired end-to-end; else FAIL with findings.\n";
p_new += "Empty findings only after reading the code. severity: blocker|major|minor.";

let p_ux = "";
p_ux += "ROLE: UI/UX quality reviewer for AD Desk (Learning, Positions, Overview).\n";
p_ux += "CONTEXT:\n" + scope_blob + "\n\n";
p_ux += "Read desk.js / desk.css / index.html for changed surfaces. Do NOT edit files.\n";
p_ux += "Check: clear labels, edit/save/cancel states, toasts on success/fail, busy/disabled, ";
p_ux += "empty states, HOLD/FREE/AD bands, Needs-you vs teach (not coach theater), mobile rail, ";
p_ux += "no dead buttons, capture/bubble handlers, cache-bust if static changed.\n";
p_ux += "Verdict PASS if UX is coherent for Kenneth daytrading desk; FAIL if save/edit dead or confusing.\n";
p_ux += "severity: blocker|major|minor.";

let p_reg = "";
p_reg += "ROLE: Regression guardian — AD Desk must not break existing functions.\n";
p_reg += "CONTEXT:\n" + scope_blob + "\n\n";
p_reg += "Read-only + optional tests. Check AGENTS.md non-negotiables:\n";
p_reg += "A) alerts table / stable_id crossing / movers never delete targets.\n";
p_reg += "B) money_truth / teach_ok / hold book excluded from AD learn.\n";
p_reg += "C) FEATURE_* defaults OFF in config where required.\n";
p_reg += "D) Existing endpoints still present: positions, flags, learning home, teach, delete lesson, ";
p_reg += "pending answer, overview hierarchy.\n";
p_reg += "E) desk.js wirePosTableOnce / loadMemory / loadPositions not broken by partial edits.\n";
p_reg += "F) Nested body models remaining inside create_app for other POSTs.\n";
p_reg += "Run make test or key unit tests if time allows; note exit codes.\n";
p_reg += "Verdict PASS only if no regression evidence; else FAIL.\n";
p_reg += "severity: blocker|major|minor.";

let panel = parallel([
    #{ prompt: p_new, label: "qa-new-functions", capability_mode: "execute", output_schema: panel_schema },
    #{ prompt: p_ux, label: "qa-ui-ux", capability_mode: "read-only", output_schema: panel_schema },
    #{ prompt: p_reg, label: "qa-regressions", capability_mode: "execute", output_schema: panel_schema },
]);

phase("Report");
let synth = "";
synth += "Synthesize AD Desk QA report for the owner (Kenneth). Be short and actionable.\n";
synth += "Scope blob:\n" + scope_blob + "\n\n";
let i = 0;
for r in panel {
    let name = if i == 0 { "new-functions" } else if i == 1 { "ui-ux" } else { "regressions" };
    synth += "Panel " + name + " success=";
    if r != () && r.success {
        synth += "true\n";
        if r.output != () {
            synth += json_encode(r.output) + "\n\n";
        }
    } else {
        synth += "false (treat as unverified / fail-closed for blockers)\n\n";
    }
    i += 1;
}
synth += "Return verdict PASS|FAIL|PASS_WITH_NOTES, summary, blockers (must-fix), follow_ups, notes.\n";
synth += "FAIL if any panel reported blocker findings or panel failed with no usable output.";

let final = agent(
    synth,
    #{ label: "qa-synth", capability_mode: "read-only", output_schema: report_schema }
);

if final != () && final.success && final.output != () {
    let path = write_scratch_file("desk-qa-report.json", json_encode(final.output));
    complete(#{
        path: path,
        report: final.output,
        summary: final.output.summary,
        verdict: final.output.verdict,
    });
}

complete(#{
    summary: "QA synthesis failed — see panel results",
    verdict: "FAIL",
    panel_ok: panel.len().to_string(),
});
