/* AD Desk v2.1 — HTTPS-first voice + full desk control */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const escapeHtml = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const state = {
    token: localStorage.getItem("desk_token") || "",
    view: "overview",
    chunks: [],
    recording: false,
    inCall: false,
    muteMic: false,
    muteSpk: false,
    agentHistory: [],
    busy: false,
    speakingHeard: false,
    silenceMs: 0,
  };

  // VAD: commit after real speech + sustained silence.
  // Long teaches allowed — no hard 60s cut mid-sentence.
  const VAD = {
    speakRms: 0.038,
    silenceRms: 0.02,
    endSilenceMs: 4500, // pause to think without ending the turn
    minSpeechMs: 550,
    speechHoldMs: 180,
    // Cap from when *speech* started (not mic open). Safety only.
    maxSpeechMs: 15 * 60 * 1000, // 15 minutes if needed
    pollMs: 70,
    postReplyDataMs: 1200,
    emptyBackoffMs: 1000,
  };

  const HIST_KEY = "desk_agent_history_v1";
  try {
    const saved = JSON.parse(localStorage.getItem(HIST_KEY) || "[]");
    if (Array.isArray(saved)) state.agentHistory = saved.slice(-24);
  } catch (_) {}

  const qp = new URLSearchParams(location.search);
  if (qp.get("token")) {
    state.token = qp.get("token");
    localStorage.setItem("desk_token", state.token);
    history.replaceState({}, "", location.pathname);
  }

  function headers(json = true) {
    const h = {};
    if (json) h["Content-Type"] = "application/json";
    if (state.token) h["X-Desk-Token"] = state.token;
    return h;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { ...headers(!(opts.body instanceof FormData)), ...(opts.headers || {}) },
    });
    if (res.status === 401) {
      const t = prompt("Desk API token (DESK_API_TOKEN):");
      if (t) {
        state.token = t;
        localStorage.setItem("desk_token", t);
        return api(path, opts);
      }
      throw new Error("Unauthorized");
    }
    if (!res.ok) {
      let msg = await res.text();
      try {
        const j = JSON.parse(msg);
        const d = j.detail != null ? j.detail : j;
        if (typeof d === "string") msg = d;
        else if (Array.isArray(d))
          msg = d
            .map((x) =>
              typeof x === "string"
                ? x
                : x.msg || x.message || JSON.stringify(x)
            )
            .join("; ");
        else if (d && typeof d === "object") msg = JSON.stringify(d);
        else msg = String(d);
      } catch (_) {}
      throw new Error(msg || res.statusText);
    }
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res.text();
  }

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (el.hidden = true), 2800);
  }

  const fmtPx = (n) => {
    if (n == null || Number.isNaN(+n)) return "—";
    const x = +n;
    if (x >= 1000) return x.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (x >= 1) return x.toFixed(4);
    return x.toPrecision(4);
  };
  const fmtChg = (n) =>
    n == null ? "—" : (Number(n) >= 0 ? "+" : "") + Number(n).toFixed(2) + "%";
  const fmtTime = (ts) =>
    ts
      ? new Date(ts * 1000).toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";

  function table(heads, body) {
    if (!body)
      return '<div class="empty">No data yet — bot writes SQLite as you trade.</div>';
    return `<table class="data"><thead><tr>${heads
      .map((h) => `<th>${h}</th>`)
      .join("")}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function setView(name) {
    state.view = name;
    $$(".view").forEach((v) => v.classList.remove("on"));
    $$(".nav button").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === name)
    );
    const el = $(`#view-${name}`);
    if (el) el.classList.add("on");
    const titles = {
      overview: "Overview",
      targets: "Targets",
      movers: "Movers",
      positions: "Positions",
      pnl: "PnL",
      memory: "Learning",
      intel: "Intel",
      voice: "Voice log",
      roadmap: "Roadmap",
      playbook: "Playbook",
    };
    $("#title").textContent = titles[name] || "Desk";
    const sub = $("#subtitle");
    if (sub) {
      if (name === "memory") {
        sub.textContent = "You teach. Agent stores cases — not advice.";
        sub.hidden = false;
      } else {
        sub.textContent = "";
        sub.hidden = true;
      }
    }
  }

  function renderMajors(_majors) {
    /* majors strip removed for a cleaner focus UI */
  }

  function rankEmpty(msg) {
    return `<div class="rank-empty">${msg}</div>`;
  }

  function escHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function updateLearningNavBadge(count) {
    const badge = $("#navLearnBadge");
    const btn = $("#navLearning");
    if (!badge) return;
    const n = Math.max(0, Number(count) || 0);
    if (n > 0) {
      badge.hidden = false;
      badge.textContent = String(n > 9 ? "9+" : n);
      if (btn) btn.classList.add("has-pending");
    } else {
      badge.hidden = true;
      badge.textContent = "0";
      if (btn) btn.classList.remove("has-pending");
    }
  }

  async function loadOverview() {
    const d = await api("/api/overview");
    const h = d.hierarchy || {};

    // Needs you — pending engagement only (max 2)
    const needsEl = $("#ovNeedsYou");
    if (needsEl) {
      const ny = h.needs_you || d.needs_you || {};
      const qs = (ny.pending_questions || []).slice(0, 2);
      updateLearningNavBadge(ny.count != null ? ny.count : qs.length);
      if (!qs.length) {
        needsEl.hidden = true;
        needsEl.innerHTML = "";
      } else {
        needsEl.hidden = false;
        let html = `<div class="ov-needs-h">Needs you <button type="button" class="btn soft sm" data-jump="memory">Learning</button></div>`;
        qs.forEach((q) => {
          const meta = [
            q.symbol || (q.event && q.event.symbol) || "—",
            q.velocity_band || (q.event && q.event.velocity_band) || "",
            q.drop_pct != null
              ? Number(q.drop_pct).toFixed(1) + "%"
              : q.event && q.event.drop_pct != null
                ? Number(q.event.drop_pct).toFixed(1) + "%"
                : "",
            q.fire_price != null
              ? "@" + q.fire_price
              : q.event && q.event.price != null
                ? "@" + q.event.price
                : "",
            q.fire_ts || (q.event && q.event.ts)
              ? fmtTime(q.fire_ts || q.event.ts)
              : "",
          ]
            .filter(Boolean)
            .join(" · ");
          html += `<div class="ov-needs-row">
            <div class="ov-needs-meta">${meta}</div>
            <div class="ov-needs-q">${(q.question || "Took or skip?").slice(0, 180)}</div>
            <div class="row-gap">
              <button type="button" class="btn sm" data-ans="${q.id}" data-act="took">Took</button>
              <button type="button" class="btn soft sm" data-ans="${q.id}" data-act="skip">Skip</button>
              <button type="button" class="btn soft sm" data-dismiss-q="${q.id}">Dismiss</button>
            </div>
          </div>`;
        });
        needsEl.innerHTML = html;
        $$("[data-ans]", needsEl).forEach((b) =>
          b.addEventListener("click", async () => {
            try {
              await api("/api/learning/answer", {
                method: "POST",
                body: JSON.stringify({
                  question_id: +b.dataset.ans,
                  action: b.dataset.act,
                  answer_text: "overview",
                }),
              });
              toast("Saved");
              loadOverview();
            } catch (e) {
              toast(e.message);
            }
          })
        );
        $$("[data-dismiss-q]", needsEl).forEach((b) =>
          b.addEventListener("click", async () => {
            try {
              await api("/api/learning/answer", {
                method: "POST",
                body: JSON.stringify({
                  question_id: +b.getAttribute("data-dismiss-q"),
                  dismiss: true,
                }),
              });
              loadOverview();
            } catch (e) {
              toast(e.message);
            }
          })
        );
        $$("[data-jump]", needsEl).forEach((b) =>
          b.addEventListener("click", () => {
            setView(b.dataset.jump);
            refreshAll();
          })
        );
      }
    }

    // Bad intel strip — always top 5 delist/scam/hack/… (any age = reminder)
    const newsEl = $("#ovBookNews");
    if (newsEl) {
      const bn = h.book_news || [];
      if (!bn.length) {
        newsEl.hidden = false;
        newsEl.innerHTML =
          `<div class="ov-news-h">Book intel · bad news</div>` +
          rankEmpty("No delist/scam/hack items in cache yet — open Intel radar");
      } else {
        newsEl.hidden = false;
        newsEl.innerHTML =
          `<div class="panel-h ov-news-head">
            <h3 class="ov-news-h">Book intel · bad news</h3>
            <span class="mute sm">Top 5 · delist/scam/hack · even if old</span>
            <button type="button" class="btn soft sm" data-jump="intel">All intel</button>
          </div>` +
          bn
            .slice(0, 5)
            .map((n) => {
              const cls = (n.class || n.severity || "intel").toString().toUpperCase();
              const age = n.age_label || fmtTime(n.ts) || "";
              const hit = n.book_hit ? " · on book" : "";
              const sym =
                n.symbol ||
                (Array.isArray(n.bases) && n.bases.length
                  ? n.bases.slice(0, 4).join(",")
                  : "—");
              const title = escHtml(String(n.title || "—").slice(0, 140));
              const link = n.url
                ? `<a class="ov-news-link" href="${escHtml(
                    n.url
                  )}" target="_blank" rel="noopener">open</a>`
                : "";
              return `<div class="ov-news-row${n.book_hit ? " on-book" : ""}">
              <span class="ov-news-sev sev-${escHtml(
                cls.toLowerCase().slice(0, 12)
              )}">${escHtml(cls.slice(0, 10))}</span>
              <div class="ov-news-body">
                <div class="ov-news-title">${title}</div>
                <div class="ov-news-meta">${escHtml(String(sym).slice(0, 48))} · ${escHtml(
                String(n.source || "")
              )} · ${escHtml(age)}${hit}${link ? " · " + link : ""}</div>
              </div>
            </div>`;
            })
            .join("");
        $$("[data-jump]", newsEl).forEach((b) =>
          b.addEventListener("click", () => {
            setView(b.dataset.jump);
            refreshAll();
          })
        );
      }
    }

    function posUnder(pos) {
      if (!pos) return "";
      const up = pos.upnl_pct;
      const upS =
        up != null
          ? `<span class="${up >= 0 ? "up" : "dn"}">${up >= 0 ? "+" : ""}${Number(
              up
            ).toFixed(1)}%</span>`
          : "—";
      return `<div class="cmd-pos-under">pos avg ${
        pos.entry != null ? fmtPx(pos.entry) : "—"
      } · mark ${pos.mark != null ? fmtPx(pos.mark) : "—"} · ${upS}${
        pos.size_remaining != null ? " · qty " + Number(pos.size_remaining).toFixed(2) : ""
      }${pos.hold_hours != null ? " · " + pos.hold_hours + "h" : ""}</div>`;
    }

    function ageLabel(sec) {
      if (sec == null || isNaN(sec)) return "";
      if (sec < 60) return Math.round(sec) + "s ago";
      if (sec < 3600) return Math.round(sec / 60) + "m ago";
      return (sec / 3600).toFixed(1) + "h ago";
    }

    const tt = h.top_targets || [];
    $("#ovTopTargets").innerHTML = tt.length
      ? tt
          .map(
            (a, i) => `<div class="cmd-row">
            <div class="cmd-row-main">
              <span class="cmd-rank">${String(i + 1).padStart(2, "0")}</span>
              <div>
                <div class="cmd-sym">${a.symbol}</div>
                <div class="cmd-meta">${(a.market || "spot").toString().slice(0, 1).toUpperCase()} · fired ${
              ageLabel(a.age_seconds) || fmtTime(a.fired_at || a.ts)
            }</div>
                ${posUnder(a.position)}
              </div>
            </div>
            <div class="cmd-px">${fmtPx(a.price)}</div>
          </div>`
          )
          .join("")
      : rankEmpty("No target fires yet");

    const tm = h.top_movers || [];
    $("#ovTopMovers").innerHTML = tm.length
      ? tm
          .map(
            (e, i) => `<div class="cmd-row hot">
            <div class="cmd-row-main">
              <span class="cmd-rank">${String(i + 1).padStart(2, "0")}</span>
              <div>
                <div class="cmd-sym">${e.symbol}</div>
                <div class="cmd-meta">${e.velocity_band || e.mode || "mover"} · ${
              ageLabel(e.age_seconds) || fmtTime(e.fired_at || e.ts)
            }</div>
                ${posUnder(e.position)}
              </div>
            </div>
            <div class="cmd-px dn">${
              e.drop_pct != null
                ? Number(e.drop_pct).toFixed(1) + "%"
                : e.move_1h_pct != null
                  ? Number(e.move_1h_pct).toFixed(1) + "%"
                  : "—"
            }</div>
          </div>`
          )
          .join("")
      : rankEmpty("No mover fires in the last hour");

    const pos = h.positions || d.positions || [];
    $("#ovPos").innerHTML = pos.length
      ? pos
          .map((p) => {
            const free = p.free_coins
              ? `<span class="pos-free">FREE</span>`
              : "";
            const hero =
              p.free_coins && p.remaining_mark_usd != null
                ? "$" + Number(p.remaining_mark_usd).toFixed(0)
                : p.upnl_usd_est != null
                  ? (Number(p.upnl_usd_est) >= 0 ? "+$" : "−$") +
                    Math.abs(Number(p.upnl_usd_est)).toFixed(0)
                  : p.realized_pnl_usd != null
                    ? (Number(p.realized_pnl_usd) >= 0 ? "+$" : "−$") +
                      Math.abs(Number(p.realized_pnl_usd)).toFixed(0)
                    : "—";
            const flow = [
              p.bought_usd != null ? "in $" + Number(p.bought_usd).toFixed(0) : null,
              p.sold_usd != null ? "out $" + Number(p.sold_usd).toFixed(0) : null,
              p.remaining_mark_usd != null && !p.free_coins
                ? "held $" + Number(p.remaining_mark_usd).toFixed(0)
                : null,
            ]
              .filter(Boolean)
              .join(" · ");
            return `<div class="cmd-row pos">
            <div class="cmd-row-main">
              <div>
                <div class="cmd-sym">${escHtml(String(p.symbol || ""))} ${free}</div>
                <div class="cmd-meta">${flow || "—"}</div>
                <div class="cmd-meta">${
                  p.hold_hours != null ? p.hold_hours + "h" : ""
                }</div>
              </div>
            </div>
            <div class="cmd-side">
              <div class="cmd-px mono">${hero}</div>
            </div>
          </div>`;
          })
          .join("")
      : rankEmpty("No open positions");

    // Isolated-dump investigations only (bad news lives in Book intel · bad news above)
    const intelEl = $("#ovBookIntel");
    if (intelEl) {
      const bi = h.book_intel || [];
      if (!bi.length) {
        intelEl.hidden = true;
        intelEl.innerHTML = "";
      } else {
        intelEl.hidden = false;
        intelEl.innerHTML =
          `<div class="panel-h"><h3>Isolated dump probes</h3><button type="button" class="btn soft sm" data-jump="intel">Open</button></div>` +
          bi
            .map(
              (i) => `<div class="cmd-row">
              <div class="cmd-row-main">
                <div class="cmd-sym">${escHtml(i.symbol || "—")}</div>
                <div class="cmd-meta">${escHtml(
                  i.verdict || i.velocity_band || "intel"
                )}${
                i.drop_pct != null
                  ? " · " + Number(i.drop_pct).toFixed(1) + "%"
                  : ""
              }</div>
              </div>
              <div class="cmd-px">${
                i.confidence != null ? Number(i.confidence).toFixed(2) : "—"
              }</div>
            </div>`
            )
            .join("");
        $$("[data-jump]", intelEl).forEach((b) =>
          b.addEventListener("click", () => setView(b.dataset.jump))
        );
      }
    }

    // Agent memory strip only when there are real lessons (sparse Overview)
    const learnedEl = $("#ovAgentLearned");
    if (learnedEl) {
      const ny = h.needs_you || d.needs_you || {};
      const hasLessons = ny.has_lessons || (d.hierarchy && d.hierarchy.needs_you && d.hierarchy.needs_you.has_lessons);
      const summary =
        h.agent_summary || d.agent_summary || d.what_learned_reply || "";
      if (hasLessons && summary && String(summary).trim()) {
        learnedEl.hidden = false;
        learnedEl.innerHTML = `<div class="panel-h"><h3>Agent memory</h3><button type="button" class="btn soft sm" data-jump="memory">Learning</button></div>
          <pre class="learn-recall-sm">${escHtml(String(summary).slice(0, 420))}</pre>`;
        $$("[data-jump]", learnedEl).forEach((b) =>
          b.addEventListener("click", () => {
            setView(b.dataset.jump);
            refreshAll();
          })
        );
      } else {
        learnedEl.hidden = true;
        learnedEl.innerHTML = "";
      }
    }
  }

  function posOutcomeBadge(p) {
    const isOpen = p.status === "open" || p.is_open;
    if (isOpen) {
      if (p.is_hold || p.position_book === "hold") {
        return `<span class="pos-outcome hold" title="Long-term invest — excluded from AD learning">HOLD</span>`;
      }
      if (p.free_coins) {
        const src = p.free_coins_source === "manual" ? " · manual" : "";
        return `<span class="pos-outcome free" title="Principal recovered — free inventory${src}">FREE</span>`;
      }
      if (p.free_coins_status === "near_free") {
        return `<span class="pos-outcome near" title="Almost principal recovered">NEAR</span>`;
      }
      return `<span class="pos-outcome open">OPEN</span>`;
    }
    const o = (p.outcome || p.status || "").toLowerCase();
    if (o === "success") return `<span class="pos-outcome success">WIN</span>`;
    if (o === "miss") return `<span class="pos-outcome miss">MISS</span>`;
    return `<span class="pos-outcome flat">FLAT</span>`;
  }

  function posMarketPill(m) {
    const x = (m || "").toLowerCase();
    if (x === "futures") return `<span class="pos-mkt fut">FUT</span>`;
    if (x === "spot") return `<span class="pos-mkt spot">SPOT</span>`;
    return `<span class="pos-mkt">?</span>`;
  }

  function _usd(n, signed) {
    if (n == null || Number.isNaN(Number(n))) return "—";
    const v = Number(n);
    if (signed) {
      return (v >= 0 ? "+$" : "−$") + Math.abs(v).toFixed(0);
    }
    return "$" + Math.abs(v).toFixed(0);
  }

  function _holdWhen(p, isOpen) {
    if (isOpen) {
      if (p.hold_hours == null) return "—";
      return p.hold_hours >= 24
        ? (p.hold_hours / 24).toFixed(1) + "d"
        : p.hold_hours + "h";
    }
    if (p.closed_ago_seconds != null) {
      const sec = p.closed_ago_seconds;
      if (sec < 3600) return Math.round(sec / 60) + "m ago";
      if (sec < 86400) return (sec / 3600).toFixed(1) + "h ago";
      return (sec / 86400).toFixed(1) + "d ago";
    }
    if (p.hold_hours != null) return "held " + p.hold_hours + "h";
    return "—";
  }

  function _fillUsd(o) {
    const q =
      o.quote_qty != null
        ? Number(o.quote_qty)
        : o.price != null && o.qty != null
          ? Number(o.price) * Number(o.qty)
          : null;
    return q != null && !Number.isNaN(q) ? "$" + q.toFixed(0) : "—";
  }

  function posCardHtml(p) {
    const isOpen = p.status === "open" || p.is_open;
    const entry = p.entry_display != null ? p.entry_display : p.entry_avg;
    const exit_ = p.exit_avg;
    const mark = p.mark_price;
    const bought = p.bought_usd != null ? Number(p.bought_usd) : null;
    const sold = p.sold_usd != null ? Number(p.sold_usd) : null;
    const held = p.remaining_mark_usd != null ? Number(p.remaining_mark_usd) : null;
    const real = p.realized_pnl_usd != null ? Number(p.realized_pnl_usd) : null;
    const cashBanked =
      bought != null && sold != null ? sold - bought : null;
    const posId =
      p.entity_key ||
      p.exchange_position_id ||
      `${p.market || "?"}:${p.symbol}:${p.status}:${p.opened_at || ""}:${p.closed_at || ""}`;

    // Hero money (token 3): free bag → held $; open → uPnL $; closed → realized $
    let heroMain = "—";
    let heroSub = "";
    let heroCls = "mute";
    if (isOpen && p.free_coins && held != null) {
      heroMain = _usd(held, false);
      heroCls = "up";
      if (cashBanked != null) heroSub = "cash " + _usd(cashBanked, true);
    } else if (isOpen) {
      if (p.upnl_usd_est != null) {
        heroMain = _usd(p.upnl_usd_est, true);
        heroCls = Number(p.upnl_usd_est) >= 0 ? "up" : "dn";
      }
      if (p.upnl_pct != null) {
        heroSub =
          (Number(p.upnl_pct) >= 0 ? "+" : "") +
          Number(p.upnl_pct).toFixed(1) +
          "%";
      }
    } else {
      if (real != null) {
        heroMain = _usd(real, true);
        heroCls = real >= 0 ? "up" : "dn";
      }
      if (p.realized_pnl_pct != null) {
        heroSub =
          (Number(p.realized_pnl_pct) >= 0 ? "+" : "") +
          Number(p.realized_pnl_pct).toFixed(1) +
          "%";
      }
    }

    // Flow: In · Out · Held (open) or In · Out · PnL (closed)
    const flow3k = isOpen ? "Held" : "PnL";
    const flow3v = isOpen
      ? held != null
        ? _usd(held, false)
        : "—"
      : real != null
        ? _usd(real, true)
        : "—";

    const when = _holdWhen(p, isOpen);
    const layers =
      p.n_buys || p.n_sells
        ? `B${p.n_buys || 0}/S${p.n_sells || 0}`
        : "";

    const truth =
      p.exchange_history || p.money_truth === "exchange"
        ? `<span class="pos-src" title="Exchange history">EXCH</span>`
        : p.exchange_hold || p.money_truth === "spot_balance"
          ? `<span class="pos-src" title="Live / balance">LIVE</span>`
          : p.money_truth === "fill_recon_unverified" || p.verified === false
            ? `<span class="pos-src" title="Fill residual">FILL?</span>`
            : "";

    const buys = (p.buy_orders || [])
      .map(
        (o) =>
          `<div class="pos-layer buy"><span>BUY</span><span class="pos-layer-usd">${_fillUsd(
            o
          )}</span><span>@ ${
            o.price != null ? fmtPx(o.price) : "—"
          }</span><span class="mute">${fmtTime(o.ts)}</span></div>`
      )
      .join("");
    const sells = (p.sell_orders || [])
      .map(
        (o) =>
          `<div class="pos-layer sell"><span>SELL</span><span class="pos-layer-usd">${_fillUsd(
            o
          )}</span><span>@ ${
            o.price != null ? fmtPx(o.price) : "—"
          }</span><span class="mute">${fmtTime(o.ts)}</span></div>`
      )
      .join("");

    const isHold = !!(p.is_hold || p.position_book === "hold");
    const freeBtns = isOpen
      ? `<div class="pos-flag-actions">
            <div class="pos-flag-row">
              <span class="pos-flag-label mute">Book</span>
              <button type="button" class="btn soft sm ${isHold ? "" : "on"}" data-book-ad="${escHtml(
                String(posId)
              )}" data-sym="${escHtml(p.symbol || "")}" data-mkt="${escHtml(
                (p.market || "spot").toString()
              )}" title="AD / panic trading — used for agent learning">AD desk</button>
              <button type="button" class="btn soft sm ${isHold ? "on" : ""}" data-book-hold="${escHtml(
                String(posId)
              )}" data-sym="${escHtml(p.symbol || "")}" data-mkt="${escHtml(
                (p.market || "spot").toString()
              )}" title="Long-term invest — excluded from AD learning">Long-term</button>
            </div>
            ${
              !isHold && (p.market || "").toLowerCase() === "spot"
                ? `<div class="pos-flag-row">
              <span class="pos-flag-label mute">Free coins</span>
              <button type="button" class="btn soft sm" data-free-on="${escHtml(
                String(posId)
              )}" data-sym="${escHtml(p.symbol || "")}" data-mkt="spot" data-mark="${
                  p.remaining_mark_usd != null ? p.remaining_mark_usd : ""
                }">Mark free</button>
              <button type="button" class="btn soft sm" data-free-off="${escHtml(
                String(posId)
              )}" data-sym="${escHtml(p.symbol || "")}" data-mkt="spot">Not free</button>
            </div>`
                : isHold
                  ? `<div class="pos-flag-note mute">Long-term invest — not used for AD bulk teach / agent cases.</div>`
                  : ""
            }
          </div>`
      : "";

    const markLine = isOpen
      ? `Avg ${entry != null ? fmtPx(entry) : "—"} · Mark ${
          mark != null ? fmtPx(mark) : "—"
        }${
          p.upnl_pct != null
            ? " · " +
              (Number(p.upnl_pct) >= 0 ? "+" : "") +
              Number(p.upnl_pct).toFixed(1) +
              "%"
            : ""
        }${
          p.position_side === "short"
            ? " · short"
            : p.position_side === "long"
              ? " · long"
              : ""
        }${p.leverage != null ? " · " + p.leverage + "x" : ""}`
      : `${entry != null ? fmtPx(entry) : "—"} → ${
          exit_ != null ? fmtPx(exit_) : "—"
        }`;

    const freeBanner = p.free_coins
      ? `<div class="pos-free-banner">Free bag · ${
          held != null ? _usd(held, false) : "—"
        } mark · scale out on the way up${
          cashBanked != null ? " · cash banked " + _usd(cashBanked, true) : ""
        }</div>`
      : p.free_coins_status === "near_free"
        ? `<div class="pos-free-banner near">Near free · principal almost back</div>`
        : "";

    return `<details class="pos-card ${isOpen ? "is-open" : "is-closed"} outcome-${
      (p.outcome || (isOpen ? "open" : "flat")).toLowerCase()
    }${p.free_coins && !isHold ? " is-free" : ""}${
      p.free_coins_status === "near_free" && !isHold ? " is-near" : ""
    }${isHold ? " is-hold" : ""}" data-pos-id="${String(posId).replace(/"/g, "")}">
      <summary class="pos-sum">
        <div class="pos-id"><span class="pos-sym">${escHtml(
          String(p.symbol || "")
        )}</span></div>
        ${posOutcomeBadge(p)}
        <div class="pos-pnl-block">
          <span class="pos-pnl-main ${heroCls}">${heroMain}</span>
          ${heroSub ? `<span class="pos-pnl-sub">${heroSub}</span>` : ""}
        </div>
        <div class="pos-flow">
          <div class="pos-flow-cell"><span class="pos-flow-k">In</span><span class="pos-flow-v">${
            bought != null ? _usd(bought, false) : "—"
          }</span></div>
          <div class="pos-flow-cell"><span class="pos-flow-k">Out</span><span class="pos-flow-v">${
            sold != null ? _usd(sold, false) : "—"
          }</span></div>
          <div class="pos-flow-cell"><span class="pos-flow-k">${flow3k}</span><span class="pos-flow-v">${flow3v}</span></div>
        </div>
        <div class="pos-meta">${when}${layers ? " · " + layers : ""}</div>
        <div class="pos-tags">${posMarketPill(p.market)}${truth}</div>
        <span class="pos-chev" aria-hidden="true">▾</span>
      </summary>
      <div class="pos-detail">
        <div class="pos-g">Capital</div>
        <div class="pos-cash">
          <div class="pos-cash-cell"><span class="pos-cash-k">In</span><span class="pos-cash-v">${
            bought != null ? _usd(bought, false) : "—"
          }</span></div>
          <div class="pos-cash-cell"><span class="pos-cash-k">Out</span><span class="pos-cash-v">${
            sold != null ? _usd(sold, false) : "—"
          }</span></div>
          <div class="pos-cash-cell"><span class="pos-cash-k">Cost left</span><span class="pos-cash-v">${
            p.remaining_cost_usd != null
              ? _usd(p.remaining_cost_usd, false)
              : isOpen
                ? "—"
                : "$0"
          }</span></div>
          <div class="pos-cash-cell"><span class="pos-cash-k">${
            isOpen ? "Bag" : "Real"
          }</span><span class="pos-cash-v">${
      isOpen
        ? held != null
          ? _usd(held, false)
          : "—"
        : real != null
          ? _usd(real, true)
          : "—"
    }</span></div>
        </div>
        <div class="pos-g">Realized</div>
        <div class="pos-price-line">${
          real != null
            ? "Real " +
              _usd(real, true) +
              (isOpen ? " (basis on sold)" : "") +
              (p.realized_pnl_pct != null
                ? " · " +
                  (Number(p.realized_pnl_pct) >= 0 ? "+" : "") +
                  Number(p.realized_pnl_pct).toFixed(1) +
                  "%"
                : "")
            : "No realized yet"
        }${
          cashBanked != null && isOpen
            ? " · cash path " + _usd(cashBanked, true)
            : ""
        }${p.principal_recovered ? " · principal back" : ""}${
          p.fee != null ? " · fee " + p.fee : ""
        }</div>
        ${
          isOpen
            ? `${!isHold && (p.market || "").toLowerCase() === "spot" ? `<div class="pos-g">Free bag</div>${freeBanner}` : ""}${!isHold && freeBanner && (p.market || "").toLowerCase() !== "spot" ? freeBanner : ""}${isHold ? `<div class="pos-g">Book</div><div class="pos-hold-banner">Long-term invest · excluded from AD learning</div>` : ""}<div class="pos-g">Flags</div>${freeBtns}`
            : freeBanner
        }
        <div class="pos-g">Mark</div>
        <div class="pos-price-line">${markLine} · ${
      isOpen ? "opened" : "cycle"
    } ${fmtTime(p.opened_at)}${
      p.closed_at ? " → " + fmtTime(p.closed_at) : ""
    }</div>
        <div class="pos-g">Layers</div>
        <div class="pos-fills-h">Buys (${p.n_buys || 0})</div>
        ${buys || "<div class='mute'>No buys yet</div>"}
        <div class="pos-fills-h">Sells (${p.n_sells || 0})</div>
        ${
          sells ||
          (isOpen
            ? "<div class='mute'>No sells yet</div>"
            : "<div class='mute'>No sells recorded</div>")
        }
        <div class="pos-g">Meta</div>
        <div class="pos-notes">Notes: ${escHtml(
          String((p.notes || "—").slice(0, 160))
        )}</div>
        ${
          isOpen && p.journal_id != null && Number(p.journal_id) > 0
            ? `<div class="row-gap mt"><button type="button" class="btn soft sm" data-close="${p.journal_id}">Close journal</button></div>`
            : ""
        }
      </div>
    </details>`;
  }

  let _posFingerprint = "";
  let _posLastInteract = 0;
  let _posWired = false;
  /** Last positions payload (for optimistic flag updates without waiting on MEXC). */
  let _posCache = [];

  function posFingerprint(positions) {
    return (positions || [])
      .map(
        (p) =>
          `${p.entity_key || p.symbol}:${p.status}:${p.realized_pnl_usd ?? ""}:${
            p.upnl_pct ?? ""
          }:${p.size_remaining ?? ""}:${p.outcome || ""}:${
            p.position_book || ""
          }:${p.is_hold ? 1 : 0}:${p.free_coins ? 1 : 0}:${
            p.free_coins_override || ""
          }`
      )
      .join("|");
  }

  async function postPositionFlag(payload) {
    return api("/api/positions/flags", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  function applyFlagLocally(entityKey, patch) {
    const key = String(entityKey || "");
    let hit = false;
    _posCache = (_posCache || []).map((p) => {
      const ek = String(p.entity_key || "");
      const sym = String(p.symbol || "").toUpperCase();
      const match =
        ek === key ||
        (key && ek && ek.startsWith(key)) ||
        (patch.symbol &&
          sym === String(patch.symbol).toUpperCase() &&
          (p.status === "open" || p.is_open));
      if (!match) return p;
      hit = true;
      const next = { ...p };
      if (patch.book != null) {
        const book = String(patch.book).toLowerCase() === "hold" ? "hold" : "ad";
        next.position_book = book;
        next.is_hold = book === "hold";
        next.ad_learning = book !== "hold";
        if (book === "hold") {
          next.free_coins = false;
          next.free_coins_status = "none";
        }
      }
      if (patch.free_coins_override === "on") {
        next.free_coins = true;
        next.free_coins_override = "on";
        next.free_coins_source = "manual";
        next.free_coins_status = "free";
      } else if (patch.free_coins_override === "off") {
        next.free_coins = false;
        next.free_coins_override = "off";
        next.free_coins_source = "manual";
        next.free_coins_status = "none";
      }
      return next;
    });
    if (hit) {
      renderPositionsList(_posCache);
      _posFingerprint = posFingerprint(_posCache);
    }
    return hit;
  }

  function renderPositionsList(positions) {
    const host = $("#posTable");
    if (!host) return;
    let opens = positions.filter((p) => p.status === "open" || p.is_open);
    const closed = positions.filter((p) => !(p.status === "open" || p.is_open));
    const isHoldP = (p) => !!(p.is_hold || p.position_book === "hold");
    const holdOpens = opens.filter(isHoldP);
    const adOpens = opens.filter((p) => !isHoldP(p));
    // AD opens: FREE first, then NEAR, then rest
    adOpens.sort((a, b) => {
      const ra = a.free_coins ? 0 : a.free_coins_status === "near_free" ? 1 : 2;
      const rb = b.free_coins ? 0 : b.free_coins_status === "near_free" ? 1 : 2;
      return ra - rb;
    });
    const freeOpens = adOpens.filter((p) => p.free_coins);
    const riskOpens = adOpens.filter((p) => !p.free_coins);

    const head = $("#posListHead");
    if (head) {
      head.textContent = `${riskOpens.length} AD · ${freeOpens.length} free · ${holdOpens.length} hold · ${closed.length} closed`;
    }
    const br = $("#posBankroll");
    if (br) {
      let adMark = 0,
        freeMark = 0,
        freeN = 0,
        holdMark = 0,
        holdN = 0,
        openReal = 0,
        cashBanked = 0;
      opens.forEach((p) => {
        const m =
          p.remaining_mark_usd != null ? Number(p.remaining_mark_usd) : 0;
        if (isHoldP(p)) {
          holdN += 1;
          holdMark += m;
          return;
        }
        if (p.free_coins) {
          freeN += 1;
          freeMark += m;
        } else {
          adMark += m;
        }
        if (p.realized_pnl_usd != null) openReal += Number(p.realized_pnl_usd);
        if (
          p.principal_recovered &&
          p.bought_usd != null &&
          p.sold_usd != null
        ) {
          cashBanked += Number(p.sold_usd) - Number(p.bought_usd);
        }
      });
      br.innerHTML = `<div class="pos-strip">
        <div class="pos-strip-cell"><span class="pos-strip-k">AD risk</span><span class="pos-strip-v">$${adMark.toFixed(
          0
        )}</span></div>
        <div class="pos-strip-cell is-free"><span class="pos-strip-k">Free bags</span><span class="pos-strip-v">${freeN} · $${freeMark.toFixed(
        0
      )}</span></div>
        <div class="pos-strip-cell is-hold"><span class="pos-strip-k">Long-term</span><span class="pos-strip-v">${holdN} · $${holdMark.toFixed(
        0
      )}</span></div>
      </div>
      <div class="pos-strip-foot mute">Partial AD real ${
        openReal >= 0 ? "+" : ""
      }$${openReal.toFixed(0)}${
        cashBanked
          ? " · free cash " +
            (cashBanked >= 0 ? "+$" : "−$") +
            Math.abs(cashBanked).toFixed(0)
          : ""
      } · hold bags excluded from AD learning</div>`;
    }
    let html = "";
    if (riskOpens.length) {
      html += `<div class="pos-band-h">AD open risk <span class="pos-band-n">${riskOpens.length}</span></div>`;
      html += riskOpens.map(posCardHtml).join("");
    } else {
      html += `<div class="pos-band-h mute">No AD open risk</div>`;
    }
    if (freeOpens.length) {
      html += `<div class="pos-band-h free">Free coins <span class="pos-band-n">${freeOpens.length}</span></div>`;
      html += freeOpens.map(posCardHtml).join("");
    }
    if (holdOpens.length) {
      html += `<div class="pos-band-h hold">Long-term hold <span class="pos-band-n">${holdOpens.length}</span></div>`;
      html += `<p class="pos-band-hint mute">Invest bags — not used for AD bulk teach / agent cases.</p>`;
      html += holdOpens.map(posCardHtml).join("");
    }
    if (closed.length) {
      html += `<div class="pos-band-h closed">Closed <span class="pos-band-n">${closed.length}</span></div>`;
      html += closed.map(posCardHtml).join("");
    }
    host.innerHTML = html;
  }

  function wirePosTableOnce() {
    const host = $("#posTable");
    if (!host || _posWired) return;
    _posWired = true;
    const bump = () => {
      _posLastInteract = Date.now();
    };
    host.addEventListener("scroll", bump, { passive: true });
    host.addEventListener("pointerdown", bump);

    async function runFlagClick(btn, payload, okMsg) {
      if (!btn || btn.disabled) return;
      const prev = btn.textContent;
      btn.disabled = true;
      btn.textContent = "…";
      try {
        await postPositionFlag(payload);
        applyFlagLocally(payload.entity_key, payload);
        toast(okMsg);
        // Background reconcile with exchange (slow) — UI already updated
        loadPositions({ force: true }).catch(() => {});
      } catch (e) {
        const msg =
          e && e.message
            ? typeof e.message === "string"
              ? e.message
              : JSON.stringify(e.message)
            : String(e);
        toast(msg);
        console.error("position flag failed", e);
      } finally {
        if (btn.isConnected) {
          btn.disabled = false;
          btn.textContent = prev;
        }
      }
    }

    // Capture phase so we always see the click even if something else stops bubble
    host.addEventListener(
      "click",
      (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const bookHold = t.closest("[data-book-hold]");
        if (bookHold && host.contains(bookHold)) {
          ev.preventDefault();
          ev.stopPropagation();
          const ek =
            bookHold.getAttribute("data-book-hold") ||
            bookHold.dataset.bookHold;
          runFlagClick(
            bookHold,
            {
              entity_key: ek,
              symbol: bookHold.getAttribute("data-sym") || bookHold.dataset.sym,
              market:
                bookHold.getAttribute("data-mkt") ||
                bookHold.dataset.mkt ||
                "spot",
              book: "hold",
            },
            "Long-term hold — out of AD learning"
          );
          return;
        }
        const bookAd = t.closest("[data-book-ad]");
        if (bookAd && host.contains(bookAd)) {
          ev.preventDefault();
          ev.stopPropagation();
          const ek =
            bookAd.getAttribute("data-book-ad") || bookAd.dataset.bookAd;
          runFlagClick(
            bookAd,
            {
              entity_key: ek,
              symbol: bookAd.getAttribute("data-sym") || bookAd.dataset.sym,
              market:
                bookAd.getAttribute("data-mkt") ||
                bookAd.dataset.mkt ||
                "spot",
              book: "ad",
            },
            "Back on AD desk book"
          );
          return;
        }
        const freeOn = t.closest("[data-free-on]");
        if (freeOn && host.contains(freeOn)) {
          ev.preventDefault();
          ev.stopPropagation();
          const ek =
            freeOn.getAttribute("data-free-on") || freeOn.dataset.freeOn;
          const markRaw =
            freeOn.getAttribute("data-mark") || freeOn.dataset.mark || "";
          runFlagClick(
            freeOn,
            {
              entity_key: ek,
              symbol: freeOn.getAttribute("data-sym") || freeOn.dataset.sym,
              market:
                freeOn.getAttribute("data-mkt") ||
                freeOn.dataset.mkt ||
                "spot",
              free_coins_override: "on",
              free_mark_usd: markRaw !== "" ? +markRaw : null,
            },
            "Marked free coins"
          );
          return;
        }
        const freeOff = t.closest("[data-free-off]");
        if (freeOff && host.contains(freeOff)) {
          ev.preventDefault();
          ev.stopPropagation();
          const ek =
            freeOff.getAttribute("data-free-off") || freeOff.dataset.freeOff;
          runFlagClick(
            freeOff,
            {
              entity_key: ek,
              symbol: freeOff.getAttribute("data-sym") || freeOff.dataset.sym,
              market:
                freeOff.getAttribute("data-mkt") ||
                freeOff.dataset.mkt ||
                "spot",
              free_coins_override: "off",
            },
            "Unmarked free coins"
          );
          return;
        }
        const b = t.closest("[data-close]");
        if (!b || !host.contains(b)) return;
        ev.preventDefault();
        ev.stopPropagation();
        (async () => {
          try {
            await api("/api/positions/close", {
              method: "POST",
              body: JSON.stringify({ trade_id: +b.dataset.close }),
            });
            toast("Position closed");
            loadPositions({ force: true });
          } catch (e) {
            toast(e.message || String(e));
          }
        })();
      },
      true
    );
  }

  state.pnlWindow = "30d";

  async function loadPnl() {
    const host = $("#pnlBody");
    if (!host) return;
    try {
      const d = await api(
        `/api/pnl?window=${encodeURIComponent(state.pnlWindow || "30d")}`
      );
      const b = d.bankroll || {};
      const r = d.realized || {};
      const free = d.free_bags || [];
      const book = d.open_book || [];
      const atRisk = Number(b.at_risk_mark_usd != null ? b.at_risk_mark_usd : 0);
      const freeMark = Number(b.free_mark_usd || 0);
      const openMark = Number(b.open_mark_usd || 0);
      const score = Number(r.pnl_usd || 0);
      host.innerHTML = `
        <div class="pnl-hero">
          <div class="pnl-hero-card primary">
            <div class="pnl-k">Score · ${escHtml(d.window || "")}</div>
            <div class="pnl-v ${score >= 0 ? "up" : "dn"}">${
              score >= 0 ? "+" : ""
            }$${score.toFixed(0)}</div>
            <div class="pnl-sub">W ${r.win_n || 0} ($${Number(
        r.win_usd || 0
      ).toFixed(0)}) · L ${r.miss_n || 0} ($${Number(r.miss_usd || 0).toFixed(
        0
      )}) · flat ${r.flat_n || 0}</div>
          </div>
          <div class="pnl-hero-card">
            <div class="pnl-k">Bankroll</div>
            <div class="pnl-v">$${openMark.toFixed(0)}</div>
            <div class="pnl-sub">At risk $${atRisk.toFixed(
              0
            )} · free $${freeMark.toFixed(0)} · open ${b.open_n || 0}</div>
          </div>
          <div class="pnl-hero-card free">
            <div class="pnl-k">Free capital</div>
            <div class="pnl-v">${b.free_bags_n || 0} · $${freeMark.toFixed(0)}</div>
            <div class="pnl-sub">Principal back · bag left as inventory</div>
          </div>
        </div>
        <h4 class="pnl-sec">Open book</h4>
        ${
          book.length
            ? `<div class="scroll pnl-book-wrap">${table(
                ["Sym", "In", "Out", "Real", "Held", "Free"],
                book
                  .map((p) => {
                    const fr = p.free_coins
                      ? `<span class="pnl-free-cell">FREE</span>`
                      : "—";
                    return `<tr class="${
                      p.free_coins ? "pnl-row-free" : ""
                    }"><td>${escHtml(p.symbol)}</td><td>${
                      p.bought_usd != null
                        ? Number(p.bought_usd).toFixed(0)
                        : "—"
                    }</td><td>${
                      p.sold_usd != null ? Number(p.sold_usd).toFixed(0) : "—"
                    }</td><td>${
                      p.realized_pnl_usd != null
                        ? Number(p.realized_pnl_usd).toFixed(0)
                        : "—"
                    }</td><td>${
                      p.remaining_mark_usd != null
                        ? Number(p.remaining_mark_usd).toFixed(0)
                        : "—"
                    }</td><td>${fr}</td></tr>`;
                  })
                  .join("")
              )}</div>`
            : rankEmpty("No open positions")
        }
        <h4 class="pnl-sec">Free bags</h4>
        ${
          free.length
            ? `<div class="pnl-bags">${free
                .map((f) => {
                  const cash =
                    f.bought_usd != null && f.sold_usd != null
                      ? Number(f.sold_usd) - Number(f.bought_usd)
                      : null;
                  return `<div class="pnl-bag">
                    <div class="pnl-bag-h"><span>${escHtml(
                      f.symbol
                    )}</span><span class="pos-free">FREE</span></div>
                    <div class="pnl-bag-row"><span>Bag</span><b>$${
                      f.remaining_mark_usd != null
                        ? Number(f.remaining_mark_usd).toFixed(0)
                        : "—"
                    }</b></div>
                    <div class="pnl-bag-row"><span>Out / In</span><b>$${
                      f.sold_usd != null ? Number(f.sold_usd).toFixed(0) : "—"
                    } / $${
                      f.bought_usd != null ? Number(f.bought_usd).toFixed(0) : "—"
                    }</b></div>
                    <div class="pnl-bag-row"><span>Cash path</span><b>${
                      cash != null
                        ? (cash >= 0 ? "+$" : "−$") + Math.abs(cash).toFixed(0)
                        : "—"
                    }</b></div>
                  </div>`;
                })
                .join("")}</div>`
            : `<div class="mute">No free bags</div>`
        }
        <h4 class="pnl-sec">By book · extremes</h4>
        <div class="pnl-grid">
          <div class="pnl-card">
            <div class="pnl-k">Spot / Futures</div>
            <div class="pnl-sub">Spot $${Number(
              (d.by_book && d.by_book.spot_realized_usd) || 0
            ).toFixed(0)}</div>
            <div class="pnl-sub">Futures $${Number(
              (d.by_book && d.by_book.futures_realized_usd) || 0
            ).toFixed(0)}</div>
          </div>
          <div class="pnl-card">
            <div class="pnl-k">Best</div>
            <div class="pnl-sub">${
              r.best
                ? escHtml(r.best.symbol) +
                  " +$" +
                  Number(r.best.realized_pnl_usd).toFixed(0)
                : "—"
            }</div>
            <div class="pnl-k mt">Worst</div>
            <div class="pnl-sub">${
              r.worst
                ? escHtml(r.worst.symbol) +
                  " $" +
                  Number(r.worst.realized_pnl_usd).toFixed(0)
                : "—"
            }</div>
          </div>
        </div>
      `;
    } catch (e) {
      host.innerHTML = rankEmpty(e.message || "PnL failed");
    }
  }

  /** @param {{ force?: boolean, soft?: boolean }} [opts] */
  async function loadPositions(opts) {
    const force = !!(opts && opts.force);
    const soft = !!(opts && opts.soft) || (!force && !!_posFingerprint);
    const host = $("#posTable");
    if (!host) return;
    wirePosTableOnce();

    // Soft: skip while user is interacting (scroll / expand)
    if (soft && Date.now() - _posLastInteract < 8000) return;
    if (soft && host.querySelector("details[open]")) return;
    if (soft && host.matches(":hover")) return;

    if (!soft && !host.querySelector(".pos-card")) {
      host.innerHTML = rankEmpty("Loading positions…");
    }
    let d;
    try {
      d = await api("/api/positions?closed=true");
    } catch (e) {
      if (!soft) {
        host.innerHTML = rankEmpty(
          "Failed to load positions — " + (e.message || e)
        );
      }
      return;
    }
    const positions = d.positions || [];
    const fp = posFingerprint(positions);
    if (soft && fp === _posFingerprint) return; // nothing changed — keep scroll

    const scrollY = host.scrollTop;
    const openIds = new Set(
      [...host.querySelectorAll("details[open]")].map((el) => el.dataset.posId)
    );

    if (!positions.length) {
      host.innerHTML = rankEmpty("No positions yet — fills sync or log manual");
      const head0 = $("#posListHead");
      if (head0) head0.textContent = "0 open · 0 closed";
      _posFingerprint = fp;
      return;
    }
    _posCache = positions;
    renderPositionsList(positions);
    _posFingerprint = fp;

    // restore expand + scroll after rebuild
    host.querySelectorAll("details[data-pos-id]").forEach((el) => {
      if (openIds.has(el.dataset.posId)) el.open = true;
    });
    host.scrollTop = scrollY;
  }

  let _activeMoverSetId = null;

  function _moverSetQuery() {
    return _activeMoverSetId != null ? `?set_id=${_activeMoverSetId}` : "";
  }

  function _fillMoverSetSelect(sets, activeId) {
    const sel = $("#moverSetSelect");
    if (!sel) return;
    const list = sets || [];
    if (!list.length) {
      sel.innerHTML = "";
      return;
    }
    let aid = activeId;
    if (aid == null || !list.some((s) => +s.id === +aid)) {
      aid = list[0].id;
    }
    _activeMoverSetId = +aid;
    sel.innerHTML = list
      .map(
        (s) =>
          `<option value="${s.id}" ${+s.id === +_activeMoverSetId ? "selected" : ""}>${escapeHtml(
            s.name
          )} · ${s.enabled ? "ON" : "off"} · ${s.threshold_percent}% · ${s.watch_count || 0} coins</option>`
      )
      .join("");
  }

  async function loadMovers(opts) {
    const soft = !!(opts && opts.soft);
    try {
      const q = _moverSetQuery();
      const d = await api(`/api/watchlist${q}`);
      _fillMoverSetSelect(d.sets || [], d.active_set_id ?? _activeMoverSetId);
      if (_activeMoverSetId != null && d.active_set_id == null) {
        const d2 = await api(`/api/watchlist?set_id=${_activeMoverSetId}`);
        return _renderMovers(d2, soft);
      }
      _renderMovers(d, soft);
    } catch (e) {
      if (!soft) toast(e.message || String(e));
    }
  }

  function _renderMovers(d, soft) {
    const s = d.settings;
    const setMeta = (d.sets || []).find((x) => +x.id === +_activeMoverSetId);
    $("#mwBadge").textContent = s
      ? `${setMeta ? setMeta.name + " · " : ""}${s.enabled ? "ON" : "OFF"} · ${s.threshold_percent}% · ${Math.round(
          (s.lookback_seconds || 0) / 60
        )}m`
      : "movers";
    if (s && !soft) {
      const f = $("#moversForm");
      if (f) {
        f.enabled.checked = !!s.enabled;
        f.threshold_percent.value = s.threshold_percent ?? "";
        f.lookback_minutes.value = s.lookback_seconds
          ? Math.round(s.lookback_seconds / 60)
          : "";
      }
    }
    const by = {};
    (d.tickers || []).forEach((t) => (by[t.symbol] = t));
    const wl = d.watchlist || [];
    const tableEl = $("#moversTable") || $("#tapeTable");
    if (s && s.enabled && !wl.length) {
      if (tableEl) {
        tableEl.innerHTML =
          rankEmpty(
            "Movers ON but watchlist is EMPTY — no dumps will fire. " +
              "Add coins or Restore from recent fires."
          ) +
          `<div class="row-gap mt"><button type="button" class="btn sm" id="btnRestoreWatch">Restore from recent fires (7d)</button></div>`;
        const br = $("#btnRestoreWatch");
        if (br && !br.dataset.bound) {
          br.dataset.bound = "1";
          br.addEventListener("click", async () => {
            try {
              const r = await api(
                `/api/watchlist/restore-from-fires?days=7${
                  _activeMoverSetId != null ? "&set_id=" + _activeMoverSetId : ""
                }`,
                { method: "POST" }
              );
              toast(
                r.added
                  ? `Restored ${r.added} symbols to watchlist`
                  : "No recent fire symbols to restore"
              );
              loadMovers({ force: true });
            } catch (e) {
              toast(e.message || String(e));
            }
          });
        }
      }
      return;
    }
    const rows = wl
      .map((w) => {
        const key = String(w.symbol).toUpperCase().replace(/_/g, "");
        const t = by[key] || by[key.replace("USDT", "") + "USDT"];
        const chg = t ? +t.changePercent : null;
        const lf = w.last_fire || null;
        const dump =
          lf && lf.drop_pct != null
            ? Number(lf.drop_pct).toFixed(1) + "%"
            : "—";
        const band = (lf && lf.velocity_band) || "—";
        const when = lf && lf.ts ? fmtTime(lf.ts) : "—";
        const mode = lf
          ? lf.mode ||
            (String(lf.source || "").includes("step")
              ? "step"
              : String(lf.source || "").includes("peak")
                ? "peak"
                : lf.source || "")
          : "";
        return `<tr class="${lf && lf.id === state.lastAlarmFlashId ? "row-flash" : ""}">
          <td>${w.market === "futures" ? "F" : "S"}</td>
          <td>${w.symbol}</td>
          <td>${t ? fmtPx(t.price) : "—"}</td>
          <td class="${chg != null && chg < 0 ? "dn" : "up"}">${fmtChg(chg)}</td>
          <td class="${dump !== "—" && Number(lf.drop_pct) < 0 ? "dn" : ""}">${dump}${
            mode ? " · " + mode : ""
          }</td>
          <td class="mute sm">${when !== "—" ? when + " · " + band : "—"}</td>
          <td><button type="button" class="btn soft sm" data-unwatch="${w.symbol}" data-m="${w.market}" data-set="${w.set_id || _activeMoverSetId || ""}">✕</button></td>
        </tr>`;
      })
      .join("");
    if (tableEl)
      tableEl.innerHTML = table(
        ["M", "Symbol", "Mark", "24h", "Last fire", "When", ""],
        rows
      );
    $$("[data-unwatch]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          let url = `/api/watchlist?symbol=${encodeURIComponent(b.dataset.unwatch)}&market=${b.dataset.m}`;
          if (b.dataset.set) url += `&set_id=${b.dataset.set}`;
          await api(url, { method: "DELETE" });
          toast("Removed from set");
          loadMovers();
        } catch (e) {
          toast(e.message);
        }
      })
    );
  }

  async function loadTargets(opts) {
    const soft = !!(opts && opts.soft);
    let d;
    try {
      d = await api("/api/alerts");
    } catch (e) {
      if (!soft) toast(e.message || String(e));
      return;
    }
    const rows = (d.alerts || [])
      .map(
        (a) => `<tr>
        <td>#${a.visual_id}</td>
        <td>${a.market === "futures" ? "F" : "S"}</td>
        <td>${a.symbol}</td>
        <td>${fmtPx(a.price)}</td>
        <td>${a.enabled ? "on" : "off"}</td>
        <td>
          <button type="button" class="btn soft sm" data-tog="${a.stable_id}" data-en="${a.enabled ? 0 : 1}">${a.enabled ? "Disable" : "Enable"}</button>
          <button type="button" class="btn soft sm" data-del="${a.stable_id}">Delete</button>
        </td>
      </tr>`
      )
      .join("");
    $("#alertsTable").innerHTML = table(
      ["#", "M", "Symbol", "Target", "State", ""],
      rows
    );
    $$("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!confirm("Delete this alert?")) return;
        try {
          await api(`/api/alerts/${b.dataset.del}`, { method: "DELETE" });
          toast("Deleted");
          loadTargets();
        } catch (e) {
          toast(e.message);
        }
      })
    );
    $$("[data-tog]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(`/api/alerts/${b.dataset.tog}`, {
            method: "PATCH",
            body: JSON.stringify({ enabled: b.dataset.en === "1" }),
          });
          loadTargets();
        } catch (e) {
          toast(e.message);
        }
      })
    );
  }

  // Learning P1 — case factory: pick → snapshot → chips + note
  state.learnSel = null;
  state.learnBehaviors = [];
  state.learnCase = null;

  function _metric(label, value, cls) {
    const v =
      value == null || value === "" || value === "undefined" ? "—" : value;
    return `<div class="learn-metric"><span>${escHtml(
      label
    )}</span><strong class="${cls || ""}">${escHtml(String(v))}</strong></div>`;
  }

  function _visualAdSlotHtml(c) {
    const vad = c && c.visual_ad;
    if (!vad || typeof vad !== "object") return "";
    const bits = [];
    if (vad.tf) bits.push(String(vad.tf));
    if (vad.high != null && vad.low != null)
      bits.push(Number(vad.high) + " → " + Number(vad.low));
    else if (vad.high != null) bits.push("H " + Number(vad.high));
    else if (vad.low != null) bits.push("L " + Number(vad.low));
    if (vad.note) bits.push(String(vad.note));
    const caption = bits.join(" · ");
    const canImg = vad.image_relpath && c.id;
    if (!caption && !canImg) return "";
    const img = canImg
      ? `<img class="learn-visual-ad-img" data-visual-ad-case="${Number(
          c.id
        )}" alt="Visual AD">`
      : "";
    return `<div class="learn-visual-ad">${img}${
      caption
        ? `<p class="learn-visual-ad-cap mute">${escHtml(caption)}</p>`
        : ""
    }</div>`;
  }

  function _visualAdAttr(val) {
    if (val == null || val === "") return "";
    return escHtml(String(val));
  }

  function _visualAdWriteHtml(c) {
    // Live preview has no frozen id — do not pretend Save AD works.
    if (!c || c.id == null || c.id === "") return "";
    const vad = c.visual_ad && typeof c.visual_ad === "object" ? c.visual_ad : {};
    return `<form class="learn-visual-ad-write" id="visualAdForm" data-visual-ad-write="${Number(
      c.id
    )}">
      <p class="learn-visual-ad-write-h">Actual AD</p>
      <div class="learn-visual-ad-write-row">
        <label>TF<input type="text" id="visualAdTf" maxlength="24" placeholder="15m" autocomplete="off" value="${_visualAdAttr(
          vad.tf
        )}"></label>
        <label>High<input type="number" id="visualAdHigh" step="any" inputmode="decimal" placeholder="high" value="${_visualAdAttr(
          vad.high
        )}"></label>
        <label>Low<input type="number" id="visualAdLow" step="any" inputmode="decimal" placeholder="low" value="${_visualAdAttr(
          vad.low
        )}"></label>
      </div>
      <label class="learn-visual-ad-write-note">Note<input type="text" id="visualAdNote" maxlength="280" placeholder="optional" autocomplete="off" value="${_visualAdAttr(
        vad.note
      )}"></label>
      <div class="learn-visual-ad-write-actions">
        <button type="submit" class="btn sm" id="visualAdSave">Save AD</button>
      </div>
    </form>`;
  }

  function _visualAdPayloadFromForm() {
    const payload = {};
    const tf = ($("#visualAdTf") && $("#visualAdTf").value.trim()) || "";
    const note = ($("#visualAdNote") && $("#visualAdNote").value.trim()) || "";
    const highRaw = $("#visualAdHigh") && $("#visualAdHigh").value;
    const lowRaw = $("#visualAdLow") && $("#visualAdLow").value;
    if (tf) payload.tf = tf.slice(0, 24);
    if (note) payload.note = note.slice(0, 280);
    if (highRaw != null && String(highRaw).trim() !== "") {
      const high = Number(highRaw);
      if (!Number.isNaN(high)) payload.high = high;
    }
    if (lowRaw != null && String(lowRaw).trim() !== "") {
      const low = Number(lowRaw);
      if (!Number.isNaN(low)) payload.low = low;
    }
    return payload;
  }

  function _applyVisualAdView(c, view) {
    const vad = view && view.visual_ad ? view.visual_ad : null;
    if (c) c.visual_ad = vad;
    if (state.learnCase) state.learnCase.visual_ad = vad;
    const sel = state.learnSel;
    const cacheKey = _learnSnapKey(sel);
    if (cacheKey && state.learnSnapCache && state.learnSnapCache[cacheKey]) {
      state.learnSnapCache[cacheKey].visual_ad = vad;
    }
    if (sel && sel.case) sel.case.visual_ad = vad;
    const slot = $("#learnVisualAdSlot");
    if (slot) {
      slot.innerHTML = _visualAdSlotHtml({
        ...(c || {}),
        ...(view || {}),
        visual_ad: vad,
        id: (view && view.id) || (c && c.id),
      });
      hydrateVisualAdImages(slot);
    }
    if (state.incidentChart) state.incidentChart.clicks = [];
    paintIncidentChart();
  }

  function bindVisualAdWrite(host, c) {
    const form = host && host.querySelector("#visualAdForm");
    const btn = host && host.querySelector("#visualAdSave");
    if (!form || !c || c.id == null || c.id === "") return;
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const payload = _visualAdPayloadFromForm();
      if (
        payload.tf == null &&
        payload.high == null &&
        payload.low == null &&
        payload.note == null
      ) {
        toast("Need TF, high, low, or a short note");
        return;
      }
      if (btn) btn.disabled = true;
      try {
        const view = await api(`/api/learning/cases/${Number(c.id)}/visual-ad`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        _applyVisualAdView(c, view);
        toast("AD saved");
      } catch (e) {
        toast(e.message);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  function hydrateVisualAdImages(host) {
    if (!host) return;
    host.querySelectorAll("img[data-visual-ad-case]").forEach((img) => {
      const id = img.getAttribute("data-visual-ad-case");
      if (!id) return;
      fetch(`/api/learning/cases/${id}/visual-ad/image`, {
        headers: headers(false),
      })
        .then((r) => (r.ok ? r.blob() : Promise.reject()))
        .then((b) => {
          img.src = URL.createObjectURL(b);
        })
        .catch(() => {
          img.remove();
        });
    });
  }

  function _incidentDefaultTf(c) {
    const vad = c && c.visual_ad && typeof c.visual_ad === "object" ? c.visual_ad : {};
    if (vad.tf) return String(vad.tf);
    if (c && c.tf_hint) return String(c.tf_hint);
    return "15m";
  }

  function _incidentChartTfs(c) {
    const known = ["1m", "5m", "15m", "1h", "4h"];
    const extra = [];
    const inc = c && c.incident;
    if (inc && Array.isArray(inc.chart_tfs)) extra.push.apply(extra, inc.chart_tfs);
    if (c && c.tf_hint) extra.push(c.tf_hint);
    if (c && c.visual_ad && c.visual_ad.tf) extra.push(c.visual_ad.tf);
    const out = [];
    known.concat(extra).forEach((t) => {
      const s = String(t || "").trim();
      if (s && out.indexOf(s) < 0) out.push(s);
    });
    return out;
  }

  function _incidentChartCanShow(c, sel) {
    const sym = (c && c.symbol) || (sel && sel.symbol);
    const ts = (c && (c.fire_ts || c.incident_ts)) || (sel && sel.ts);
    const eid = (c && c.event_id) || (sel && sel.event_id);
    const cid = c && c.id;
    return !!(sym && (ts || eid || cid));
  }

  function _incidentChartHtml(c, sel) {
    if (!_incidentChartCanShow(c, sel)) return "";
    const tf = _incidentDefaultTf(c);
    const opts = _incidentChartTfs(c)
      .map(
        (t) =>
          `<option value="${escHtml(t)}"${
            t === tf ? " selected" : ""
          }>${escHtml(t)}</option>`
      )
      .join("");
    return `<div class="learn-incident-chart" id="learnIncidentChart">
      <div class="learn-incident-chart-h">
        <span>Incident candles</span>
        <span class="mute" id="learnIncidentChartMeta"></span>
        <label class="learn-incident-chart-tf">TF
          <select id="learnIncidentTf">${opts}</select>
        </label>
      </div>
      <canvas id="learnIncidentCanvas" width="640" height="220"></canvas>
      <p class="learn-incident-chart-hint mute" id="learnIncidentChartHint">Click two prices on the dump to mark the AD zone · not live</p>
    </div>`;
  }

  function _adPxInput(n) {
    if (n == null || Number.isNaN(+n)) return "";
    const x = +n;
    if (Math.abs(x) >= 1000) return String(Math.round(x * 100) / 100);
    if (Math.abs(x) >= 1) return String(Math.round(x * 1e6) / 1e6);
    return String(Number(x.toPrecision(6)));
  }

  function _chartYRange(bars, extras) {
    let lo = Infinity;
    let hi = -Infinity;
    (bars || []).forEach((b) => {
      if (b.l != null && +b.l < lo) lo = +b.l;
      if (b.h != null && +b.h > hi) hi = +b.h;
    });
    (extras || []).forEach((p) => {
      if (p == null || Number.isNaN(+p)) return;
      lo = Math.min(lo, +p);
      hi = Math.max(hi, +p);
    });
    if (!isFinite(lo) || !isFinite(hi)) return { lo: 0, hi: 1 };
    if (hi <= lo) {
      const pad0 = Math.abs(hi) * 0.02 || 1e-8;
      return { lo: lo - pad0, hi: hi + pad0 };
    }
    const pad = (hi - lo) * 0.08;
    return { lo: lo - pad, hi: hi + pad };
  }

  function _incidentZonePrices() {
    const highEl = $("#visualAdHigh");
    const lowEl = $("#visualAdLow");
    let high = highEl && String(highEl.value).trim() !== "" ? Number(highEl.value) : null;
    let low = lowEl && String(lowEl.value).trim() !== "" ? Number(lowEl.value) : null;
    const vad = state.learnCase && state.learnCase.visual_ad;
    if ((high == null || Number.isNaN(high)) && vad && vad.high != null) high = Number(vad.high);
    if ((low == null || Number.isNaN(low)) && vad && vad.low != null) low = Number(vad.low);
    if (high != null && Number.isNaN(high)) high = null;
    if (low != null && Number.isNaN(low)) low = null;
    return { high, low };
  }

  function paintIncidentChart() {
    const cv = $("#learnIncidentCanvas");
    if (!cv) return;
    const pack = state.incidentChart || {};
    const bars = pack.bars || [];
    const host = cv.parentElement;
    const cssW = Math.max(260, (host && host.clientWidth) || 320);
    const cssH = 220;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(cssW * dpr);
    cv.height = Math.round(cssH * dpr);
    cv.style.width = cssW + "px";
    cv.style.height = cssH + "px";
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.fillStyle = "rgba(0,0,0,0.35)";
    ctx.fillRect(0, 0, cssW, cssH);
    const pad = { l: 52, r: 8, t: 8, b: 22 };
    const plotW = cssW - pad.l - pad.r;
    const plotH = cssH - pad.t - pad.b;
    const zone = _incidentZonePrices();
    const extras = [pack.fire_price, zone.high, zone.low].concat(pack.clicks || []);
    const yr = _chartYRange(bars, extras);
    const yOf = (p) => pad.t + ((yr.hi - p) / (yr.hi - yr.lo)) * plotH;
    const xOf = (i, n) => pad.l + ((i + 0.5) / Math.max(n, 1)) * plotW;

    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.font = "10px ui-monospace, IBM Plex Mono, monospace";
    ctx.textAlign = "right";
    [yr.hi, (yr.hi + yr.lo) / 2, yr.lo].forEach((p) => {
      ctx.fillText(fmtPx(p), pad.l - 4, yOf(p) + 3);
    });

    if (!bars.length) {
      ctx.fillStyle = "rgba(255,255,255,0.4)";
      ctx.textAlign = "center";
      ctx.fillText("No candles for this incident", cssW / 2, cssH / 2);
      return;
    }

    const n = bars.length;
    if (pack.fire_ts) {
      let fi = 0;
      let best = Infinity;
      bars.forEach((b, i) => {
        const d = Math.abs(b.ts - pack.fire_ts);
        if (d < best) {
          best = d;
          fi = i;
        }
      });
      const x = xOf(fi, n);
      ctx.strokeStyle = "rgba(34,211,238,0.55)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, pad.t + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (zone.high != null && zone.low != null) {
      const y1 = yOf(Math.max(zone.high, zone.low));
      const y2 = yOf(Math.min(zone.high, zone.low));
      ctx.fillStyle = "rgba(34,211,238,0.16)";
      ctx.fillRect(pad.l, y1, plotW, Math.max(2, y2 - y1));
      ctx.strokeStyle = "rgba(34,211,238,0.7)";
      ctx.beginPath();
      ctx.moveTo(pad.l, y1);
      ctx.lineTo(pad.l + plotW, y1);
      ctx.moveTo(pad.l, y2);
      ctx.lineTo(pad.l + plotW, y2);
      ctx.stroke();
    } else {
      (pack.clicks || []).forEach((p) => {
        const y = yOf(p);
        ctx.strokeStyle = "rgba(34,211,238,0.5)";
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(pad.l + plotW, y);
        ctx.stroke();
        ctx.setLineDash([]);
      });
    }

    const slot = plotW / n;
    const bodyW = Math.max(1, Math.min(8, slot * 0.6));
    bars.forEach((b, i) => {
      const x = xOf(i, n);
      const up = b.c >= b.o;
      ctx.strokeStyle = up ? "#34d399" : "#f87171";
      ctx.fillStyle = up ? "#34d399" : "#f87171";
      ctx.beginPath();
      ctx.moveTo(x, yOf(b.h));
      ctx.lineTo(x, yOf(b.l));
      ctx.stroke();
      const top = yOf(Math.max(b.o, b.c));
      const bot = yOf(Math.min(b.o, b.c));
      ctx.fillRect(x - bodyW / 2, top, bodyW, Math.max(1, bot - top));
    });

    ctx.fillStyle = "rgba(255,255,255,0.4)";
    ctx.textAlign = "center";
    [0, Math.floor((n - 1) / 2), n - 1].forEach((i) => {
      if (!bars[i]) return;
      ctx.fillText(fmtTime(bars[i].ts), xOf(i, n), cssH - 6);
    });
  }

  function _priceAtCanvasY(cv, clientY) {
    const rect = cv.getBoundingClientRect();
    const y = clientY - rect.top;
    const pack = state.incidentChart || {};
    const bars = pack.bars || [];
    const pad = { l: 52, r: 8, t: 8, b: 22 };
    const plotH = rect.height - pad.t - pad.b;
    const zone = _incidentZonePrices();
    const extras = [pack.fire_price, zone.high, zone.low].concat(pack.clicks || []);
    const yr = _chartYRange(bars, extras);
    const t = (y - pad.t) / Math.max(plotH, 1);
    return yr.hi - t * (yr.hi - yr.lo);
  }

  function _onIncidentChartClick(ev) {
    const cv = ev.currentTarget;
    const rect = cv.getBoundingClientRect();
    const y = ev.clientY - rect.top;
    if (y < 8 || y > rect.height - 22) return;
    const px = _priceAtCanvasY(cv, ev.clientY);
    if (px == null || Number.isNaN(px)) return;
    const pack = state.incidentChart || (state.incidentChart = {});
    const clicks = pack.clicks || [];
    if (clicks.length >= 2) clicks.length = 0;
    clicks.push(px);
    pack.clicks = clicks;
    const hint = $("#learnIncidentChartHint");
    if (clicks.length === 2) {
      const hi = Math.max(clicks[0], clicks[1]);
      const lo = Math.min(clicks[0], clicks[1]);
      const highEl = $("#visualAdHigh");
      const lowEl = $("#visualAdLow");
      if (highEl) highEl.value = _adPxInput(hi);
      if (lowEl) lowEl.value = _adPxInput(lo);
      if (hint) {
        hint.textContent =
          highEl && lowEl
            ? "Zone set — Save AD to store it"
            : "Zone marked on this preview — Save AD needs a frozen case";
      }
    } else if (hint) {
      hint.textContent = "Click the other side of the AD zone";
    }
    paintIncidentChart();
  }

  function _incidentCandlesQuery(c, sel, tf) {
    const q = new URLSearchParams();
    if (tf) q.set("tf", tf);
    if (c && c.id != null && c.id !== "") {
      q.set("case_id", String(c.id));
      return q;
    }
    const eid = (c && c.event_id) || (sel && sel.event_id);
    if (eid) q.set("event_id", String(eid));
    const sym = (c && c.symbol) || (sel && sel.symbol);
    const mkt = (c && c.market) || (sel && sel.market);
    const ts = (c && (c.fire_ts || c.incident_ts)) || (sel && sel.ts);
    if (sym) q.set("symbol", sym);
    if (mkt) q.set("market", mkt);
    if (ts) q.set("fire_ts", String(ts));
    return q;
  }

  async function loadIncidentCandles(c, sel, tf) {
    const cv = $("#learnIncidentCanvas");
    if (!cv) return;
    const useTf = tf || _incidentDefaultTf(c);
    const q = _incidentCandlesQuery(c, sel, useTf);
    if (!q.get("case_id") && !q.get("event_id") && !(q.get("symbol") && q.get("fire_ts"))) {
      return;
    }
    const hint = $("#learnIncidentChartHint");
    if (hint) hint.textContent = "Loading incident candles…";
    try {
      const body = await api("/api/learning/incident-candles?" + q.toString());
      state.incidentChart = state.incidentChart || {};
      state.incidentChart.bars = (body && body.bars) || [];
      state.incidentChart.tf = (body && body.tf) || useTf;
      state.incidentChart.fire_ts = body && body.fire_ts;
      state.incidentChart.fire_price = body && body.fire_price;
      state.incidentChart.symbol = body && body.symbol;
      if (!state.incidentChart.clicks) state.incidentChart.clicks = [];
      const meta = $("#learnIncidentChartMeta");
      if (meta) {
        meta.textContent = ((body && body.tf) || useTf) + " · around fire · not live";
      }
      if (hint) {
        hint.textContent = state.incidentChart.bars.length
          ? "Click two prices on the dump to mark the AD zone · not live"
          : "No candles for this incident";
      }
      paintIncidentChart();
    } catch (e) {
      if (hint) hint.textContent = e.message || "Could not load incident candles";
      state.incidentChart = state.incidentChart || {};
      state.incidentChart.bars = [];
      paintIncidentChart();
    }
  }

  function bindIncidentChart(host, c, sel) {
    const box = host && host.querySelector("#learnIncidentChart");
    if (!box) return;
    state.incidentChart = {
      bars: [],
      tf: _incidentDefaultTf(c),
      clicks: [],
      fire_ts: c && (c.fire_ts || c.incident_ts),
      fire_price: c && c.fire_price,
    };
    const tfSel = box.querySelector("#learnIncidentTf");
    const writerTf = host.querySelector("#visualAdTf");
    const syncTf = (next) => {
      const tf = (next || "").trim() || _incidentDefaultTf(c);
      if (tfSel && tfSel.value !== tf) {
        if (![...tfSel.options].some((o) => o.value === tf)) {
          const opt = document.createElement("option");
          opt.value = tf;
          opt.textContent = tf;
          tfSel.appendChild(opt);
        }
        tfSel.value = tf;
      }
      if (writerTf && writerTf.value !== tf) writerTf.value = tf;
      loadIncidentCandles(c, sel, tf);
    };
    if (tfSel) tfSel.addEventListener("change", () => syncTf(tfSel.value));
    if (writerTf) {
      writerTf.addEventListener("change", () => syncTf(writerTf.value));
    }
    const highEl = host.querySelector("#visualAdHigh");
    const lowEl = host.querySelector("#visualAdLow");
    if (highEl) highEl.addEventListener("input", () => paintIncidentChart());
    if (lowEl) lowEl.addEventListener("input", () => paintIncidentChart());
    const canvas = box.querySelector("#learnIncidentCanvas");
    if (canvas) canvas.addEventListener("click", _onIncidentChartClick);
    loadIncidentCandles(c, sel, (tfSel && tfSel.value) || _incidentDefaultTf(c));
  }

  function renderCaseSnap(snap, sel) {
    const host = $("#learnCaseSnap");
    if (!host) return;
    if (!sel) {
      host.hidden = true;
      host.innerHTML = "";
      state.learnCase = null;
      return;
    }
    const c = snap || {};
    state.learnCase = c;
    const freeze = c.freeze || (c.features_ok ? "ok" : c.ok === false ? "partial" : "partial");
    const badge =
      freeze === "ok"
        ? "FROZEN"
        : freeze === "partial"
          ? "PARTIAL"
          : "NO SNAP";
    const drop =
      c.drop_pct != null
        ? `${Number(c.drop_pct) <= 0 ? "" : "−"}${Math.abs(
            Number(c.drop_pct)
          ).toFixed(1)}%`
        : c.dd_pct != null
          ? `−${Math.abs(Number(c.dd_pct)).toFixed(1)}%`
          : null;
    const vel =
      c.vel_pct_min != null ? `${Number(c.vel_pct_min).toFixed(2)}%/m` : null;
    const adDepth =
      c.ad_depth_ratio != null
        ? `${Number(c.ad_depth_ratio).toFixed(2)}×`
        : null;
    const vol =
      c.vol_flag || c.vol_ratio != null
        ? `${c.vol_flag || "vol"}${
            c.vol_ratio != null ? " " + Number(c.vol_ratio).toFixed(1) + "×" : ""
          }`
        : null;
    const heat =
      c.heat_breadth != null ? `${c.heat_breadth} names` : null;
    const setup =
      c.setup_prior != null ? Number(c.setup_prior).toFixed(2) : null;
    let tradeLine = "";
    if (sel.type === "trade" && sel.tradeSnap) {
      const t = sel.tradeSnap;
      tradeLine = [
        t.entry_avg != null ? "entry " + t.entry_avg : "",
        t.exit_avg != null ? "exit " + t.exit_avg : "",
        t.pnl_pct != null
          ? (t.pnl_pct >= 0 ? "+" : "") + Number(t.pnl_pct).toFixed(1) + "%"
          : "",
        t.pnl_usd != null ? "$" + t.pnl_usd : "",
        t.money_truth || "",
      ]
        .filter(Boolean)
        .join(" · ");
    }
    host.hidden = false;
    host.innerHTML = `
      <div class="learn-case-h">
        <span class="learn-case-title">Case snapshot</span>
        <span class="learn-case-badge" data-freeze="${escHtml(
          freeze
        )}">${badge}${
      sel.type === "fire"
        ? " · fire"
        : sel.event_id
          ? " · trade · fire #" + sel.event_id
          : " · trade"
    }</span>
      </div>
      <div class="learn-case-grid">
        ${_metric("Symbol", (c.symbol || sel.symbol || "").toString().toUpperCase())}
        ${_metric("Market", c.market || sel.market || "—")}
        ${_metric("Drop", drop, drop ? "dn" : "")}
        ${_metric("Band", c.band || c.velocity_band || "—")}
        ${_metric("Vel", vel)}
        ${_metric("Fire px", c.fire_price != null ? c.fire_price : sel.price)}
        ${_metric("AD zone", c.ad_zone || "—")}
        ${_metric("AD depth", adDepth)}
        ${_metric("Vol", vol)}
        ${_metric("Regime", c.regime_guess || "—")}
        ${_metric("TF hint", c.tf_hint || "—")}
        ${_metric(
          "Reds",
          (c.timing_gate && c.timing_gate.red_streak != null
            ? String(c.timing_gate.red_streak)
            : "—") +
            (c.timing_gate && c.timing_gate.red_label
              ? " " + c.timing_gate.red_label
              : "")
        )}
        ${_metric(
          "Stack",
          c.factor_alignment && c.factor_alignment.yes_count != null
            ? `${c.factor_alignment.yes_count} of ${
                (c.factor_alignment.yes_count || 0) +
                (c.factor_alignment.weak_count || 0)
              } history matches`
            : "—"
        )}
        ${_metric("Heat", heat)}
        ${_metric(
          "Setup score",
          setup,
          ""
        )}
        ${_metric(
          "Time",
          c.fire_ts ? fmtTime(c.fire_ts) : sel.ts ? fmtTime(sel.ts) : "—"
        )}
      </div>
      ${
        tradeLine
          ? `<p class="learn-case-trade mono mute">${escHtml(tradeLine)}</p>`
          : ""
      }
      ${
        Array.isArray(c.ad_by_tf) && c.ad_by_tf.length
          ? `<p class="learn-case-tfs mute mono">${c.ad_by_tf
              .map((p) => {
                const bits = [p.tf, p.ad_zone || "—"];
                if (p.red_streak != null) bits.push(p.red_streak + "r");
                if (p.vol_panic_bar) bits.push("vol!");
                return bits.join(" ");
              })
              .join(" · ")}</p>`
          : ""
      }
      <div id="learnVisualAdSlot">${_visualAdSlotHtml(c)}</div>
      ${_incidentChartHtml(c, sel)}
      ${_visualAdWriteHtml(c)}
      <p class="learn-case-foot mute">${
        freeze === "ok"
          ? "Chart history on the TF you click is the truth. Chips record what you see / how you traded — they don’t invent an AD."
          : "Features incomplete — still teach with chips + note. (Setup score is chart structure only — not a recommendation.)"
      }</p>
      <div class="learn-similar mute" hidden></div>`;
    hydrateVisualAdImages(host);
    bindVisualAdWrite(host, c);
    bindIncidentChart(host, c, sel);
    try {
      host.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (_) {}
    loadSimilarCases(c, sel);
  }

  async function loadSimilarCases(c, sel) {
    const box = $("#learnCaseSnap") && $("#learnCaseSnap").querySelector(".learn-similar");
    if (!box) return;
    try {
      let q = `k=4`;
      if (c && c.id) q += `&case_id=${c.id}`;
      const sym = (c && c.symbol) || (sel && sel.symbol);
      const mkt = (c && c.market) || (sel && sel.market) || "futures";
      if (sym) q += `&symbol=${encodeURIComponent(sym)}&market=${encodeURIComponent(mkt)}`;
      if (!c?.id && !sym) return;
      const r = await api(`/api/learning/similar-cases?${q}`);
      const rows = (r && r.similar) || [];
      if (!rows.length) {
        box.hidden = true;
        return;
      }
      box.hidden = false;
      box.innerHTML =
        `<span class="learn-similar-h">Similar setups (index)</span> ` +
        rows
          .map(
            (s) =>
              `<span class="learn-similar-item">${escHtml(
                (s.symbol || "") +
                  " " +
                  (s.bucket || "") +
                  (s.tf_hint ? " · " + s.tf_hint : "")
              )}</span>`
          )
          .join(" · ");
    } catch (_) {
      box.hidden = true;
    }
  }

  function _learnSnapKey(sel) {
    if (!sel) return "";
    if (sel.event_id) return "ev:" + sel.event_id;
    if (sel.entity_key) return "ek:" + sel.entity_key;
    return "sy:" + (sel.symbol || "") + ":" + (sel.market || "");
  }

  async function loadCasePreview(sel) {
    if (!sel || !sel.symbol) {
      renderCaseSnap(null, null);
      return;
    }
    state.learnSnapCache = state.learnSnapCache || {};
    const cacheKey = _learnSnapKey(sel);
    if (sel.case && (sel.case.features_ok || sel.case.ad_zone || sel.case.band)) {
      state.learnSnapCache[cacheKey] = sel.case;
      renderCaseSnap(sel.case, sel);
      return;
    }
    if (cacheKey && state.learnSnapCache[cacheKey]) {
      renderCaseSnap(state.learnSnapCache[cacheKey], sel);
      return;
    }
    const host = $("#learnCaseSnap");
    if (host) {
      host.hidden = false;
      host.innerHTML = `<p class="mute">Loading chart history…</p>`;
    }
    try {
      let q = `symbol=${encodeURIComponent(sel.symbol)}&market=${encodeURIComponent(
        sel.market || "futures"
      )}`;
      if (sel.event_id) q += `&event_id=${sel.event_id}`;
      const snap = await api(`/api/learning/case-preview?${q}`);
      // merge fire identity if preview thin
      if (sel.drop_pct != null && snap.drop_pct == null)
        snap.drop_pct = sel.drop_pct;
      if (sel.velocity_band && !snap.velocity_band)
        snap.velocity_band = sel.velocity_band;
      if (sel.price != null && snap.fire_price == null) snap.fire_price = sel.price;
      if (cacheKey) state.learnSnapCache[cacheKey] = snap;
      renderCaseSnap(snap, sel);
    } catch (e) {
      renderCaseSnap(
        {
          freeze: "partial",
          ok: false,
          symbol: sel.symbol,
          market: sel.market,
          drop_pct: sel.drop_pct,
          velocity_band: sel.velocity_band,
          fire_price: sel.price,
        },
        sel
      );
    }
  }

  function setLearnSelection(sel) {
    state.learnSel = sel;
    state.learnBehaviors = [];
    $$(".chip", $("#learnBehaviorChips")).forEach((c) =>
      c.classList.remove("on")
    );
    $$(".chip-ad", $("#learnAdChips")).forEach((c) => c.classList.remove("on"));
    $$(".chip-bucket", $("#learnBucketChips")).forEach((c) =>
      c.classList.remove("on")
    );
    $$(".chip[data-tag]", $("#teachForm")).forEach((c) =>
      c.classList.remove("on")
    );
    const bar = $("#learnContextBar");
    const det = $("#learnContextDetail");
    const sub = $("#teachSubmit");
    const ek = $("#teachEntityKey");
    const sy = $("#teachSymbol");
    const mk = $("#teachMarket");
    const ev = $("#teachEventId");
    const ct = $("#teachContextType");
    if (!sel) {
      if (bar) {
        bar.className = "learn-context empty";
        bar.textContent =
          "Select a trade or fire → case snapshot loads → chips + note → Save.";
      }
      if (det) {
        det.hidden = true;
        det.innerHTML = "";
      }
      renderCaseSnap(null, null);
      if (sub) {
        sub.disabled = true;
        sub.textContent = "Save case + lesson";
      }
      if (ek) ek.value = "";
      if (sy) sy.value = "";
      if (mk) mk.value = "";
      if (ev) ev.value = "";
      if (ct) ct.value = "";
      $$(".learn-pick").forEach((el) => el.classList.remove("selected"));
      return;
    }
    const kind =
      sel.type === "fire"
        ? `FIRE #${sel.event_id || "?"}`
        : (sel.status || "TRADE").toString().toUpperCase();
    if (bar) {
      bar.className = "learn-context on";
      bar.innerHTML = `<strong>Teaching case</strong> · ${escHtml(
        (sel.symbol || "").toString()
      )} · ${escHtml((sel.market || "").toString())} · ${escHtml(kind)}`;
    }
    if (det) {
      det.hidden = !sel.detail;
      det.innerHTML = escHtml(sel.detail || "");
    }
    if (sub) {
      sub.disabled = false;
      sub.textContent = "Save case + lesson";
    }
    if (ek) ek.value = sel.entity_key || "";
    if (sy) sy.value = sel.symbol || "";
    if (mk) mk.value = sel.market || "";
    if (ev) ev.value = sel.event_id != null ? String(sel.event_id) : "";
    if (ct) ct.value = sel.type || "";
    loadCasePreview(sel);
  }

  async function loadMemory() {
    let bundle = {};
    try {
      bundle = await api("/api/learning");
    } catch (e) {
      toast(e.message);
      return;
    }
    const needs = bundle.needs_you || {};
    const pending = (needs.pending_questions || bundle.pending_questions || []).slice(
      0,
      2
    );
    const lessons = bundle.lessons || [];
    const stats = bundle.stats || {};
    const fires = bundle.fires || [];
    const trades = bundle.trades || [];
    updateLearningNavBadge(needs.count != null ? needs.count : pending.length);

    // 1 Pending
    const wrap = $("#ovNeedsYouLearn");
    const pb = $("#learnPendingBadge");
    if (pb) pb.textContent = String(needs.count != null ? needs.count : pending.length);
    if (wrap) wrap.hidden = pending.length === 0;
    const pendEl = $("#learnPending");
    if (pendEl) {
      pendEl.innerHTML = pending.length
        ? pending
            .map((q) => {
              const sym = q.symbol || (q.event && q.event.symbol) || "—";
              const band =
                q.velocity_band || (q.event && q.event.velocity_band) || "—";
              const drop =
                q.drop_pct != null ? Number(q.drop_pct).toFixed(1) + "%" : "—";
              const px = q.fire_price != null ? q.fire_price : "—";
              const eid = q.event_id || (q.event && q.event.id) || "";
              return `<div class="learn-card rich">
                <div class="learn-card-h">${escHtml(sym)} · ${escHtml(
                band
              )} · ${drop} @ ${escHtml(String(px))}</div>
                <div class="learn-card-meta">${fmtTime(
                  q.fire_ts || q.created_at
                )} · fire #${eid || "—"}</div>
                <div class="learn-card-t">${escHtml(
                  (q.question || "Took or skip?").slice(0, 200)
                )}</div>
                <div class="row-gap mt">
                  <button type="button" class="btn sm" data-pq="${
                    q.id
                  }" data-act="took">Took</button>
                  <button type="button" class="btn soft sm" data-pq="${
                    q.id
                  }" data-act="skip">Skip</button>
                  <button type="button" class="btn soft sm" data-pq="${
                    q.id
                  }" data-act="partial">Partial</button>
                  <button type="button" class="btn soft sm" data-pq="${
                    q.id
                  }" data-act="late">Late</button>
                  <button type="button" class="btn soft sm" data-teach-fire="${
                    eid
                  }" data-sym="${escHtml(sym)}" data-mkt="${escHtml(
                (q.market || (q.event && q.event.market) || "futures").toString()
              )}">Teach about this fire</button>
                  <button type="button" class="btn soft sm" data-pq-dismiss="${
                    q.id
                  }">Dismiss</button>
                </div>
              </div>`;
            })
            .join("")
        : "";
      $$("[data-pq]", pendEl).forEach((b) =>
        b.addEventListener("click", async () => {
          try {
            await api("/api/learning/answer", {
              method: "POST",
              body: JSON.stringify({
                question_id: +b.dataset.pq,
                action: b.dataset.act,
                answer_text: "desk",
              }),
            });
            toast("Engagement saved");
            loadMemory();
          } catch (err) {
            toast(err.message);
          }
        })
      );
      $$("[data-pq-dismiss]", pendEl).forEach((b) =>
        b.addEventListener("click", async () => {
          try {
            await api("/api/learning/answer", {
              method: "POST",
              body: JSON.stringify({
                question_id: +b.getAttribute("data-pq-dismiss"),
                dismiss: true,
              }),
            });
            loadMemory();
          } catch (err) {
            toast(err.message);
          }
        })
      );
      $$("[data-teach-fire]", pendEl).forEach((b) =>
        b.addEventListener("click", () => {
          setLearnSelection({
            type: "fire",
            symbol: b.dataset.sym,
            market: b.dataset.mkt || "futures",
            entity_key: "",
            event_id: b.dataset.teachFire ? +b.dataset.teachFire : null,
            label: `FIRE ${b.dataset.sym} (#${b.dataset.teachFire || "?"})`,
            detail: "Pending fire — teach process / take-skip reasoning",
          });
          toast("Selected fire — write the lesson on the right");
        })
      );
    }

    // 2 Trade list
    const stEl = $("#learnStats");
    const stB = $("#learnStatsBadge");
    if (stEl) {
      stEl.textContent = `fires ${stats.events || 0} · took ${
        stats.took || 0
      } · skip ${stats.skip || 0} · lessons ${lessons.length} · cases ${
        stats.cases != null ? stats.cases : (bundle.cases || []).length
      }`;
    }
    if (stB) stB.textContent = `${(trades || []).length} trades`;

    const tradeList = $("#learnTradeList");
    if (tradeList) {
      // Opens first, then closed by recency (API already newest-first overall)
      const list = (trades || []).slice().sort((a, b) => {
        const ao = a.status === "open" ? 0 : 1;
        const bo = b.status === "open" ? 0 : 1;
        if (ao !== bo) return ao - bo;
        const ta = Number(a.closed_at || a.opened_at || 0);
        const tb = Number(b.closed_at || b.opened_at || 0);
        return tb - ta;
      });
      tradeList.innerHTML = list.length
        ? list
            .map((t, i) => {
              const pnl =
                t.pnl_usd != null
                  ? `<span class="${Number(t.pnl_usd) >= 0 ? "up" : "dn"}">${
                      Number(t.pnl_usd) >= 0 ? "+$" : "−$"
                    }${Math.abs(Number(t.pnl_usd)).toFixed(0)}</span>`
                  : t.pnl_pct != null
                    ? `<span class="${Number(t.pnl_pct) >= 0 ? "up" : "dn"}">${
                        Number(t.pnl_pct) >= 0 ? "+" : ""
                      }${Number(t.pnl_pct).toFixed(1)}%</span>`
                    : t.status || "—";
              const ek = t.entity_key || t.id || `t${i}`;
              const layers = `B${t.n_buys || 0}/S${t.n_sells || 0}`;
              const mt = (t.money_truth || "").toString();
              const mtShort =
                mt === "exchange"
                  ? "EXCH"
                  : mt === "fill_cycle"
                    ? "FILLS"
                    : mt === "fill_recon_unverified"
                      ? "FILL?"
                      : mt.slice(0, 6) || "—";
              const free = t.free_coins
                ? ` · <span class="pos-free">FREE</span>`
                : "";
              const sel =
                state.learnSel &&
                state.learnSel.entity_key === String(ek)
                  ? " selected"
                  : "";
              const inOut =
                t.bought_usd != null || t.sold_usd != null
                  ? ` · in $${
                      t.bought_usd != null
                        ? Number(t.bought_usd).toFixed(0)
                        : "—"
                    } / out $${
                      t.sold_usd != null ? Number(t.sold_usd).toFixed(0) : "—"
                    }`
                  : "";
              return `<div class="learn-card rich learn-pick${sel}" data-pick-trade="${escHtml(
                String(ek)
              )}" data-i="${i}">
                <div class="learn-card-h">${escHtml(t.symbol)} · ${escHtml(
                (t.market || "?").toString().slice(0, 1).toUpperCase()
              )} · ${pnl}${free}</div>
                <div class="learn-card-meta">${escHtml(
                  (t.status || "").toString().toUpperCase()
                )} · ${layers} · ${escHtml(mtShort)}${inOut} · ${fmtTime(
                t.closed_at || t.opened_at
              )}</div>
                <div class="row-gap mt">
                  <button type="button" class="btn sm" data-pick-trade-btn="${escHtml(
                    String(ek)
                  )}" data-i="${i}">Teach about this trade</button>
                </div>
              </div>`;
            })
            .join("")
        : rankEmpty(
            "No trades yet — open/closed from Positions (fill cycles + exchange)"
          );
      const pickTrade = (i) => {
        const t = list[i];
        if (!t) return;
        const pnl = t.pnl_pct;
        const detail = [
          t.status,
          t.bought_usd != null ? "in $" + Number(t.bought_usd).toFixed(0) : "",
          t.sold_usd != null ? "out $" + Number(t.sold_usd).toFixed(0) : "",
          t.entry_avg != null ? "entry " + t.entry_avg : "",
          t.exit_avg != null ? "exit " + t.exit_avg : "",
          t.pnl_usd != null ? "PnL $" + t.pnl_usd : "",
          `layers B${t.n_buys || 0}/S${t.n_sells || 0}`,
          t.money_truth || "",
          t.free_coins ? "FREE bag" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        setLearnSelection({
          type: "trade",
          symbol: t.symbol,
          market: t.market || "futures",
          entity_key: String(t.entity_key || t.id || ""),
          event_id: t.primary_event_id || null,
          status: t.status,
          label: `${t.symbol} ${(t.market || "").toString()} · ${
            t.status
          } · ${
            t.pnl_usd != null
              ? (Number(t.pnl_usd) >= 0 ? "+$" : "−$") +
                Math.abs(Number(t.pnl_usd)).toFixed(0)
              : "—"
          }`,
          detail,
          tradeSnap: t,
        });
        $$(".learn-pick", tradeList).forEach((el) =>
          el.classList.toggle("selected", el.dataset.i === String(i))
        );
      };
      $$("[data-pick-trade-btn]", tradeList).forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          pickTrade(+b.dataset.i);
        })
      );
      $$("[data-pick-trade]", tradeList).forEach((el) =>
        el.addEventListener("click", () => pickTrade(+el.dataset.i))
      );
    }

    const fireList = $("#learnFireList");
    if (fireList) {
      fireList.innerHTML = (fires || []).length
        ? fires
            .slice(0, 20)
            .map((e, i) => {
              return `<div class="learn-card learn-pick" data-pick-fire="${e.id}" data-i="${i}">
                <div class="learn-card-h">#${e.id} ${escHtml(
                e.symbol
              )} · ${escHtml(e.velocity_band || "—")} · ${
                e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : "—"
              }</div>
                <div class="learn-card-meta">${fmtTime(e.ts)} · ${escHtml(
                e.market || ""
              )} · action ${escHtml(e.last_action || "—")}</div>
                <div class="row-gap mt">
                  <button type="button" class="btn sm" data-pick-fire-btn="${
                    e.id
                  }" data-i="${i}">Teach about this fire</button>
                </div>
              </div>`;
            })
            .join("")
        : rankEmpty("No recent fires");
      const fl = fires || [];
      const pickFire = (i) => {
        const e = fl[i];
        if (!e) return;
        setLearnSelection({
          type: "fire",
          symbol: e.symbol,
          market: e.market || "futures",
          entity_key: "",
          event_id: e.id,
          price: e.price,
          drop_pct: e.drop_pct,
          velocity_band: e.velocity_band,
          ts: e.ts,
          case: e.case || null,
          label: `FIRE ${e.symbol} #${e.id} · ${e.velocity_band || ""} · ${
            e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : ""
          }`,
          detail: `Fire @ ${e.price ?? "—"} · ${fmtTime(e.ts)} · action ${
            e.last_action || "unlabeled"
          }${e.has_case ? " · case frozen" : ""}`,
        });
      };
      $$("[data-pick-fire-btn]", fireList).forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          pickFire(+b.dataset.i);
        })
      );
    }

    // 4 Lessons with about context + delete
    const SETUP_TF = [
      "tf:1m",
      "tf:5m",
      "tf:15m",
      "tf:1h",
      "tf:4h",
      "tf:8h",
      "tf:12h",
      "tf:1d",
      "tf:1w",
    ];
    const SETUP_REGIME = ["regime:familiar", "regime:new_high", "regime:new_low"];
    const SETUP_REDS = [
      "reds:1",
      "reds:2",
      "reds:3",
      "reds:4",
      "reds:5",
      "reds:6",
    ];
    const SETUP_VOL = ["vol:climax", "vol:dry"];
    const SETUP_ALL = [
      ...SETUP_TF,
      ...SETUP_REGIME,
      ...SETUP_REDS,
      ...SETUP_VOL,
    ];
    const stackLabel = (t) => {
      if (t.startsWith("tf:")) return t.slice(3);
      if (t.startsWith("regime:")) return t.slice(7).replace("_", " ");
      if (t.startsWith("reds:")) {
        const n = t.slice(5);
        return n === "6" ? "6+ reds" : n + (n === "1" ? "st" : n === "2" ? "nd" : n === "3" ? "rd" : "th");
      }
      if (t.startsWith("vol:")) return t.slice(4);
      return t;
    };
    const PROCESS_CHIPS = [
      "plan_ok",
      "greed",
      "fomo",
      "hesitant",
      "pride",
      "rule_break",
      "process_skip",
      "false_panic",
      "free_coins",
      "free_tp_ok",
      "free_tp_greed",
    ];
    const AD_CHIPS = ["ad_met", "ad_missed"];
    const BUCKET_CHIPS = ["ad_take", "ad_press", "ad_wait", "ad_skip"];

    const lesEl = $("#learnLessons");
    if (lesEl) {
      lesEl.innerHTML = lessons.length
        ? lessons
            .map((l) => {
              const tags = (() => {
                if (Array.isArray(l.tags)) return l.tags;
                try {
                  return JSON.parse(l.tags_json || "[]");
                } catch (_) {
                  return [];
                }
              })();
              const symTag = (tags || []).find(
                (x) => typeof x === "string" && x.startsWith("sym:")
              );
              const bucketTag = (tags || []).find(
                (x) => typeof x === "string" && x.startsWith("bucket:")
              );
              const about = l.symbol_norm
                ? l.symbol_norm
                : symTag
                  ? symTag.slice(4)
                  : (l.text || "").startsWith("[")
                    ? "linked"
                    : "general";
              const chipTags = (tags || []).filter(
                (x) =>
                  typeof x === "string" &&
                  !x.includes(":") &&
                  x.length < 24
              );
              const stackTags = (tags || []).filter(
                (x) =>
                  typeof x === "string" &&
                  SETUP_ALL.includes(x.toLowerCase())
              );
              const bucket =
                l.bucket ||
                (bucketTag ? bucketTag.slice(7) : "") ||
                "";
              const when =
                l.incident_iso ||
                (l.incident_ts
                  ? fmtTime(l.incident_ts)
                  : l.created_at
                    ? fmtTime(l.created_at)
                    : "");
              const px =
                l.incident_price != null && !Number.isNaN(+l.incident_price)
                  ? fmtPx(l.incident_price)
                  : "";
              const metaBits = [
                when ? `incident ${when}` : "",
                px ? `@ ${px}` : "",
                bucket ? bucket : "",
                l.event_id ? `ev ${l.event_id}` : "",
              ].filter(Boolean);
              const metaHtml = metaBits.length
                ? `<div class="learn-lesson-incident mute">${escHtml(
                    metaBits.join(" · ")
                  )}</div>`
                : "";
              const tagHtml =
                chipTags.length || bucket || stackTags.length
                  ? `<div class="learn-lesson-tags">${chipTags
                      .map((t) => {
                        const ad = t === "ad_met" || t === "ad_missed";
                        return `<span class="learn-tag ${
                          ad ? "ad" : "beh"
                        }">${escHtml(t)}</span>`;
                      })
                      .join("")}${stackTags
                      .map(
                        (t) =>
                          `<span class="learn-tag stack">${escHtml(
                            stackLabel(t.toLowerCase())
                          )}</span>`
                      )
                      .join("")}${
                      bucket
                        ? `<span class="learn-tag bucket">${escHtml(
                            bucket
                          )}</span>`
                        : ""
                    }</div>`
                  : "";
              const fullText = l.text || "";
              const activeBucket = bucket || "";
              const chipBtns = [
                ...PROCESS_CHIPS,
                ...AD_CHIPS,
                ...BUCKET_CHIPS,
              ]
                .map((c) => {
                  const on =
                    chipTags.includes(c) || c === activeBucket ? " on" : "";
                  const ad = AD_CHIPS.includes(c) ? " chip-ad" : "";
                  const bk = BUCKET_CHIPS.includes(c) ? " chip-bucket" : "";
                  return `<button type="button" class="chip sm${ad}${bk}${on}" data-edit-chip="${c}" data-lid="${
                    l.id
                  }">${c}</button>`;
                })
                .join("");
              const setupBtns = (codes, kind) =>
                codes
                  .map((c) => {
                    const on = stackTags.includes(c) ? " on" : "";
                    return `<button type="button" class="chip sm chip-setup chip-${kind}${on}" data-edit-chip="${c}" data-setup="${kind}" data-lid="${
                      l.id
                    }">${escHtml(stackLabel(c))}</button>`;
                  })
                  .join("");
              return `<div class="learn-lesson" data-lesson-id="${l.id}" data-incident-ts="${
                l.incident_ts != null ? l.incident_ts : ""
              }" data-base="${escHtml(l.base || "")}">
                <div class="learn-lesson-view">
                  <div class="learn-lesson-main">
                    <span class="learn-about">${escHtml(about)}</span>
                    ${metaHtml}
                    <span class="learn-lesson-text">${escHtml(fullText)}</span>
                    ${tagHtml}
                  </div>
                  <div class="learn-lesson-actions">
                    <button type="button" class="btn soft sm" data-edit-lesson="${
                      l.id
                    }" title="Edit this lesson">Edit</button>
                    <button type="button" class="btn soft sm learn-del" data-del-lesson="${
                      l.id
                    }" title="Remove this lesson">Delete</button>
                  </div>
                </div>
                <div class="learn-lesson-edit" hidden data-edit-panel="${l.id}">
                  <p class="learn-panel-hint mute">Edit text, process chips, and the chart stack (TF / reds / vol). History stays on the candles.</p>
                  <textarea class="learn-edit-text" data-edit-text="${
                    l.id
                  }" rows="4">${escHtml(fullText)}</textarea>
                  <div class="learn-chips learn-edit-chips" data-edit-chips="${
                    l.id
                  }">${chipBtns}
                    <span class="learn-chip-hint mute" style="width:100%">Stack · TF</span>
                    ${setupBtns(SETUP_TF, "tf")}
                    <span class="learn-chip-hint mute" style="width:100%">Regime</span>
                    ${setupBtns(SETUP_REGIME, "regime")}
                    <span class="learn-chip-hint mute" style="width:100%">Reds</span>
                    ${setupBtns(SETUP_REDS, "red")}
                    <span class="learn-chip-hint mute" style="width:100%">Vol</span>
                    ${setupBtns(SETUP_VOL, "vol")}
                  </div>
                  <div class="row-gap">
                    <button type="button" class="btn sm" data-save-lesson="${
                      l.id
                    }">Save changes</button>
                    <button type="button" class="btn soft sm" data-cancel-edit="${
                      l.id
                    }">Cancel</button>
                  </div>
                </div>
              </div>`;
            })
            .join("")
        : rankEmpty(
            "No cases yet. Pick a trade or fire → snapshot → chips + note → Save."
          );

      const openEdit = (id) => {
        const row = lesEl.querySelector(`[data-lesson-id="${id}"]`);
        if (!row) return;
        const view = row.querySelector(".learn-lesson-view");
        const edit = row.querySelector(`[data-edit-panel="${id}"]`);
        if (view) view.hidden = true;
        if (edit) edit.hidden = false;
      };
      const closeEdit = (id) => {
        const row = lesEl.querySelector(`[data-lesson-id="${id}"]`);
        if (!row) return;
        const view = row.querySelector(".learn-lesson-view");
        const edit = row.querySelector(`[data-edit-panel="${id}"]`);
        if (view) view.hidden = false;
        if (edit) edit.hidden = true;
      };

      $$("[data-edit-lesson]", lesEl).forEach((b) =>
        b.addEventListener("click", () => openEdit(+b.dataset.editLesson))
      );
      $$("[data-cancel-edit]", lesEl).forEach((b) =>
        b.addEventListener("click", () => {
          closeEdit(+b.dataset.cancelEdit);
          loadMemory();
        })
      );
      $$("[data-edit-chip]", lesEl).forEach((b) =>
        b.addEventListener("click", () => {
          const code = b.dataset.editChip;
          const panel = b.closest("[data-edit-chips]");
          if (code === "ad_met" || code === "ad_missed") {
            $$(".chip-ad", panel).forEach((c) => {
              if (c !== b) c.classList.remove("on");
            });
          }
          if (BUCKET_CHIPS.includes(code)) {
            $$(".chip-bucket", panel).forEach((c) => {
              if (c !== b) c.classList.remove("on");
            });
          }
          const setupKind = b.dataset.setup;
          if (setupKind) {
            $$(`[data-setup="${setupKind}"]`, panel).forEach((c) => {
              if (c !== b) c.classList.remove("on");
            });
          }
          b.classList.toggle("on");
        })
      );
      $$("[data-save-lesson]", lesEl).forEach((b) =>
        b.addEventListener("click", async () => {
          const id = +b.dataset.saveLesson;
          const ta = lesEl.querySelector(`[data-edit-text="${id}"]`);
          const chipRoot = lesEl.querySelector(`[data-edit-chips="${id}"]`);
          const text = (ta && ta.value.trim()) || "";
          if (!text) {
            toast("Lesson text cannot be empty");
            return;
          }
          const behaviors = $$(".chip.on", chipRoot).map(
            (c) => c.dataset.editChip
          );
          try {
            b.disabled = true;
            await api(`/api/learning/lessons/${id}`, {
              method: "PATCH",
              body: JSON.stringify({ text, behaviors }),
            });
            toast("Lesson updated");
            loadMemory();
          } catch (err) {
            toast(err.message);
            b.disabled = false;
          }
        })
      );
      $$("[data-del-lesson]", lesEl).forEach((b) =>
        b.addEventListener("click", async () => {
          const id = +b.dataset.delLesson;
          if (!id) return;
          if (
            !confirm(
              "Delete this lesson? The agent will no longer use it in memory."
            )
          )
            return;
          try {
            await api(`/api/learning/lessons/${id}`, { method: "DELETE" });
            toast("Lesson deleted");
            loadMemory();
          } catch (err) {
            toast(err.message);
          }
        })
      );
    }

    // restore selection highlight after re-render
    if (state.learnSel && state.learnSel.entity_key) {
      setLearnSelection(state.learnSel);
    }
  }

  function wireLearningForms() {
    // tabs
    $$(".learn-tab").forEach((tab) => {
      if (tab.dataset.bound) return;
      tab.dataset.bound = "1";
      tab.addEventListener("click", () => {
        $$(".learn-tab").forEach((t) => t.classList.remove("on"));
        tab.classList.add("on");
        const which = tab.dataset.learnTab;
        const tl = $("#learnTradeList");
        const fl = $("#learnFireList");
        if (tl) tl.hidden = which !== "trades";
        if (fl) fl.hidden = which !== "fires";
      });
    });

    // process + AD + bucket chips
    const BUCKET_SET = ["ad_take", "ad_press", "ad_wait", "ad_skip"];
    const chipRoots = [
      $("#learnBehaviorChips"),
      $("#learnAdChips"),
      $("#learnBucketChips"),
    ].filter(Boolean);
    // Click-first setup tags (TF / regime / reds / vol) — exclusive per row
    [
      ["#learnTfChips", ".chip-tf"],
      ["#learnRegimeChips", ".chip-regime"],
      ["#learnRedChips", ".chip-red"],
      ["#learnVolChips", ".chip-vol"],
    ].forEach(([rid, sel]) => {
      const root = $(rid);
      if (!root || root.dataset.bound) return;
      root.dataset.bound = "1";
      root.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-tag]");
        if (!b) return;
        const on = !b.classList.contains("on");
        $$(sel, root).forEach((c) => c.classList.remove("on"));
        if (on) b.classList.add("on");
      });
    });

    chipRoots.forEach((chips) => {
      if (chips.dataset.bound) return;
      chips.dataset.bound = "1";
      chips.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-beh]");
        if (!b) return;
        const code = b.dataset.beh;
        // AD met/missed mutually exclusive
        if (code === "ad_met" || code === "ad_missed") {
          $$(".chip-ad", $("#learnAdChips")).forEach((c) => {
            if (c !== b) c.classList.remove("on");
          });
          state.learnBehaviors = state.learnBehaviors.filter(
            (x) => x !== "ad_met" && x !== "ad_missed"
          );
        }
        // One case bucket only
        if (BUCKET_SET.includes(code)) {
          $$(".chip-bucket", $("#learnBucketChips")).forEach((c) => {
            if (c !== b) c.classList.remove("on");
          });
          state.learnBehaviors = state.learnBehaviors.filter(
            (x) => !BUCKET_SET.includes(x)
          );
        }
        b.classList.toggle("on");
        if (b.classList.contains("on")) {
          if (!state.learnBehaviors.includes(code))
            state.learnBehaviors.push(code);
        } else {
          state.learnBehaviors = state.learnBehaviors.filter((x) => x !== code);
        }
      });
    });

    const clear = $("#teachClear");
    if (clear && !clear.dataset.bound) {
      clear.dataset.bound = "1";
      clear.addEventListener("click", () => setLearnSelection(null));
    }

    const tf = $("#teachForm");
    if (tf && !tf.dataset.bound) {
      tf.dataset.bound = "1";
      tf.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const text = ($("#teachText") && $("#teachText").value.trim()) || "";
        if (!text) return;
        const sel = state.learnSel;
        if (!sel || !sel.symbol) {
          toast(
            "Select a trade or fire first — cases must attach to something"
          );
          return;
        }
        const btn = $("#teachSubmit");
        const prevLabel = btn ? btn.textContent : "";
        if (btn) {
          btn.disabled = true;
          btn.textContent = "Saving…";
        }
        try {
          await api("/api/learning/teach", {
            method: "POST",
            body: JSON.stringify({
              text,
              symbol: sel.symbol,
              market: sel.market,
              entity_key: sel.entity_key || null,
              event_id: sel.event_id || null,
              context_type: sel.type || null,
              behaviors: (state.learnBehaviors || []).concat(
                $$(".chip.on[data-tag]", $("#teachForm")).map((c) => c.dataset.tag)
              ),
            }),
          });
          toast(`Case saved on ${sel.symbol}`);
          if ($("#teachText")) $("#teachText").value = "";
          state.learnBehaviors = [];
          $$(".chip", $("#learnBehaviorChips")).forEach((c) =>
            c.classList.remove("on")
          );
          $$(".chip-ad", $("#learnAdChips")).forEach((c) =>
            c.classList.remove("on")
          );
          $$(".chip-bucket", $("#learnBucketChips")).forEach((c) =>
            c.classList.remove("on")
          );
          $$(".chip.on[data-tag]", $("#teachForm")).forEach((c) =>
            c.classList.remove("on")
          );
          setLearnSelection(null);
          loadMemory();
        } catch (e) {
          toast(e.message);
          if (btn) {
            btn.disabled = false;
            btn.textContent = prevLabel || "Save case + lesson";
          }
        }
      });
    }
    const wl = $("#btnWhatLearned");
    if (wl && !wl.dataset.bound) {
      wl.dataset.bound = "1";
      wl.addEventListener("click", () => loadMemory());
    }
    const af = $("#agentRecallForm");
    if (af && !af.dataset.bound) {
      af.dataset.bound = "1";
      af.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const fd = new FormData(af);
        const question = (
          fd.get("question") || "What have you learned so far?"
        ).toString();
        try {
          const r = await api("/api/learning/ask", {
            method: "POST",
            body: JSON.stringify({ question }),
          });
          const pre = $("#agentRecallReply");
          if (pre) pre.textContent = r.reply || "";
        } catch (e) {
          toast(e.message);
        }
      });
    }
  }

  async function loadIntel() {
    const [news, inv] = await Promise.all([
      api("/api/news?limit=60"),
      api("/api/investigations"),
    ]);
    // Fatal news — always show full ticker list (not "and 3 other…")
    const newsHost = $("#newsTable");
    if (newsHost) {
      const items = news.news || [];
      if (!items.length) {
        newsHost.innerHTML = rankEmpty("No fatal news stored yet.");
      } else {
        newsHost.innerHTML = items
          .map((n) => {
            const bases =
              n.bases_text ||
              (Array.isArray(n.bases) ? n.bases.join(", ") : "") ||
              n.symbol ||
              "—";
            const nTick =
              (Array.isArray(n.bases) && n.bases.length) ||
              (typeof n.symbol === "string" && n.symbol.includes(",")
                ? n.symbol.split(",").length
                : n.symbol
                  ? 1
                  : 0);
            const title = (n.title || "").split(" · full:")[0];
            const url = n.url
              ? `<a class="intel-link" href="${escHtml(
                  n.url
                )}" target="_blank" rel="noopener">open</a>`
              : "";
            return `<article class="intel-delist-card">
              <div class="intel-delist-h">
                <span class="intel-cex">${escHtml(
                  (n.class || "NEWS").toString()
                )}</span>
                <span class="badge quiet">${escHtml(
                  n.source || ""
                )} · ${nTick || "?"} ticker${nTick === 1 ? "" : "s"}</span>
                <span class="mute mono">${fmtTime(n.ts)}</span>
                ${url}
              </div>
              <div class="intel-delist-title">${escHtml(title)}</div>
              <div class="intel-delist-bases" title="${escHtml(bases)}">
                <span class="mute">Tickers</span>
                <strong>${escHtml(bases)}</strong>
              </div>
            </article>`;
          })
          .join("");
      }
    }

    // Prefer grouped announcements so every ticker on a notice is visible
    let anns = news.delist_announcements || [];
    if (!anns.length && (news.delist_cache || []).length) {
      // Client-side group fallback
      const map = {};
      const order = [];
      (news.delist_cache || []).forEach((d) => {
        const key = `${d.exchange || ""}|${d.title || ""}`;
        if (!map[key]) {
          map[key] = {
            exchange: d.exchange,
            title: d.title,
            kind: d.kind,
            ts: d.ts,
            url: d.url,
            bases: [],
          };
          order.push(key);
        }
        const b = (d.base || "").toString().toUpperCase();
        if (b && !map[key].bases.includes(b)) map[key].bases.push(b);
        if (d.ts && (!map[key].ts || d.ts > map[key].ts)) map[key].ts = d.ts;
      });
      anns = order.map((k) => {
        const g = map[k];
        g.bases.sort();
        g.bases_text = g.bases.join(", ") || "—";
        g.n_bases = g.bases.length;
        return g;
      });
    }
    const badge = $("#delistBadge");
    if (badge) {
      badge.textContent = `${anns.length} notices`;
    }
    const delistHost = $("#delistTable");
    if (delistHost) {
      if (!anns.length) {
        delistHost.innerHTML = rankEmpty(
          "No delist notices in cache yet — radar polls CEX announcements."
        );
      } else {
        delistHost.innerHTML = anns
          .map((a) => {
            const bases = a.bases_text || (a.bases || []).join(", ") || "—";
            const n = a.n_bases != null ? a.n_bases : (a.bases || []).length;
            const title = a.title || "";
            const url = a.url
              ? `<a class="intel-link" href="${escHtml(
                  a.url
                )}" target="_blank" rel="noopener">open</a>`
              : "";
            return `<article class="intel-delist-card">
              <div class="intel-delist-h">
                <span class="intel-cex">${escHtml(
                  (a.exchange || "").toString().toUpperCase()
                )}</span>
                <span class="badge quiet">${escHtml(
                  a.kind || "delist"
                )} · ${n} ticker${n === 1 ? "" : "s"}</span>
                <span class="mute mono">${fmtTime(a.ts)}</span>
                ${url}
              </div>
              <div class="intel-delist-title">${escHtml(title)}</div>
              <div class="intel-delist-bases" title="${escHtml(bases)}">
                <span class="mute">Tickers</span>
                <strong>${escHtml(bases)}</strong>
              </div>
            </article>`;
          })
          .join("");
      }
    }

    $("#sourcesTable").innerHTML = table(
      ["Source", "Kind", "W", "Hits", "Conf", "False"],
      (inv.sources || [])
        .map(
          (s) =>
            `<tr><td>${escHtml(s.source)}</td><td>${escHtml(
              s.kind
            )}</td><td>${Number(s.weight).toFixed(2)}</td><td>${
              s.hits
            }</td><td>${s.confirmed_moves}</td><td>${s.false_alarms}</td></tr>`
        )
        .join("")
    );
  }

  async function loadRoadmap() {
    const d = await api("/api/roadmap");
    $("#visionText").textContent = d.vision || "";
    const card = (x) =>
      `<div class="road-card"><div class="st ${x.status}">${x.status}</div><div class="tt">${x.title}</div></div>`;
    $("#roadNow").innerHTML = (d.now || []).map(card).join("");
    $("#roadNext").innerHTML = (d.next || []).map(card).join("");
    $("#roadPrinciples").innerHTML = (d.principles || [])
      .map((p) => `<li>${p}</li>`)
      .join("");
  }

  async function loadPlaybook() {
    const d = await api("/api/strategy");
    $("#coreSentence").textContent = d.core || "";
    $("#workflowLine").textContent = "Workflow: " + (d.workflow || "");
    $("#preferList").innerHTML = (d.prefer || []).map((x) => `<li>${x}</li>`).join("");
    $("#avoidList").innerHTML = (d.avoid || []).map((x) => `<li>${x}</li>`).join("");
    $("#moduleGrid").innerHTML = Object.entries(d.modules || {})
      .map(
        ([k, v]) =>
          `<div class="mod"><div class="t">${k}</div><div class="d">${v}</div></div>`
      )
      .join("");
  }

  function agentMsg(cls, text) {
    const log = $("#agentLog");
    const div = document.createElement("div");
    div.className = "msg " + cls;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function applyHistory(hist) {
    if (Array.isArray(hist)) {
      state.agentHistory = hist.slice(-24);
      try {
        localStorage.setItem(HIST_KEY, JSON.stringify(state.agentHistory));
      } catch (_) {}
    }
  }

  function persistHistory() {
    try {
      localStorage.setItem(HIST_KEY, JSON.stringify(state.agentHistory.slice(-24)));
    } catch (_) {}
  }

  async function runAgentText(text) {
    if (state.busy) return;
    state.busy = true;
    agentMsg("user", text);
    $("#voiceStatus").textContent = "Grok thinking…";
    updateMicUi();
    try {
      const out = await api("/api/agent", {
        method: "POST",
        body: JSON.stringify({
          message: text,
          history: state.agentHistory,
        }),
      });
      if (out.history) applyHistory(out.history);
      else {
        state.agentHistory.push({ role: "user", content: text });
        state.agentHistory.push({
          role: "assistant",
          content: out.reply || "",
        });
        state.agentHistory = state.agentHistory.slice(-24);
        persistHistory();
      }
      persistHistory();
      if (out.tools_run?.length) {
        agentMsg(
          "tools",
          out.tools_run
            .map(
              (t) =>
                `${t.name}(${JSON.stringify(t.args)}) → ${JSON.stringify(t.result)}`
            )
            .join("\n")
        );
      }
      agentMsg("bot", out.reply || "—");
      refreshAll();
      // Prefer spoken reply when in a call
      if (state.inCall && out.reply && !state.muteSpk) {
        try {
          const tts = await api("/api/tts", {
            method: "POST",
            body: JSON.stringify({ text: out.reply }),
          });
          if (tts.audio_b64) await playTts(tts.audio_b64);
        } catch (_) {
          /* text still shown */
        }
      }
      $("#voiceStatus").textContent = state.inCall
        ? state.muteMic
          ? "Call live · mic muted"
          : "Listening…"
        : "Ready";
    } catch (e) {
      agentMsg("bot", "Error: " + e.message);
      $("#voiceStatus").textContent = "Error";
    } finally {
      state.busy = false;
      updateMicUi();
      if (state.inCall && !state.muteMic && !state.recording) {
        setTimeout(() => listenTurn(), 300);
      }
    }
  }

  // ---- Grok-app style voice call: VAD + speak-back + mute controls ----
  function isSecureForMic() {
    if (window.isSecureContext) return true;
    const h = location.hostname;
    return h === "localhost" || h === "127.0.0.1" || h === "[::1]";
  }

  function micSupported() {
    return !!(
      navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia &&
      window.MediaRecorder
    );
  }

  function updateMicUi() {
    const box = $("#secureBox");
    const micBtn = $("#btnMic");
    const endBtn = $("#btnEndCall");
    const muteMic = $("#btnMuteMic");
    const muteSpk = $("#btnMuteSpk");
    const level = $("#voiceLevel");
    const dock = $("#voiceDock");
    if (dock) {
      dock.classList.toggle("in-call", state.inCall);
      dock.classList.toggle("busy", state.busy);
    }
    if (endBtn) endBtn.hidden = !state.inCall;
    if (muteMic) {
      muteMic.hidden = !state.inCall;
      muteMic.textContent = state.muteMic ? "Mic muted" : "Mic";
      muteMic.classList.toggle("muted-on", state.muteMic);
    }
    if (muteSpk) {
      muteSpk.hidden = !state.inCall;
      muteSpk.textContent = state.muteSpk ? "Speaker muted" : "Speaker";
      muteSpk.classList.toggle("muted-on", state.muteSpk);
    }
    if (level) level.hidden = !(state.inCall && state.recording && !state.muteMic);

    let statusLine = "Ready";
    let mainLabel = "Start call";
    let mainDisabled = false;

    const secure = isSecureForMic();
    if (!secure) {
      if (box) {
        box.hidden = false;
        box.innerHTML = "Mic needs HTTPS (or localhost).";
      }
      mainLabel = "Need HTTPS";
      mainDisabled = true;
      statusLine = "Mic blocked";
    } else if (!micSupported()) {
      if (box) {
        box.hidden = false;
        box.innerHTML = "Mic not available.";
      }
      mainLabel = "Mic N/A";
      mainDisabled = true;
      statusLine = "No mic";
    } else {
      if (box) box.hidden = true;
      if (!state.inCall) {
        mainLabel = "Start call";
        mainDisabled = false;
        statusLine = "Ready";
      } else if (state.busy) {
        mainLabel = "…";
        mainDisabled = true;
        statusLine = "Working";
      } else if (state.muteMic) {
        mainLabel = "In call";
        mainDisabled = true;
        statusLine = "Mic muted";
      } else if (state.recording) {
        mainLabel = "Listening";
        mainDisabled = true;
        statusLine = state.speakingHeard ? "Hearing you…" : "Listening";
      } else {
        mainLabel = "In call";
        mainDisabled = true;
        statusLine = "Live";
      }
    }

    if (micBtn) {
      micBtn.disabled = mainDisabled;
      micBtn.textContent = mainLabel;
      micBtn.classList.toggle("rec", state.recording && state.inCall);
    }
    const vs = $("#voiceStatus");
    if (vs) vs.textContent = statusLine;
  }

  /** Build mono 16-bit PCM WAV from Float32 samples (no ffmpeg / no MediaRecorder). */
  function pcmFloatToWavBlob(floatChunks, sampleRate) {
    let total = 0;
    for (const c of floatChunks) total += c.length;
    if (!total) throw new Error("Empty PCM capture");
    // Downsample toward ~16 kHz for smaller/faster STT
    const stride = sampleRate > 24000 ? Math.max(1, Math.round(sampleRate / 16000)) : 1;
    const outRate = Math.round(sampleRate / stride);
    let outLen = 0;
    for (const chunk of floatChunks) {
      for (let i = 0; i < chunk.length; i += stride) outLen++;
    }
    const buf = new ArrayBuffer(44 + outLen * 2);
    const view = new DataView(buf);
    const wstr = (o, s) => {
      for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i));
    };
    wstr(0, "RIFF");
    view.setUint32(4, 36 + outLen * 2, true);
    wstr(8, "WAVE");
    wstr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, outRate, true);
    view.setUint32(28, outRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    wstr(36, "data");
    view.setUint32(40, outLen * 2, true);
    let off = 44;
    for (const chunk of floatChunks) {
      for (let i = 0; i < chunk.length; i += stride) {
        let s = Math.max(-1, Math.min(1, chunk[i]));
        view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        off += 2;
      }
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  /**
   * Fallback: decode MediaRecorder blob → WAV (may fail on some browsers).
   */
  async function blobToWav16k(blob) {
    const ab = await blob.arrayBuffer();
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    let decoded;
    try {
      decoded = await ctx.decodeAudioData(ab.slice(0));
    } catch (e) {
      await ctx.close().catch(() => {});
      throw new Error("Could not decode mic audio: " + (e.message || e));
    }
    const rate = decoded.sampleRate || 48000;
    const nCh = decoded.numberOfChannels;
    const mono = new Float32Array(decoded.length);
    for (let i = 0; i < decoded.length; i++) {
      let s = 0;
      for (let c = 0; c < nCh; c++) s += decoded.getChannelData(c)[i];
      mono[i] = s / nCh;
    }
    await ctx.close().catch(() => {});
    return pcmFloatToWavBlob([mono], rate);
  }

  function setLevelBar(rms) {
    const el = $("#voiceLevel span");
    if (!el) return;
    const pct = Math.min(100, Math.round(rms * 900));
    el.style.width = pct + "%";
  }

  async function playTts(audioB64) {
    if (state.muteSpk || !audioB64) return;
    const audio = $("#voiceAudio");
    if (!audio) return;
    // pause listening while Grok speaks (echo)
    stopListenOnly();
    audio.src = "data:audio/mpeg;base64," + audioB64;
    try {
      await audio.play();
      await new Promise((resolve) => {
        const done = () => {
          audio.removeEventListener("ended", done);
          audio.removeEventListener("error", done);
          resolve();
        };
        audio.addEventListener("ended", done);
        audio.addEventListener("error", done);
        setTimeout(done, 300000); // long teaches → STT can take a few minutes
      });
    } catch (_) {
      /* autoplay */
    }
  }

  function isEmptySpeechError(msg) {
    const m = String(msg || "").toLowerCase();
    return (
      m.includes("empty text") ||
      m.includes("silence") ||
      m.includes("too-short") ||
      m.includes("empty or too-short") ||
      m.includes("speak clearly") ||
      m.includes("stt empty")
    );
  }

  async function resumeListening(delayMs) {
    if (!state.inCall || state.muteMic || state.busy) {
      updateMicUi();
      return;
    }
    setTimeout(() => listenTurn(), delayMs || 400);
  }

  async function sendAudioBlob(blob) {
    if (!state.inCall) return;
    // Guard: tiny blobs are noise, not speech — never hit the API
    if (!blob || blob.size < 1500) {
      $("#voiceStatus").textContent = "Idle — listening for your voice";
      await resumeListening(VAD.emptyBackoffMs);
      return;
    }
    state.busy = true;
    updateMicUi();
    $("#voiceStatus").textContent = "Grok is thinking…";
    // Always upload WAV — never WebM (local desk has no ffmpeg)
    let wavBlob = blob;
    if (!(blob.type || "").includes("wav")) {
      try {
        wavBlob = await blobToWav16k(blob);
      } catch (e) {
        state.busy = false;
        updateMicUi();
        throw new Error(
          "Could not encode mic to WAV (no ffmpeg on this machine). " +
            (e.message || e)
        );
      }
    }
    const fd = new FormData();
    fd.append("file", wavBlob, "voice.wav");
    fd.append("history", JSON.stringify(state.agentHistory || []));
    const res = await fetch("/api/voice", {
      method: "POST",
      headers: headers(false),
      body: fd,
    });
    if (!res.ok) {
      let msg = await res.text();
      try {
        msg = JSON.parse(msg).detail || msg;
      } catch (_) {}
      // Silence / empty STT: stay calm, no toast spam
      if (isEmptySpeechError(msg) || String(msg).toLowerCase().includes("ffmpeg")) {
        state.busy = false;
        $("#voiceStatus").textContent = String(msg).toLowerCase().includes("ffmpeg")
          ? "Mic encode issue — retrying listen"
          : "Idle — listening (no clear speech)";
        updateMicUi();
        await resumeListening(VAD.emptyBackoffMs);
        return;
      }
      throw new Error(msg || res.statusText);
    }
    const out = await res.json();
    if (out.history) applyHistory(out.history);
    if (out.transcript) agentMsg("user", "🎤 " + out.transcript);
    if (out.tools_run?.length) {
      agentMsg(
        "tools",
        out.tools_run
          .map((t) => `${t.name} → ${JSON.stringify(t.result)}`)
          .join("\n")
      );
    }
    if (out.reply) agentMsg("bot", "🔊 " + out.reply);
    if (out.timing_ms) {
      const t = out.timing_ms;
      agentMsg(
        "tools",
        `⏱ stt ${t.stt_ms || "?"}ms · chat ${t.chat_ms || "?"}ms · tts ${t.tts_ms || "?"}ms · total ${t.total_ms || "?"}ms`
      );
    }
    persistHistory();
    refreshAll();
    state.busy = false;
    updateMicUi();
    if (out.audio_b64) {
      $("#voiceStatus").textContent = "Grok speaking…";
      await playTts(out.audio_b64);
    } else if (out.reply && !state.muteSpk) {
      try {
        const tts = await api("/api/tts", {
          method: "POST",
          body: JSON.stringify({ text: out.reply }),
        });
        if (tts.audio_b64) {
          $("#voiceStatus").textContent = "Grok speaking…";
          await playTts(tts.audio_b64);
        }
      } catch (_) {}
    }
    // Cooldown so we don't pick up Grok's own voice as the next turn
    await resumeListening(VAD.postReplyCooldownMs);
  }

  let mediaStream = null;
  let audioCtx = null;
  let analyser = null;
  let scriptNode = null;
  let vadTimer = null;
  let turnStartedAt = 0;
  let speechStartedAt = 0;
  let pcmChunks = []; // Float32Array pieces while speaking
  let pcmPreRoll = []; // recent idle buffers for pre-roll

  function clearVad() {
    if (vadTimer) {
      clearInterval(vadTimer);
      vadTimer = null;
    }
    setLevelBar(0);
  }

  function stopListenOnly() {
    clearVad();
    state.recording = false;
    if (scriptNode) {
      try {
        scriptNode.disconnect();
        scriptNode.onaudioprocess = null;
      } catch (_) {}
      scriptNode = null;
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach((t) => t.stop());
      mediaStream = null;
    }
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
      analyser = null;
    }
    pcmChunks = [];
    pcmPreRoll = [];
    $("#btnMic")?.classList.remove("rec");
  }

  function endCall() {
    state.inCall = false;
    state.busy = false;
    stopListenOnly();
    const audio = $("#voiceAudio");
    if (audio) {
      try {
        audio.pause();
        audio.removeAttribute("src");
      } catch (_) {}
    }
    $("#voiceStatus").textContent = "Call ended";
    updateMicUi();
  }

  async function startCall() {
    if (state.inCall) return;
    if (!isSecureForMic() || !micSupported()) {
      updateMicUi();
      toast("Mic needs secure context (localhost or HTTPS)");
      return;
    }
    state.inCall = true;
    state.muteMic = false;
    updateMicUi();
    $("#voiceStatus").textContent = "Idle — talk when ready (I won't interrupt)";
    agentMsg(
      "bot",
      "🔊 Call live. I'll wait until you speak clearly, then reply. Silence is fine."
    );
    await listenTurn();
  }

  async function listenTurn() {
    if (!state.inCall || state.busy || state.muteMic || state.recording) {
      updateMicUi();
      return;
    }
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      mediaStream.getAudioTracks().forEach((t) => {
        t.enabled = !state.muteMic;
      });

      const Ctx = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctx();
      if (audioCtx.state === "suspended") {
        try {
          await audioCtx.resume();
        } catch (_) {}
      }
      const source = audioCtx.createMediaStreamSource(mediaStream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      // Direct PCM capture → WAV (no WebM, no ffmpeg needed on Mac)
      const bufferSize = 4096;
      scriptNode = audioCtx.createScriptProcessor(bufferSize, 1, 1);
      const silent = audioCtx.createGain();
      silent.gain.value = 0;
      source.connect(scriptNode);
      scriptNode.connect(silent);
      silent.connect(audioCtx.destination);

      pcmChunks = [];
      pcmPreRoll = [];
      state.speakingHeard = false;
      state.silenceMs = 0;
      turnStartedAt = Date.now();
      speechStartedAt = 0;
      let speechArmedMs = 0;
      let speechTotalMs = 0;
      let captureArmed = false;
      const sampleRate = audioCtx.sampleRate || 48000;
      const maxPreChunks = Math.max(3, Math.ceil((0.45 * sampleRate) / bufferSize));

      scriptNode.onaudioprocess = (ev) => {
        if (!state.inCall || state.muteMic) return;
        const input = ev.inputBuffer.getChannelData(0);
        const copy = new Float32Array(input.length);
        copy.set(input);
        if (captureArmed || state.speakingHeard) {
          pcmChunks.push(copy);
        } else {
          pcmPreRoll.push(copy);
          if (pcmPreRoll.length > maxPreChunks) pcmPreRoll.shift();
        }
      };

      const teardownCapture = () => {
        clearVad();
        if (scriptNode) {
          try {
            scriptNode.disconnect();
            scriptNode.onaudioprocess = null;
          } catch (_) {}
          scriptNode = null;
        }
        if (mediaStream) {
          mediaStream.getTracks().forEach((t) => t.stop());
          mediaStream = null;
        }
        // Keep sampleRate before closing ctx
        if (audioCtx) {
          audioCtx.close().catch(() => {});
          audioCtx = null;
          analyser = null;
        }
        state.recording = false;
        $("#btnMic")?.classList.remove("rec");
      };

      const finishTurn = async () => {
        const heard = state.speakingHeard && speechTotalMs >= VAD.minSpeechMs;
        // Snapshot PCM before teardown
        const chunks = pcmChunks.slice();
        const rate = sampleRate;
        teardownCapture();
        pcmChunks = [];
        pcmPreRoll = [];
        if (!state.inCall) return;

        if (!heard || !chunks.length) {
          $("#voiceStatus").textContent = "Idle — listening for your voice";
          updateMicUi();
          await resumeListening(350);
          return;
        }
        try {
          const wav = pcmFloatToWavBlob(chunks, rate);
          if (wav.size < 1500) {
            $("#voiceStatus").textContent = "Idle — listening for your voice";
            await resumeListening(VAD.emptyBackoffMs);
            return;
          }
          await sendAudioBlob(wav);
        } catch (e) {
          const msg = String(e.message || e);
          state.busy = false;
          if (isEmptySpeechError(msg) || msg.toLowerCase().includes("ffmpeg")) {
            $("#voiceStatus").textContent = "Idle — listening (no clear speech)";
            updateMicUi();
            await resumeListening(VAD.emptyBackoffMs);
            return;
          }
          $("#voiceStatus").textContent = "Voice failed";
          toast(msg.slice(0, 140));
          await resumeListening(1000);
        }
      };

      state.recording = true;
      $("#btnMic")?.classList.add("rec");
      $("#voiceStatus").textContent = "Idle — listening for your voice";
      updateMicUi();

      const timeData = new Uint8Array(analyser.fftSize);
      vadTimer = setInterval(() => {
        if (!state.inCall || !analyser || !state.recording) return;
        if (state.muteMic) return;

        analyser.getByteTimeDomainData(timeData);
        let sum = 0;
        for (let i = 0; i < timeData.length; i++) {
          const v = (timeData[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / timeData.length);
        setLevelBar(rms);

        const now = Date.now();
        const loud = rms >= VAD.speakRms;

        if (loud) {
          speechArmedMs += VAD.pollMs;
          speechTotalMs += VAD.pollMs;
          state.silenceMs = 0;
          if (!state.speakingHeard && speechArmedMs >= VAD.speechHoldMs) {
            state.speakingHeard = true;
            captureArmed = true;
            speechStartedAt = now - speechArmedMs;
            // Pre-roll: include last ~0.45s so first words aren't cut
            pcmChunks = pcmPreRoll.map((c) => new Float32Array(c));
            pcmPreRoll = [];
            $("#voiceStatus").textContent =
              "Hearing you… pause ~4s when finished (take your time)";
            updateMicUi();
          } else if (state.speakingHeard) {
            // Still talking — show elapsed so long teaches feel intentional
            const sec = Math.round((now - (speechStartedAt || now)) / 1000);
            if (sec >= 5 && sec % 15 < 1) {
              $("#voiceStatus").textContent = `Hearing you… ${sec}s (pause ~4s when done)`;
            }
          }
        } else {
          speechArmedMs = 0;
          if (state.speakingHeard) {
            state.silenceMs += VAD.pollMs;
          }
        }

        const spokenLongEnough =
          state.speakingHeard && speechTotalMs >= VAD.minSpeechMs;
        const silenceDone = state.silenceMs >= VAD.endSilenceMs;

        // Natural end: you stopped talking long enough
        if (spokenLongEnough && silenceDone) {
          clearVad();
          finishTurn();
          return;
        }

        // Safety only: absurdly long continuous speech (not a short hard cut)
        const speechOrigin = speechStartedAt || turnStartedAt;
        if (
          state.speakingHeard &&
          spokenLongEnough &&
          now - speechOrigin >= VAD.maxSpeechMs
        ) {
          clearVad();
          finishTurn();
        }
      }, VAD.pollMs);
    } catch (e) {
      const name = e && e.name;
      let msg = "Mic failed";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        msg = "Allow microphone for this site";
      } else if (name === "NotFoundError") {
        msg = "No microphone found";
      }
      toast(msg);
      $("#voiceStatus").textContent = msg;
      endCall();
    }
  }

  const mic = $("#btnMic");
  if (mic) {
    mic.addEventListener("click", () => {
      if (!state.inCall) startCall();
    });
  }

  const dockLog = $("#btnDockTranscript");
  if (dockLog) {
    dockLog.addEventListener("click", () => {
      setView("voice");
      refreshAll();
    });
  }

  const endBtn = $("#btnEndCall");
  if (endBtn) {
    endBtn.addEventListener("click", () => endCall());
  }

  const muteMicBtn = $("#btnMuteMic");
  if (muteMicBtn) {
    muteMicBtn.addEventListener("click", () => {
      if (!state.inCall) return;
      state.muteMic = !state.muteMic;
      if (mediaStream) {
        mediaStream.getAudioTracks().forEach((t) => {
          t.enabled = !state.muteMic;
        });
      }
      if (state.muteMic) {
        // stop current capture turn; stay in call
        stopListenOnly();
        $("#voiceStatus").textContent = "Mic muted — unmute to talk";
      } else if (!state.busy) {
        listenTurn();
      }
      updateMicUi();
    });
  }

  const muteSpkBtn = $("#btnMuteSpk");
  if (muteSpkBtn) {
    muteSpkBtn.addEventListener("click", () => {
      if (!state.inCall) return;
      state.muteSpk = !state.muteSpk;
      const audio = $("#voiceAudio");
      if (state.muteSpk && audio) {
        try {
          audio.pause();
        } catch (_) {}
      }
      updateMicUi();
      toast(state.muteSpk ? "Speaker muted" : "Speaker on");
    });
  }

  const clearHist = $("#btnClearHist");
  if (clearHist) {
    clearHist.addEventListener("click", () => {
      state.agentHistory = [];
      try {
        localStorage.removeItem(HIST_KEY);
      } catch (_) {}
      const log = $("#agentLog");
      if (log) log.innerHTML = "";
      agentMsg("bot", "Conversation cleared (including saved history).");
      toast("History cleared");
    });
  }

  $("#agentForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#agentInput").value.trim();
    if (!v) return;
    $("#agentInput").value = "";
    runAgentText(v);
  });



  $("#posForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/positions", {
        method: "POST",
        body: JSON.stringify({
          symbol: f.symbol.value,
          market: f.market.value,
          entry_avg: f.entry_avg.value ? +f.entry_avg.value : null,
          notes: f.notes.value || null,
        }),
      });
      f.reset();
      toast("Position opened");
      loadPositions();
    } catch (err) {
      toast(err.message);
    }
  });

  $("#watchForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/watchlist", {
        method: "POST",
        body: JSON.stringify({
          symbol: f.symbol.value,
          market: f.market.value,
          set_id: _activeMoverSetId,
        }),
      });
      f.reset();
      toast("Added to set");
      loadMovers();
    } catch (err) {
      toast(err.message);
    }
  });

  $("#moversForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/movers", {
        method: "POST",
        body: JSON.stringify({
          enabled: f.enabled.checked,
          threshold_percent: f.threshold_percent.value
            ? +f.threshold_percent.value
            : null,
          lookback_minutes: f.lookback_minutes.value
            ? +f.lookback_minutes.value
            : null,
          set_id: _activeMoverSetId,
        }),
      });
      toast("Set settings saved");
      loadMovers();
    } catch (err) {
      toast(err.message);
    }
  });

  const moverSetSelect = $("#moverSetSelect");
  if (moverSetSelect) {
    moverSetSelect.addEventListener("change", () => {
      _activeMoverSetId = +moverSetSelect.value;
      loadMovers();
    });
  }
  const moverSetNew = $("#moverSetNew");
  if (moverSetNew) {
    moverSetNew.addEventListener("click", async () => {
      const name = prompt("New mover set name (e.g. Panic 7% / Grind 4%)");
      if (!name) return;
      try {
        const s = await api("/api/mover-sets", {
          method: "POST",
          body: JSON.stringify({
            name,
            threshold_percent: 5,
            lookback_minutes: 15,
            enabled: false,
          }),
        });
        _activeMoverSetId = s.id;
        toast(`Created set ${s.name}`);
        loadMovers();
      } catch (e) {
        toast(e.message);
      }
    });
  }
  const moverSetRename = $("#moverSetRename");
  if (moverSetRename) {
    moverSetRename.addEventListener("click", async () => {
      if (_activeMoverSetId == null) return;
      const name = prompt("Rename set to:");
      if (!name) return;
      try {
        await api(`/api/mover-sets/${_activeMoverSetId}`, {
          method: "PATCH",
          body: JSON.stringify({ name }),
        });
        toast("Renamed");
        loadMovers();
      } catch (e) {
        toast(e.message);
      }
    });
  }
  const moverSetDelete = $("#moverSetDelete");
  if (moverSetDelete) {
    moverSetDelete.addEventListener("click", async () => {
      if (_activeMoverSetId == null) return;
      if (!confirm("Delete this set and its coins? Default set cannot be deleted.")) return;
      try {
        await api(`/api/mover-sets/${_activeMoverSetId}`, { method: "DELETE" });
        _activeMoverSetId = null;
        toast("Set deleted");
        loadMovers();
      } catch (e) {
        toast(e.message);
      }
    });
  }

  $("#alertForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/api/alerts", {
        method: "POST",
        body: JSON.stringify({
          symbol: f.symbol.value,
          price: +f.price.value,
          market: f.market.value,
        }),
      });
      f.reset();
      toast("Alert added");
      loadTargets();
    } catch (err) {
      toast(err.message);
    }
  });

  $$("[data-label]").forEach((b) =>
    b.addEventListener("click", async () => {
      try {
        const r = await api("/api/events/label", {
          method: "POST",
          body: JSON.stringify({ action: b.dataset.label }),
        });
        toast(`Labeled #${r.event_id}`);
        loadMemory();
      } catch (e) {
        toast(e.message);
      }
    })
  );

  async function refreshAll() {
    try {
      const h = await api("/api/health");
      const st = $("#connStatus");
      st.textContent = h.db_exists ? "live · db" : "live · empty db";
      st.className = "pill ok";
      const xb = $("#xaiBadge");
      if (xb) xb.textContent = h.xai_configured ? "XAI ready" : "set XAI_API_KEY";
    } catch (e) {
      $("#connStatus").textContent = "offline";
      $("#connStatus").className = "pill err";
    }
    const map = {
      overview: loadOverview,
      positions: () => loadPositions({ force: true }),
      pnl: loadPnl,
      movers: loadMovers,
      targets: loadTargets,
      memory: loadMemory,
      intel: loadIntel,
      roadmap: loadRoadmap,
      playbook: loadPlaybook,
      voice: async () => {
        updateMicUi();
        try {
          const h = await api("/api/health");
          $("#xaiBadge").textContent = h.xai_configured ? "XAI ready" : "set XAI_API_KEY";
        } catch (_) {}
      },
    };
    try {
      await (map[state.view] || loadOverview)();
    } catch (e) {
      console.error(e);
      toast(String(e.message || e).slice(0, 140));
    }
  }

  $$(".nav button").forEach((b) =>
    b.addEventListener("click", () => {
      setView(b.dataset.view);
      refreshAll();
    })
  );
  $$(".pnl-win").forEach((b) =>
    b.addEventListener("click", () => {
      $$(".pnl-win").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      state.pnlWindow = b.dataset.pnlWin || "30d";
      loadPnl();
    })
  );
  $("#btnRefresh").addEventListener("click", refreshAll);

  // Overview jump buttons (Targets / Movers / Learning / …)
  document.body.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof HTMLElement)) return;
    const jump = t.getAttribute("data-jump");
    if (jump) {
      setView(jump);
      refreshAll();
    }
  });

  wireLearningForms();
  // Live desk: soft poll (~10s) + fire alarms (~5s) + health
  const DESK_SOFT_MS = 10000;
  const DESK_FIRE_POLL_MS = 5000;
  const HEALTH_MS = 60000;
  let softTimer = null;
  let healthTimer = null;
  let fireTimer = null;

  async function refreshHealthOnly() {
    if (document.hidden || state.inCall || state.busy) return;
    try {
      const h = await api("/api/health");
      const st = $("#connStatus");
      if (st) {
        st.textContent = h.db_exists ? "live · db" : "live · empty db";
        st.className = "pill ok";
      }
      const xb = $("#xaiBadge");
      if (xb) xb.textContent = h.xai_configured ? "XAI ready" : "set XAI_API_KEY";
    } catch (_) {
      const st = $("#connStatus");
      if (st) {
        st.textContent = "offline";
        st.className = "pill err";
      }
    }
  }
  state.lastSeenFireId = Number(localStorage.getItem("desk_last_fire_id") || 0) || 0;
  state.lastAlarmFlashId = null;
  state.alarmSoundOn = localStorage.getItem("desk_alarm_sound") === "1";
  state.alarmAudioArmed = false;

  function initAlarmSoundUi() {
    const tog = $("#alarmSoundToggle");
    if (!tog) return;
    tog.checked = !!state.alarmSoundOn;
    tog.addEventListener("change", () => {
      state.alarmSoundOn = !!tog.checked;
      localStorage.setItem("desk_alarm_sound", state.alarmSoundOn ? "1" : "0");
      // User gesture arms WebAudio / Audio play
      state.alarmAudioArmed = true;
      if (state.alarmSoundOn) {
        playAlarmSound(true);
        toast("Alarm sound on");
      } else {
        toast("Alarm sound off");
      }
    });
  }

  function playAlarmSound(quietTest) {
    if (!state.alarmSoundOn && !quietTest) return;
    if (!state.alarmAudioArmed && !quietTest) return;
    try {
      const ctx = window.AudioContext || window.webkitAudioContext;
      if (!ctx) return;
      if (!playAlarmSound._ctx) playAlarmSound._ctx = new ctx();
      const ac = playAlarmSound._ctx;
      if (ac.state === "suspended") ac.resume();
      const o = ac.createOscillator();
      const g = ac.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(880, ac.currentTime);
      o.frequency.exponentialRampToValueAtTime(1320, ac.currentTime + 0.08);
      g.gain.setValueAtTime(0.0001, ac.currentTime);
      g.gain.exponentialRampToValueAtTime(0.12, ac.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + 0.28);
      o.connect(g);
      g.connect(ac.destination);
      o.start();
      o.stop(ac.currentTime + 0.3);
    } catch (_) {}
  }

  function alarmToastLine(a) {
    const src = String(a.source || "");
    const sym = a.symbol || "?";
    const drop = a.drop_pct != null ? Number(a.drop_pct).toFixed(1) + "%" : "";
    if (src === "target" || src.includes("target")) {
      return `TARGET ${sym}${a.price != null ? " @ " + fmtPx(a.price) : ""}`;
    }
    const mode =
      a.mode ||
      (src.includes("step") ? "step" : src.includes("peak") ? "peak" : "mover");
    return `MOVER ${sym} ${drop} ${mode}`.trim();
  }

  async function pollDeskAlarms() {
    if (document.hidden || state.inCall) return;
    try {
      const q =
        state.lastSeenFireId > 0
          ? `?since_id=${state.lastSeenFireId}&limit=20`
          : `?limit=15`;
      const d = await api("/api/desk/alarms" + q);
      const alarms = d.alarms || [];
      if (!alarms.length) {
        if (d.max_id && !state.lastSeenFireId) {
          state.lastSeenFireId = +d.max_id;
          localStorage.setItem("desk_last_fire_id", String(state.lastSeenFireId));
        }
        return;
      }
      // Bootstrap: first poll only seeds cursor (no toast flood)
      if (!state.lastSeenFireId) {
        let mx = 0;
        alarms.forEach((a) => {
          mx = Math.max(mx, +a.id || 0);
        });
        state.lastSeenFireId = mx || +d.max_id || 0;
        localStorage.setItem("desk_last_fire_id", String(state.lastSeenFireId));
        return;
      }
      const fresh = alarms.filter((a) => +a.id > state.lastSeenFireId);
      if (!fresh.length) return;
      let mx = state.lastSeenFireId;
      fresh.forEach((a) => {
        mx = Math.max(mx, +a.id || 0);
      });
      state.lastSeenFireId = mx;
      localStorage.setItem("desk_last_fire_id", String(mx));
      // Newest last for toast order
      const ordered = fresh.slice().sort((a, b) => (+a.id || 0) - (+b.id || 0));
      const last = ordered[ordered.length - 1];
      state.lastAlarmFlashId = last && last.id != null ? +last.id : null;
      toast(alarmToastLine(last) + (ordered.length > 1 ? ` · +${ordered.length - 1}` : ""));
      if (state.alarmSoundOn) playAlarmSound();
      // Soft refresh surfaces that show fires
      if (state.view === "overview") {
        try {
          await loadOverview();
        } catch (_) {}
      } else if (state.view === "movers") {
        try {
          await loadMovers({ soft: true });
        } catch (_) {}
      }
    } catch (_) {
      /* quiet — offline / no learning table */
    }
  }

  async function softRefreshView() {
    if (document.hidden || state.inCall || state.busy) return;
    if (state.view === "positions") {
      await loadPositions({ soft: true });
      return;
    }
    try {
      if (state.view === "overview") {
        await loadOverview();
      } else if (state.view === "movers") {
        await loadMovers({ soft: true });
      } else if (state.view === "targets") {
        await loadTargets({ soft: true });
      }
    } catch (e) {
      console.error(e);
    }
  }

  function scheduleSoftRefresh() {
    if (softTimer) clearInterval(softTimer);
    if (healthTimer) clearInterval(healthTimer);
    if (fireTimer) clearInterval(fireTimer);
    softTimer = setInterval(softRefreshView, DESK_SOFT_MS);
    healthTimer = setInterval(refreshHealthOnly, HEALTH_MS);
    fireTimer = setInterval(pollDeskAlarms, DESK_FIRE_POLL_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshHealthOnly();
      pollDeskAlarms();
    }
  });

  initAlarmSoundUi();
  setView("overview");
  updateMicUi();
  refreshAll();
  scheduleSoftRefresh();
  // Seed fire cursor shortly after load
  setTimeout(() => pollDeskAlarms(), 800);
})();
