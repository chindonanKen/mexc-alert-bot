/* AD Desk v2.1 — HTTPS-first voice + full desk control */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  const state = {
    token: localStorage.getItem("desk_token") || "",
    view: "overview",
    chunks: [],
    recording: false,
    continuous: localStorage.getItem("desk_voice_cont") !== "0",
    agentHistory: [],
    busy: false,
  };

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
    if (Array.isArray(hist)) state.agentHistory = hist.slice(-24);
  }

  async function runAgentText(text) {
    if (state.busy) return;
    state.busy = true;
    agentMsg("user", text);
    $("#voiceStatus").textContent = "Agent thinking…";
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
      }
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
      $("#voiceStatus").textContent = state.continuous
        ? "Listening continuum — tap Stop when done"
        : "Ready";
      refreshAll();
    } catch (e) {
      agentMsg("bot", "Error: " + e.message);
      $("#voiceStatus").textContent = "Error";
    } finally {
      state.busy = false;
    }
  }

  // ---- Mic: localhost OK; convert to WAV client-side (no local ffmpeg needed) ----
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
    const cont = $("#btnCont");
    if (cont) {
      cont.classList.toggle("on", state.continuous);
      cont.textContent = state.continuous
        ? "Continuous: ON"
        : "Continuous: OFF";
    }
    if (!box || !micBtn) return;
    const secure = isSecureForMic();
    if (!secure) {
      box.hidden = false;
      box.innerHTML =
        "<strong>Mic needs HTTPS</strong> on plain http://IP.<br/>" +
        "Local: use <code>http://127.0.0.1:8080</code>. Droplet: " +
        "<code>https://IP/</code> after desk_https_up.sh.";
      micBtn.disabled = true;
      micBtn.textContent = "Need HTTPS";
      $("#voiceStatus").textContent = "Text agent works · voice needs secure context";
    } else if (!micSupported()) {
      box.hidden = false;
      box.innerHTML = "This browser cannot record audio. Try Chrome or Safari.";
      micBtn.disabled = true;
      micBtn.textContent = "Mic N/A";
    } else {
      box.hidden = true;
      micBtn.disabled = state.busy && !state.recording;
      if (state.recording) {
        micBtn.textContent = "Stop · send";
        $("#voiceStatus").textContent = "Recording… tap to send turn";
      } else if (state.busy) {
        micBtn.textContent = "Working…";
        $("#voiceStatus").textContent = "Transcribing + tools…";
      } else {
        micBtn.textContent = state.continuous
          ? "Talk (continuous)"
          : "Tap to record";
        $("#voiceStatus").textContent = state.continuous
          ? "Continuous conversation — tap to speak each turn"
          : "Ready — tap mic, speak, tap again to send";
      }
    }
  }

  /** Decode MediaRecorder blob → 16 kHz mono PCM WAV (works without ffmpeg). */
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
    const targetRate = 16000;
    const duration = decoded.duration;
    const offline = new OfflineAudioContext(
      1,
      Math.max(1, Math.ceil(duration * targetRate)),
      targetRate
    );
    const src = offline.createBufferSource();
    // mixdown to mono
    const mono = offline.createBuffer(1, decoded.length, decoded.sampleRate);
    const ch0 = mono.getChannelData(0);
    const nCh = decoded.numberOfChannels;
    for (let i = 0; i < decoded.length; i++) {
      let s = 0;
      for (let c = 0; c < nCh; c++) s += decoded.getChannelData(c)[i];
      ch0[i] = s / nCh;
    }
    src.buffer = mono;
    src.connect(offline.destination);
    src.start(0);
    const rendered = await offline.startRendering();
    await ctx.close().catch(() => {});
    const pcm = rendered.getChannelData(0);
    const samples = pcm.length;
    const buf = new ArrayBuffer(44 + samples * 2);
    const view = new DataView(buf);
    const wstr = (o, s) => {
      for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i));
    };
    wstr(0, "RIFF");
    view.setUint32(4, 36 + samples * 2, true);
    wstr(8, "WAVE");
    wstr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, targetRate, true);
    view.setUint32(28, targetRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    wstr(36, "data");
    view.setUint32(40, samples * 2, true);
    let off = 44;
    for (let i = 0; i < samples; i++) {
      let x = Math.max(-1, Math.min(1, pcm[i]));
      view.setInt16(off, x < 0 ? x * 0x8000 : x * 0x7fff, true);
      off += 2;
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  async function playTtsAndMaybeContinue(audioB64) {
    const audio = $("#voiceAudio");
    if (!audioB64 || !audio) {
      if (state.continuous && !state.busy) setTimeout(() => startRec(), 400);
      return;
    }
    audio.src = "data:audio/mpeg;base64," + audioB64;
    audio.hidden = false;
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
        // safety if play hangs
        setTimeout(done, 60000);
      });
    } catch (_) {
      /* autoplay blocked — still continue */
    }
    if (state.continuous && !state.busy && !state.recording) {
      setTimeout(() => startRec(), 350);
    }
  }

  async function sendAudioBlob(blob) {
    state.busy = true;
    updateMicUi();
    $("#voiceStatus").textContent = "Encoding WAV + STT + tools…";
    let wavBlob = blob;
    let fname = "voice.wav";
    try {
      if (!blob.type.includes("wav")) {
        wavBlob = await blobToWav16k(blob);
      }
    } catch (e) {
      // fall back to original; server may convert if ffmpeg present
      fname = blob.type.includes("mp4")
        ? "voice.mp4"
        : blob.type.includes("ogg")
          ? "voice.ogg"
          : "voice.webm";
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
        const j = JSON.parse(msg);
        msg = j.detail || msg;
      } catch (_) {}
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
    agentMsg("bot", out.reply || "—");
    refreshAll();
    state.busy = false;
    updateMicUi();
    await playTtsAndMaybeContinue(out.audio_b64);
    updateMicUi();
  }

  let recorder = null;
  let mediaStream = null;

  async function startRec() {
    if (state.recording || state.busy) return;
    if (!isSecureForMic() || !micSupported()) {
      updateMicUi();
      toast("Mic needs secure context (localhost or HTTPS)");
      return;
    }
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
      });
      state.chunks = [];
      const mimeCandidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
        "audio/ogg;codecs=opus",
        "audio/ogg",
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
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size) state.chunks.push(e.data);
      };
      recorder.onerror = () => {
        toast("Recorder error");
        stopRec();
      };
      recorder.onstop = async () => {
        try {
          if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
          mediaStream = null;
          const type = recorder?.mimeType || mime || "audio/webm";
          const blob = new Blob(state.chunks, { type });
          if (!blob.size) {
            toast("Empty recording — try again");
            return;
          }
          await sendAudioBlob(blob);
        } catch (e) {
          $("#voiceStatus").textContent = "Voice failed";
          toast(String(e.message || e).slice(0, 160));
          state.busy = false;
        } finally {
          state.recording = false;
          $("#btnMic").classList.remove("rec");
          updateMicUi();
        }
      };
      recorder.start(250);
      state.recording = true;
      $("#btnMic").classList.add("rec");
      updateMicUi();
    } catch (e) {
      const name = e && e.name;
      let msg = "Mic failed";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        msg = "Allow microphone for this site in browser settings";
      } else if (name === "NotFoundError") {
        msg = "No microphone found";
      } else if (!window.isSecureContext) {
        msg = "Need HTTPS or localhost";
      }
      toast(msg);
      $("#voiceStatus").textContent = msg;
      updateMicUi();
    }
  }

  function stopRec() {
    if (recorder && state.recording) {
      try {
        if (recorder.state !== "inactive") recorder.stop();
      } catch (_) {
        state.recording = false;
        updateMicUi();
      }
    }
  }

  const mic = $("#btnMic");
  if (mic) {
    mic.addEventListener("click", () => {
      if (state.recording) stopRec();
      else startRec();
    });
  }

  const contBtn = $("#btnCont");
  if (contBtn) {
    contBtn.addEventListener("click", () => {
      state.continuous = !state.continuous;
      localStorage.setItem("desk_voice_cont", state.continuous ? "1" : "0");
      updateMicUi();
      toast(
        state.continuous
          ? "Continuous conversation ON — after each reply, mic re-opens"
          : "Continuous OFF — one shot per tap"
      );
    });
  }

  const clearHist = $("#btnClearHist");
  if (clearHist) {
    clearHist.addEventListener("click", () => {
      state.agentHistory = [];
      const log = $("#agentLog");
      if (log) log.innerHTML = "";
      agentMsg("bot", "Conversation cleared — new continuous session.");
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
