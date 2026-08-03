/* AD Desk v2.1 — HTTPS-first voice + full desk control */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

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

  // Calmer VAD: only commit a turn after sustained real speech + silence.
  // Avoids spam on ambient noise / empty STT loops.
  const VAD = {
    speakRms: 0.038, // slightly more sensitive so it arms faster on your voice
    silenceRms: 0.02,
    endSilenceMs: 2000, // allow thinking pauses without cutting you off
    minSpeechMs: 550,
    speechHoldMs: 180, // arm "hearing you" sooner
    maxTurnMs: 60000,
    pollMs: 70,
    postReplyCooldownMs: 1200, // longer ignore after TTS (less echo false-turns)
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
        msg = JSON.parse(msg).detail || msg;
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
      memory: "Learning",
      intel: "Intel",
      voice: "Voice log",
      roadmap: "Roadmap",
      playbook: "Playbook",
    };
    $("#title").textContent = titles[name] || "Desk";
    const sub = $("#subtitle");
    if (sub) {
      sub.textContent = "";
      sub.hidden = true;
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

    // High-priority book news only (targets / movers / positions). Hidden if none.
    const newsEl = $("#ovBookNews");
    if (newsEl) {
      const bn = h.book_news || [];
      if (!bn.length) {
        newsEl.hidden = true;
        newsEl.innerHTML = "";
      } else {
        newsEl.hidden = false;
        newsEl.innerHTML =
          `<div class="ov-news-h">Book news</div>` +
          bn
            .map(
              (n) => `<div class="ov-news-row">
              <span class="ov-news-sev">${(n.severity || n.class || "news").toString().slice(0, 12)}</span>
              <div class="ov-news-body">
                <div class="ov-news-title">${(n.title || "—").slice(0, 120)}</div>
                <div class="ov-news-meta">${n.symbol || "—"} · ${n.source || ""} · ${fmtTime(n.ts)}</div>
              </div>
            </div>`
            )
            .join("");
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
      : rankEmpty("No mover fires in last hours");

    const pos = h.positions || d.positions || [];
    $("#ovPos").innerHTML = pos.length
      ? pos
          .map((p) => {
            const entry = p.entry_display != null ? p.entry_display : p.entry_avg;
            const up = p.upnl_pct;
            const upS =
              up != null
                ? `<span class="${up >= 0 ? "up" : "dn"}">${
                    up >= 0 ? "+" : ""
                  }${Number(up).toFixed(1)}%</span>`
                : "—";
            const truth =
              p.exchange_hold || p.verified || p.money_truth === "exchange"
                ? "LIVE"
                : p.money_truth === "fill_recon_unverified"
                  ? "FILL?"
                  : "";
            return `<div class="cmd-row pos">
            <div class="cmd-row-main">
              <div>
                <div class="cmd-sym">${p.symbol}${
              truth
                ? ` <span class="pos-src">${truth}</span>`
                : ""
            }</div>
                <div class="cmd-meta">avg ${
                  entry != null ? fmtPx(entry) : "—"
                } · mark ${
                  p.mark_price != null ? fmtPx(p.mark_price) : "—"
                }</div>
                <div class="cmd-meta">qty ${
                  p.size_remaining != null
                    ? Number(p.size_remaining).toFixed(2)
                    : "—"
                } · ${p.hold_hours != null ? p.hold_hours + "h" : ""}${
              p.upnl_usd_est != null
                ? " · ~$" + Number(p.upnl_usd_est).toFixed(0)
                : ""
            }</div>
              </div>
            </div>
            <div class="cmd-side">
              <div class="cmd-px">${upS}</div>
            </div>
          </div>`;
          })
          .join("")
      : rankEmpty("No open positions");

    // Book-matched intel (investigations) — only when present
    const intelEl = $("#ovBookIntel");
    if (intelEl) {
      const bi = h.book_intel || [];
      if (!bi.length) {
        intelEl.hidden = true;
        intelEl.innerHTML = "";
      } else {
        intelEl.hidden = false;
        intelEl.innerHTML =
          `<div class="panel-h"><h3>Book intel</h3><button type="button" class="btn soft sm" data-jump="intel">Open</button></div>` +
          bi
            .map(
              (i) => `<div class="cmd-row">
              <div class="cmd-row-main">
                <div class="cmd-sym">${i.symbol || "—"}</div>
                <div class="cmd-meta">${i.verdict || i.velocity_band || "intel"}${
                i.drop_pct != null ? " · " + Number(i.drop_pct).toFixed(1) + "%" : ""
              }</div>
              </div>
              <div class="cmd-px">${(i.confidence != null ? Number(i.confidence).toFixed(2) : "—")}</div>
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
    const o = (p.outcome || p.status || "").toLowerCase();
    if (o === "open" || p.status === "open") {
      return `<span class="pos-outcome open">OPEN</span>`;
    }
    if (o === "success") return `<span class="pos-outcome success">WIN</span>`;
    if (o === "miss") return `<span class="pos-outcome miss">MISS</span>`;
    return `<span class="pos-outcome flat">FLAT</span>`;
  }

  function posPnlClass(p, isOpen) {
    if (isOpen) {
      const u = p.upnl_pct;
      if (u == null) return "mute";
      return u >= 0 ? "up" : "dn";
    }
    const o = (p.outcome || "").toLowerCase();
    if (o === "success") return "up";
    if (o === "miss") return "dn";
    return "mute"; // flat band — not green on +0.2%
  }

  function posPnlLine(p) {
    const isOpen = p.status === "open" || p.is_open;
    const cls = posPnlClass(p, isOpen);
    if (isOpen) {
      const upnl = p.upnl_pct;
      if (upnl == null) return `<span class="mute">—</span>`;
      return `<span class="${cls}">${upnl >= 0 ? "+" : ""}${Number(upnl).toFixed(1)}%</span>`;
    }
    const rp = p.realized_pnl_pct;
    if (rp == null) return `<span class="mute">—</span>`;
    return `<span class="${cls}">${rp >= 0 ? "+" : ""}${Number(rp).toFixed(1)}%</span>`;
  }

  function posMarketPill(m) {
    const x = (m || "").toLowerCase();
    if (x === "futures") return `<span class="pos-mkt fut">FUT</span>`;
    if (x === "spot") return `<span class="pos-mkt spot">SPOT</span>`;
    return `<span class="pos-mkt">?</span>`;
  }

  function posCardHtml(p) {
    const isOpen = p.status === "open" || p.is_open;
    const entry = p.entry_display != null ? p.entry_display : p.entry_avg;
    const exit_ = p.exit_avg;
    const mark = p.mark_price;
    const pnlS = posPnlLine(p);
    const usd =
      isOpen && p.upnl_usd_est != null
        ? `<span class="pos-usd">~$${Number(p.upnl_usd_est).toFixed(0)}</span>`
        : !isOpen && p.realized_pnl_usd != null
          ? `<span class="pos-usd">$${Number(p.realized_pnl_usd).toFixed(0)}</span>`
          : "";
    const when = isOpen
      ? p.hold_hours != null
        ? p.hold_hours >= 24
          ? (p.hold_hours / 24).toFixed(1) + "d held"
          : p.hold_hours + "h held"
        : ""
      : p.closed_ago_seconds != null
        ? "closed " +
          (function () {
            const sec = p.closed_ago_seconds;
            if (sec < 60) return Math.round(sec) + "s ago";
            if (sec < 3600) return Math.round(sec / 60) + "m ago";
            if (sec < 86400) return (sec / 3600).toFixed(1) + "h ago";
            return (sec / 86400).toFixed(1) + "d ago";
          })()
        : p.hold_hours != null
          ? "held " + p.hold_hours + "h"
          : "";
    const layersBit =
      (p.n_buys || p.n_sells)
        ? ` · B${p.n_buys || 0}/S${p.n_sells || 0}`
        : "";
    const sizeBit = isOpen
      ? p.size_remaining != null
        ? "qty " + Number(p.size_remaining).toFixed(2) + layersBit
        : "qty —"
      : p.size_qty != null
        ? "sz " + Number(p.size_qty).toFixed(2) + layersBit
        : "sz —";
    const buys = (p.buy_orders || [])
      .map(
        (o) =>
          `<div class="pos-fill buy">BUY ${
            o.price != null ? fmtPx(o.price) : "—"
          } × ${o.qty != null ? o.qty : "—"} · ${fmtTime(o.ts)}</div>`
      )
      .join("");
    const sells = (p.sell_orders || [])
      .map(
        (o) =>
          `<div class="pos-fill sell">SELL ${
            o.price != null ? fmtPx(o.price) : "—"
          } × ${o.qty != null ? o.qty : "—"} · ${fmtTime(o.ts)}</div>`
      )
      .join("");
    const canCloseJournal =
      isOpen && p.journal_id != null && Number(p.journal_id) > 0;
    const holdAvg =
      p.hold_avg != null && p.hold_avg !== entry
        ? ` · inv ${fmtPx(p.hold_avg)}`
        : "";
    const fundBit =
      isOpen && p.hold_fee != null && Number(p.hold_fee) !== 0
        ? ` · fund ${Number(p.hold_fee) >= 0 ? "+" : ""}${Number(p.hold_fee).toFixed(2)}`
        : "";
    const priceDetail = isOpen
      ? `live avg ${entry != null ? fmtPx(entry) : "—"}${holdAvg} · mark ${
          mark != null ? fmtPx(mark) : "—"
        }${fundBit}`
      : `in ${entry != null ? fmtPx(entry) : "—"} → out ${
          exit_ != null ? fmtPx(exit_) : "—"
        }`;
    const posId =
      p.entity_key ||
      p.exchange_position_id ||
      `${p.market || "?"}:${p.symbol}:${p.status}:${p.opened_at || ""}:${p.closed_at || ""}`;
    const exchBadge =
      p.exchange_history || p.money_truth === "exchange"
        ? `<span class="pos-src" title="MEXC history_positions">EXCH</span>`
        : p.exchange_hold
          ? `<span class="pos-src" title="MEXC open_positions">LIVE</span>`
          : p.money_truth === "fill_recon_unverified" || p.verified === false
            ? `<span class="pos-src" title="Fill residual — not balance-verified">FILL?</span>`
            : "";
    const sideBit =
      p.position_side === "short"
        ? " · short"
        : p.position_side === "long"
          ? " · long"
          : "";
    return `<details class="pos-card ${isOpen ? "is-open" : "is-closed"} outcome-${
      (p.outcome || "flat").toLowerCase()
    }" data-pos-id="${String(posId).replace(/"/g, "")}">
      <summary class="pos-sum">
        <span class="pos-sym">${p.symbol}</span>
        ${posOutcomeBadge(p)}
        <span class="pos-pnl">${pnlS}${usd ? " " + usd : ""}</span>
        <span class="pos-when">${when}</span>
        <span class="pos-size">${sizeBit}</span>
        ${posMarketPill(p.market)}
        ${exchBadge}
        <span class="pos-chev" aria-hidden="true">▾</span>
      </summary>
      <div class="pos-detail">
        <div class="learn-card-meta">${priceDetail}${sideBit}${
      p.leverage != null ? " · " + p.leverage + "x" : ""
    } · ${isOpen ? "opened" : "cycle"} ${fmtTime(p.opened_at)}${
      p.closed_at ? " → " + fmtTime(p.closed_at) : ""
    }</div>
        <div class="learn-card-meta">${
          p.exchange_history
            ? "Exchange realised $" +
              (p.realized_pnl_usd != null
                ? Number(p.realized_pnl_usd).toFixed(2)
                : "—") +
              (p.fee != null ? " · fee " + p.fee : "")
            : `bought ${p.size_qty ?? "—"} / sold ${p.size_sold ?? "—"} · ${
                p.n_buys || 0
              } buys · ${p.n_sells || 0} sells`
        }</div>
        <div class="learn-card-meta">Notes: ${(p.notes || "—").slice(0, 120)}</div>
        <div class="pos-fills-h">Buy layers (${p.n_buys || 0})</div>
        ${
          buys ||
          "<div class='mute'>No buy deals in window — wait for fill sync</div>"
        }
        <div class="pos-fills-h">Sell layers (${p.n_sells || 0})</div>
        ${
          sells ||
          (isOpen
            ? "<div class='mute'>No sell deals yet (or not synced)</div>"
            : "<div class='mute'>No sell deals recorded</div>")
        }
        ${
          canCloseJournal
            ? `<div class="row-gap mt">
          <button type="button" class="btn soft sm" data-close="${p.journal_id}">Close journal</button>
        </div>`
            : ""
        }
      </div>
    </details>`;
  }

  let _posFingerprint = "";
  let _posLastInteract = 0;
  let _posWired = false;

  function posFingerprint(positions) {
    return (positions || [])
      .map(
        (p) =>
          `${p.entity_key || p.symbol}:${p.status}:${p.realized_pnl_usd ?? ""}:${
            p.upnl_pct ?? ""
          }:${p.size_remaining ?? ""}:${p.outcome || ""}`
      )
      .join("|");
  }

  function renderPositionsList(positions) {
    const host = $("#posTable");
    if (!host) return;
    const opens = positions.filter((p) => p.status === "open" || p.is_open);
    const closed = positions.filter((p) => !(p.status === "open" || p.is_open));
    const head = $("#posListHead");
    if (head) {
      head.textContent = `${opens.length} open · ${closed.length} closed`;
    }
    let html = "";
    if (opens.length) {
      html += `<div class="pos-band-h">Open risk <span class="pos-band-n">${opens.length}</span></div>`;
      html += opens.map(posCardHtml).join("");
    } else {
      html += `<div class="pos-band-h mute">No open risk</div>`;
    }
    if (closed.length) {
      html += `<div class="pos-band-h closed">Closed outcomes <span class="pos-band-n">${closed.length}</span></div>`;
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
    host.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-close]");
      if (!b || !host.contains(b)) return;
      ev.preventDefault();
      try {
        await api("/api/positions/close", {
          method: "POST",
          body: JSON.stringify({ trade_id: +b.dataset.close }),
        });
        toast("Position closed");
        loadPositions({ force: true });
      } catch (e) {
        toast(e.message);
      }
    });
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
    renderPositionsList(positions);
    _posFingerprint = fp;

    // restore expand + scroll after rebuild
    host.querySelectorAll("details[data-pos-id]").forEach((el) => {
      if (openIds.has(el.dataset.posId)) el.open = true;
    });
    host.scrollTop = scrollY;
  }

  async function loadMovers() {
    const d = await api("/api/watchlist");
    const s = d.settings;
    $("#mwBadge").textContent = s
      ? `${s.enabled ? "ON" : "OFF"} · ${s.threshold_percent}% · ${Math.round(
          (s.lookback_seconds || 0) / 60
        )}m`
      : "movers";
    if (s) {
      const f = $("#moversForm");
      f.enabled.checked = !!s.enabled;
      f.threshold_percent.value = s.threshold_percent ?? "";
      f.lookback_minutes.value = s.lookback_seconds
        ? Math.round(s.lookback_seconds / 60)
        : "";
    }
    const by = {};
    (d.tickers || []).forEach((t) => (by[t.symbol] = t));
    const rows = (d.watchlist || [])
      .map((w) => {
        const key = String(w.symbol).toUpperCase().replace(/_/g, "");
        const t = by[key] || by[key.replace("USDT", "") + "USDT"];
        const chg = t ? +t.changePercent : null;
        return `<tr>
          <td>${w.market === "futures" ? "F" : "S"}</td>
          <td>${w.symbol}</td>
          <td>${t ? fmtPx(t.price) : "—"}</td>
          <td class="${chg != null && chg < 0 ? "dn" : "up"}">${fmtChg(chg)}</td>
          <td><button type="button" class="btn soft sm" data-unwatch="${w.symbol}" data-m="${w.market}">✕</button></td>
        </tr>`;
      })
      .join("");
    const tableEl = $("#moversTable") || $("#tapeTable");
    if (tableEl) tableEl.innerHTML = table(["M", "Symbol", "Mark", "24h", ""], rows);
    $$("[data-unwatch]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(
            `/api/watchlist?symbol=${encodeURIComponent(b.dataset.unwatch)}&market=${b.dataset.m}`,
            { method: "DELETE" }
          );
          toast("Removed from watchlist");
          loadMovers();
        } catch (e) {
          toast(e.message);
        }
      })
    );
  }

  async function loadTargets() {
    const d = await api("/api/alerts");
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

  // Learning V1 — trade-first teaching context
  state.learnSel = null; // { type, symbol, market, entity_key, event_id, label, detail }
  state.learnBehaviors = [];

  function setLearnSelection(sel) {
    state.learnSel = sel;
    state.learnBehaviors = [];
    $$(".chip", $("#learnBehaviorChips")).forEach((c) =>
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
          "Select a trade or fire on the left — then write the lesson.";
      }
      if (det) {
        det.hidden = true;
        det.innerHTML = "";
      }
      if (sub) sub.disabled = true;
      if (ek) ek.value = "";
      if (sy) sy.value = "";
      if (mk) mk.value = "";
      if (ev) ev.value = "";
      if (ct) ct.value = "";
      $$(".learn-pick").forEach((el) => el.classList.remove("selected"));
      return;
    }
    if (bar) {
      bar.className = "learn-context on";
      bar.innerHTML = `<strong>Teaching about:</strong> ${escHtml(
        sel.label
      )}`;
    }
    if (det) {
      det.hidden = false;
      det.innerHTML = escHtml(sel.detail || "");
    }
    if (sub) sub.disabled = false;
    if (ek) ek.value = sel.entity_key || "";
    if (sy) sy.value = sel.symbol || "";
    if (mk) mk.value = sel.market || "";
    if (ev) ev.value = sel.event_id != null ? String(sel.event_id) : "";
    if (ct) ct.value = sel.type || "";
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
      } · skip ${stats.skip || 0} · lessons ${lessons.length}`;
    }
    if (stB) stB.textContent = `${(trades || []).length} trades`;

    const tradeList = $("#learnTradeList");
    if (tradeList) {
      const list = trades || [];
      tradeList.innerHTML = list.length
        ? list
            .map((t, i) => {
              const pnl = t.pnl_pct;
              const pnlS =
                pnl != null
                  ? `<span class="${pnl >= 0 ? "up" : "dn"}">${
                      pnl >= 0 ? "+" : ""
                    }${Number(pnl).toFixed(1)}%</span>`
                  : t.status || "—";
              const ek = t.entity_key || t.id || `t${i}`;
              const layers = `B${t.n_buys || 0}/S${t.n_sells || 0}`;
              const sel =
                state.learnSel &&
                state.learnSel.entity_key === String(ek)
                  ? " selected"
                  : "";
              return `<div class="learn-card rich learn-pick${sel}" data-pick-trade="${escHtml(
                String(ek)
              )}" data-i="${i}">
                <div class="learn-card-h">${escHtml(t.symbol)} · ${escHtml(
                (t.market || "?").toString().slice(0, 1).toUpperCase()
              )} · ${pnlS}</div>
                <div class="learn-card-meta">${escHtml(
                  t.status || ""
                )} · ${layers} · ${escHtml(
                t.money_truth || ""
              )} · ${fmtTime(t.closed_at || t.opened_at)}</div>
                <div class="row-gap mt">
                  <button type="button" class="btn sm" data-pick-trade-btn="${escHtml(
                    String(ek)
                  )}" data-i="${i}">Teach about this trade</button>
                </div>
              </div>`;
            })
            .join("")
        : rankEmpty("No teach_ok trades yet — Positions feed this list");
      const pickTrade = (i) => {
        const t = list[i];
        if (!t) return;
        const pnl = t.pnl_pct;
        const detail = [
          t.status,
          t.entry_avg != null ? "entry " + t.entry_avg : "",
          t.exit_avg != null ? "exit " + t.exit_avg : "",
          t.pnl_usd != null ? "$" + t.pnl_usd : "",
          `layers B${t.n_buys || 0}/S${t.n_sells || 0}`,
          t.money_truth || "",
        ]
          .filter(Boolean)
          .join(" · ");
        setLearnSelection({
          type: "trade",
          symbol: t.symbol,
          market: t.market || "futures",
          entity_key: String(t.entity_key || t.id || ""),
          event_id: t.primary_event_id || null,
          label: `${t.symbol} ${(t.market || "").toString()} · ${
            t.status
          } · ${pnl != null ? (pnl >= 0 ? "+" : "") + Number(pnl).toFixed(1) + "%" : "—"}`,
          detail,
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
          label: `FIRE ${e.symbol} #${e.id} · ${e.velocity_band || ""} · ${
            e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : ""
          }`,
          detail: `Fire @ ${e.price ?? "—"} · ${fmtTime(e.ts)} · action ${
            e.last_action || "unlabeled"
          }`,
        });
      };
      $$("[data-pick-fire-btn]", fireList).forEach((b) =>
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          pickFire(+b.dataset.i);
        })
      );
    }

    // 4 Lessons with about context
    const lesEl = $("#learnLessons");
    if (lesEl) {
      lesEl.innerHTML = lessons.length
        ? lessons
            .map((l) => {
              const tags = (() => {
                try {
                  return JSON.parse(l.tags_json || "[]");
                } catch (_) {
                  return l.tags || [];
                }
              })();
              const symTag = (tags || []).find(
                (x) => typeof x === "string" && x.startsWith("sym:")
              );
              const about = symTag
                ? symTag.slice(4)
                : (l.text || "").startsWith("[")
                  ? "linked"
                  : "general";
              return `<div class="learn-lesson">
                <span class="learn-about">${escHtml(about)}</span>
                ${escHtml((l.text || "").slice(0, 220))}
              </div>`;
            })
            .join("")
        : rankEmpty(
            "No lessons yet. Select a trade → teach about it → Save lesson."
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

    // process + AD chips (same handler)
    const chipRoots = [$("#learnBehaviorChips"), $("#learnAdChips")].filter(
      Boolean
    );
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
          toast("Select a trade or fire first — lessons must attach to something");
          return;
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
              behaviors: state.learnBehaviors || [],
            }),
          });
          toast(`Lesson saved on ${sel.symbol}`);
          if ($("#teachText")) $("#teachText").value = "";
          state.learnBehaviors = [];
          $$(".chip", $("#learnBehaviorChips")).forEach((c) =>
            c.classList.remove("on")
          );
          loadMemory();
        } catch (e) {
          toast(e.message);
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
      api("/api/news"),
      api("/api/investigations"),
    ]);
    $("#newsTable").innerHTML = table(
      ["Class", "Sym", "Title", "Src", "When"],
      (news.news || [])
        .map(
          (n) =>
            `<tr><td>${n.class || ""}</td><td>${n.symbol || "—"}</td><td>${(n.title || "").slice(0, 70)}</td><td>${n.source || ""}</td><td>${fmtTime(n.ts)}</td></tr>`
        )
        .join("")
    );
    $("#delistTable").innerHTML = table(
      ["CEX", "Base", "Kind", "Title", "When"],
      (news.delist_cache || [])
        .map(
          (d) =>
            `<tr><td>${d.exchange}</td><td>${d.base || "—"}</td><td>${d.kind}</td><td>${(d.title || "").slice(0, 60)}</td><td>${fmtTime(d.ts)}</td></tr>`
        )
        .join("")
    );
    $("#sourcesTable").innerHTML = table(
      ["Source", "Kind", "W", "Hits", "Conf", "False"],
      (inv.sources || [])
        .map(
          (s) =>
            `<tr><td>${s.source}</td><td>${s.kind}</td><td>${Number(s.weight).toFixed(2)}</td><td>${s.hits}</td><td>${s.confirmed_moves}</td><td>${s.false_alarms}</td></tr>`
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
        setTimeout(done, 90000);
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
            $("#voiceStatus").textContent = "Hearing you… pause when done";
            updateMicUi();
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

        if (spokenLongEnough && silenceDone) {
          clearVad();
          finishTurn();
          return;
        }

        if (
          state.speakingHeard &&
          spokenLongEnough &&
          now - turnStartedAt >= VAD.maxTurnMs
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
        body: JSON.stringify({ symbol: f.symbol.value, market: f.market.value }),
      });
      f.reset();
      toast("Watch added");
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
        }),
      });
      toast("Movers settings saved");
      loadMovers();
    } catch (err) {
      toast(err.message);
    }
  });

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
  // Soft auto-refresh: health only by default. Positions never full-wipe on timer.
  const HEALTH_MS = 60000;
  const OVERVIEW_SOFT_MS = 180000; // 3 min overview only
  let softTimer = null;
  let healthTimer = null;

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

  async function softRefreshView() {
    if (document.hidden || state.inCall || state.busy) return;
    // Positions: soft patch only (skip if interacting / unchanged)
    if (state.view === "positions") {
      await loadPositions({ soft: true });
      return;
    }
    // Overview can soft-reload; other tabs are manual / nav only
    if (state.view === "overview") {
      try {
        await loadOverview();
      } catch (e) {
        console.error(e);
      }
    }
  }

  function scheduleSoftRefresh() {
    if (softTimer) clearInterval(softTimer);
    if (healthTimer) clearInterval(healthTimer);
    softTimer = setInterval(softRefreshView, OVERVIEW_SOFT_MS);
    healthTimer = setInterval(refreshHealthOnly, HEALTH_MS);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshHealthOnly();
  });

  setView("overview");
  updateMicUi();
  refreshAll();
  scheduleSoftRefresh();
})();
