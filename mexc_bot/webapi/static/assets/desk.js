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
    speakRms: 0.045, // higher — room noise should not count as speech
    silenceRms: 0.02,
    endSilenceMs: 900, // pause after you finish talking
    minSpeechMs: 700, // must talk this long before we consider a turn
    speechHoldMs: 280, // need continuous speech this long to arm "heard you"
    maxTurnMs: 45000, // safety cap only (never sends without real speech)
    pollMs: 70,
    postReplyCooldownMs: 900, // ignore mic right after Grok speaks (echo)
    emptyBackoffMs: 1200,
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
    const meta = {
      overview: ["Overview", "Regime · fires · positions"],
      positions: ["Positions", "Journal of open AD trades"],
      tape: ["Tape", "Watchlist · live marks · mover settings"],
      targets: ["Targets", "Add · edit · delete one-shot alerts"],
      memory: ["Memory", "Fires + labels"],
      intel: ["Intel", "News · delist radar · source weights"],
      voice: ["Voice Agent", "Mic on HTTPS · Grok tools control the desk"],
      roadmap: ["Roadmap", "Where the platform is going"],
      playbook: ["Playbook", "AD strategy encoded"],
    }[name] || ["Desk", ""];
    $("#title").textContent = meta[0];
    $("#subtitle").textContent = meta[1];
  }

  function renderMajors(majors) {
    const el = $("#majors");
    if (!majors?.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = majors
      .map((m) => {
        const up = Number(m.changePercent) >= 0;
        return `<div class="major"><span>${m.symbol.replace(
          "USDT",
          ""
        )}</span> <b>${fmtPx(m.price)}</b> <span class="${up ? "up" : "dn"}">${fmtChg(
          m.changePercent
        )}</span></div>`;
      })
      .join("");
  }

  async function loadOverview() {
    const d = await api("/api/overview");
    renderMajors(d.market?.majors || []);
    $("#regimeValue").textContent = d.pulse?.regime || "—";
    $("#regimeBias").textContent = d.pulse?.ad_bias || "";
    const c = d.counts || {};
    $("#counters").innerHTML = [
      ["Targets", c.alerts_enabled],
      ["Watch", c.watchlist],
      ["Events", c.events],
      ["Open", c.open_positions],
      ["Intel", c.investigations],
      ["News", c.news],
    ]
      .map(
        ([k, v]) =>
          `<div class="metric"><span>${k}</span><b>${v ?? 0}</b></div>`
      )
      .join("");
    const pos = d.positions || [];
    $("#ovPos").innerHTML = pos.length
      ? pos
          .map(
            (p) =>
              `<div>#${p.id} ${p.symbol} ${p.market} @ ${
                p.entry_avg != null ? fmtPx(p.entry_avg) : "—"
              }</div>`
          )
          .join("")
      : "<div>No open journal trades</div>";

    $("#ovEvents").innerHTML = table(
      ["ID", "Sym", "Drop", "Band", "When"],
      (d.recent_events || [])
        .map(
          (e) => `<tr>
          <td>#${e.id}</td><td>${e.symbol}</td>
          <td class="dn">${e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : "—"}</td>
          <td>${e.velocity_band || "—"}</td><td>${fmtTime(e.ts)}</td></tr>`
        )
        .join("")
    );
    $("#ovInv").innerHTML = table(
      ["ID", "Sym", "Verdict", "Conf", "When"],
      (d.recent_investigations || [])
        .map(
          (i) => `<tr>
          <td>#${i.id}</td><td>${i.symbol}</td><td>${i.verdict}</td>
          <td>${i.confidence != null ? Math.round(i.confidence * 100) + "%" : "—"}</td>
          <td>${fmtTime(i.ts)}</td></tr>`
        )
        .join("")
    );
  }

  async function loadPositions() {
    const d = await api("/api/positions");
    $("#posMode").textContent = d.mode || "journal";
    const rows = (d.positions || [])
      .map(
        (p) => `<tr>
        <td>#${p.id}</td>
        <td>${p.market?.[0]?.toUpperCase() || "?"}</td>
        <td>${p.symbol}</td>
        <td>${p.entry_avg != null ? fmtPx(p.entry_avg) : "—"}</td>
        <td>${(p.notes || "").slice(0, 40)}</td>
        <td>${fmtTime(p.opened_at)}</td>
        <td><button type="button" class="btn soft sm" data-close="${p.id}">Close</button></td>
      </tr>`
      )
      .join("");
    $("#posTable").innerHTML = table(
      ["ID", "M", "Symbol", "Entry", "Notes", "Opened", ""],
      rows
    );
    $$("[data-close]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api("/api/positions/close", {
            method: "POST",
            body: JSON.stringify({ trade_id: +b.dataset.close }),
          });
          toast("Position closed");
          loadPositions();
        } catch (e) {
          toast(e.message);
        }
      })
    );
  }

  async function loadTape() {
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
    $("#tapeTable").innerHTML = table(["M", "Symbol", "Mark", "24h", ""], rows);
    $$("[data-unwatch]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api(
            `/api/watchlist?symbol=${encodeURIComponent(b.dataset.unwatch)}&market=${b.dataset.m}`,
            { method: "DELETE" }
          );
          toast("Removed from watchlist");
          loadTape();
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

  async function loadMemory() {
    const d = await api("/api/events?limit=60");
    const rows = (d.events || [])
      .map(
        (e) => `<tr>
        <td>#${e.id}</td><td>${e.symbol}</td>
        <td class="dn">${e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : "—"}</td>
        <td>${e.velocity_band || "—"}</td>
        <td>${e.last_action || "unlabeled"}</td>
        <td>${fmtTime(e.ts)}</td>
        <td>
          <button type="button" class="btn soft sm" data-eid="${e.id}" data-a="took">Took</button>
          <button type="button" class="btn soft sm" data-eid="${e.id}" data-a="skip">Skip</button>
        </td>
      </tr>`
      )
      .join("");
    $("#memoryTable").innerHTML = table(
      ["ID", "Sym", "Drop", "Band", "Label", "When", ""],
      rows
    );
    $$("[data-eid]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          await api("/api/events/label", {
            method: "POST",
            body: JSON.stringify({ event_id: +b.dataset.eid, action: b.dataset.a }),
          });
          toast("Labeled " + b.dataset.a);
          loadMemory();
        } catch (e) {
          toast(e.message);
        }
      })
    );
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
    const railPill = $("#callRailPill");
    const railCall = $("#btnRailCall");
    const topState = $("#topVoiceState");

    if (dock) {
      dock.classList.toggle("in-call", state.inCall);
      dock.classList.toggle("busy", state.busy);
    }
    if (endBtn) endBtn.hidden = !state.inCall;
    if (muteMic) {
      muteMic.hidden = !state.inCall;
      muteMic.textContent = state.muteMic ? "Mic muted" : "Mic on";
      muteMic.classList.toggle("muted-on", state.muteMic);
    }
    if (muteSpk) {
      muteSpk.hidden = !state.inCall;
      muteSpk.textContent = state.muteSpk ? "Speaker muted" : "Speaker on";
      muteSpk.classList.toggle("muted-on", state.muteSpk);
    }
    if (level) level.hidden = !(state.inCall && state.recording && !state.muteMic);

    let statusLine = "Ready — Start call to talk";
    let pillText = "voice idle";
    let mainLabel = "Start call";
    let mainDisabled = false;

    const secure = isSecureForMic();
    if (!secure) {
      if (box) {
        box.hidden = false;
        box.innerHTML =
          "<strong>Mic needs HTTPS</strong> on plain http://IP.<br/>" +
          "Local: <code>http://127.0.0.1:8080</code>. Droplet: <code>https://IP/</code>.";
      }
      mainLabel = "Need HTTPS";
      mainDisabled = true;
      statusLine = "Voice needs secure context";
      pillText = "voice blocked";
    } else if (!micSupported()) {
      if (box) {
        box.hidden = false;
        box.innerHTML = "This browser cannot record audio. Try Chrome or Safari.";
      }
      mainLabel = "Mic N/A";
      mainDisabled = true;
      statusLine = "Mic not available";
      pillText = "no mic";
    } else {
      if (box) box.hidden = true;
      if (!state.inCall) {
        mainLabel = "Start call";
        mainDisabled = false;
        statusLine = "Ready — Start call anytime (any page)";
        pillText = "voice idle";
      } else if (state.busy) {
        mainLabel = "Grok…";
        mainDisabled = true;
        statusLine = "Working · STT + tools + speaking";
        pillText = "call · busy";
      } else if (state.muteMic) {
        mainLabel = "In call";
        mainDisabled = true;
        statusLine = "Call live · mic muted";
        pillText = "call · muted";
      } else if (state.recording) {
        mainLabel = state.speakingHeard ? "Hearing you" : "Idle";
        mainDisabled = true;
        statusLine = state.speakingHeard
          ? "Hearing you — pause when finished"
          : "Idle — listening for your voice";
        pillText = state.speakingHeard ? "call · speak" : "call · idle";
      } else {
        mainLabel = "In call";
        mainDisabled = true;
        statusLine = "Call live";
        pillText = "call · live";
      }
    }

    if (micBtn) {
      micBtn.disabled = mainDisabled;
      micBtn.textContent = mainLabel;
      micBtn.classList.toggle("rec", state.recording && state.inCall);
    }
    const vs = $("#voiceStatus");
    if (vs) vs.textContent = statusLine;
    if (railPill) {
      railPill.textContent = pillText;
      railPill.classList.toggle("live", state.inCall);
    }
    if (railCall) {
      railCall.textContent = state.inCall ? "End call" : "Start call";
      railCall.classList.toggle("danger", state.inCall);
      railCall.disabled = !secure || !micSupported();
    }
    if (topState) {
      topState.textContent = state.inCall
        ? state.muteMic
          ? "Voice · mic muted"
          : state.busy
            ? "Voice · Grok busy"
            : "Voice · live"
        : "Voice idle";
      topState.classList.toggle("live", state.inCall);
    }
  }

  /**
   * Fast path: decode mic blob → mono PCM WAV at native rate (no Offline resample).
   * xAI STT accepts common rates; skipping 16k resample cuts hundreds of ms.
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
    const samples = decoded.length;
    // Downsample in one pass if > 24 kHz (cheap every-N pick, not full resampler)
    const stride = rate > 24000 ? Math.max(1, Math.round(rate / 16000)) : 1;
    const outRate = Math.round(rate / stride);
    const outLen = Math.floor(samples / stride);
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
    for (let i = 0; i < outLen; i++) {
      const idx = i * stride;
      let s = 0;
      for (let c = 0; c < nCh; c++) s += decoded.getChannelData(c)[idx];
      s /= nCh;
      s = Math.max(-1, Math.min(1, s));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      off += 2;
    }
    await ctx.close().catch(() => {});
    return new Blob([buf], { type: "audio/wav" });
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
    if (!blob || blob.size < 2500) {
      $("#voiceStatus").textContent = "Idle — listening for your voice";
      await resumeListening(VAD.emptyBackoffMs);
      return;
    }
    state.busy = true;
    updateMicUi();
    $("#voiceStatus").textContent = "Grok is thinking…";
    let wavBlob = blob;
    let fname = "voice.wav";
    try {
      if (!blob.type.includes("wav")) wavBlob = await blobToWav16k(blob);
    } catch (e) {
      fname = "voice.webm";
      wavBlob = blob;
      console.warn("client WAV convert failed", e);
    }
    const fd = new FormData();
    fd.append("file", wavBlob, fname);
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
      if (isEmptySpeechError(msg)) {
        state.busy = false;
        $("#voiceStatus").textContent = "Idle — listening (no clear speech)";
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

  let recorder = null;
  let mediaStream = null;
  let audioCtx = null;
  let analyser = null;
  let vadTimer = null;
  let turnStartedAt = 0;
  let speechStartedAt = 0;

  function clearVad() {
    if (vadTimer) {
      clearInterval(vadTimer);
      vadTimer = null;
    }
    setLevelBar(0);
  }

  function stopListenOnly() {
    clearVad();
    if (recorder && state.recording) {
      try {
        recorder.onstop = null;
        if (recorder.state !== "inactive") recorder.stop();
      } catch (_) {}
    }
    state.recording = false;
    if (mediaStream) {
      mediaStream.getTracks().forEach((t) => t.stop());
      mediaStream = null;
    }
    if (audioCtx) {
      audioCtx.close().catch(() => {});
      audioCtx = null;
      analyser = null;
    }
    recorder = null;
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
      const source = audioCtx.createMediaStreamSource(mediaStream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      state.chunks = [];
      state.speakingHeard = false;
      state.silenceMs = 0;
      turnStartedAt = Date.now();
      speechStartedAt = 0;
      let speechArmedMs = 0; // continuous loud frames
      let speechTotalMs = 0; // cumulative loud frames this arm

      const mimeCandidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ];
      let mime = "";
      for (const c of mimeCandidates) {
        if (MediaRecorder.isTypeSupported(c)) {
          mime = c;
          break;
        }
      }
      recorder = mime
        ? new MediaRecorder(mediaStream, { mimeType: mime })
        : new MediaRecorder(mediaStream);

      // Only keep audio after speech is armed — cuts empty uploads
      let captureArmed = false;
      recorder.ondataavailable = (e) => {
        if (!e.data || !e.data.size) return;
        if (captureArmed || state.speakingHeard) state.chunks.push(e.data);
      };

      const teardownCapture = () => {
        clearVad();
        try {
          if (recorder && recorder.state !== "inactive") {
            recorder.onstop = null;
            recorder.stop();
          }
        } catch (_) {}
        if (mediaStream) {
          mediaStream.getTracks().forEach((t) => t.stop());
          mediaStream = null;
        }
        if (audioCtx) {
          audioCtx.close().catch(() => {});
          audioCtx = null;
          analyser = null;
        }
        recorder = null;
        state.recording = false;
        $("#btnMic")?.classList.remove("rec");
      };

      const finishTurn = async (reason) => {
        const type = recorder?.mimeType || mime || "audio/webm";
        const blob = new Blob(state.chunks, { type });
        const heard = state.speakingHeard && speechTotalMs >= VAD.minSpeechMs;
        teardownCapture();
        if (!state.inCall) return;

        // Idle / no real speech → quietly keep listening (not pushy)
        if (!heard || blob.size < 2500) {
          $("#voiceStatus").textContent = "Idle — listening for your voice";
          updateMicUi();
          await resumeListening(350);
          return;
        }
        try {
          await sendAudioBlob(blob);
        } catch (e) {
          const msg = String(e.message || e);
          state.busy = false;
          if (isEmptySpeechError(msg)) {
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

      recorder.onerror = () => {
        toast("Recorder error");
        endCall();
      };

      recorder.start(200);
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
          // Arm only after sustained speech (not a cough/click)
          if (!state.speakingHeard && speechArmedMs >= VAD.speechHoldMs) {
            state.speakingHeard = true;
            captureArmed = true;
            speechStartedAt = now - speechArmedMs;
            // Include a bit of pre-roll by starting to keep chunks now
            $("#voiceStatus").textContent = "Hearing you… pause when done";
            updateMicUi();
          }
        } else {
          speechArmedMs = 0;
          if (state.speakingHeard) {
            state.silenceMs += VAD.pollMs;
          } else {
            // Truly idle — drop buffered noise so we never upload it
            if (state.chunks.length > 40) state.chunks = state.chunks.slice(-8);
          }
        }

        const spokenLongEnough =
          state.speakingHeard && speechTotalMs >= VAD.minSpeechMs;
        const silenceDone = state.silenceMs >= VAD.endSilenceMs;

        // Only finish when user spoke enough AND then paused.
        // Never send on max-time alone (that was the "pushy" loop).
        if (spokenLongEnough && silenceDone) {
          clearVad();
          try {
            if (recorder && recorder.state !== "inactive") {
              recorder.onstop = () => finishTurn("vad");
              recorder.stop();
            } else {
              finishTurn("vad");
            }
          } catch (_) {
            finishTurn("vad");
          }
          return;
        }

        // Safety: if armed forever without silence, force after maxTurn
        if (
          state.speakingHeard &&
          spokenLongEnough &&
          now - turnStartedAt >= VAD.maxTurnMs
        ) {
          clearVad();
          try {
            if (recorder && recorder.state !== "inactive") {
              recorder.onstop = () => finishTurn("max");
              recorder.stop();
            } else finishTurn("max");
          } catch (_) {
            finishTurn("max");
          }
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

  const railCall = $("#btnRailCall");
  if (railCall) {
    railCall.addEventListener("click", () => {
      if (state.inCall) endCall();
      else startCall();
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

  const quick = $("#voiceQuick");
  if (quick) {
    quick.addEventListener("click", (e) => {
      const t = e.target;
      if (!(t instanceof HTMLElement)) return;
      const msg = t.getAttribute("data-agent");
      if (msg) runAgentText(msg);
    });
  }

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
      loadTape();
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
      loadTape();
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
      positions: loadPositions,
      tape: loadTape,
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
      const p = await api("/api/prices");
      renderMajors(p.context?.majors || p.tickers || []);
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

  setView("overview");
  updateMicUi();
  refreshAll();
  setInterval(refreshAll, 40000);
})();
