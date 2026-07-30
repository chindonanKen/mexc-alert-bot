/* AD Desk v2 beta client */
(function () {
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  const state = {
    token: localStorage.getItem("desk_token") || "",
    view: "overview",
  };

  // Allow ?token= for first login
  const params = new URLSearchParams(location.search);
  if (params.get("token")) {
    state.token = params.get("token");
    localStorage.setItem("desk_token", state.token);
    history.replaceState({}, "", location.pathname);
  }

  function headers() {
    const h = { "Content-Type": "application/json" };
    if (state.token) h["X-Desk-Token"] = state.token;
    return h;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { ...headers(), ...(opts.headers || {}) },
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
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.hidden = true;
    }, 2800);
  }

  function fmtPx(n) {
    if (n == null || Number.isNaN(n)) return "—";
    const x = Number(n);
    if (x >= 1000) return x.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (x >= 1) return x.toFixed(4);
    return x.toPrecision(4);
  }

  function fmtChg(n) {
    if (n == null) return "—";
    const x = Number(n);
    const s = (x >= 0 ? "+" : "") + x.toFixed(2) + "%";
    return s;
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function table(headers, rowsHtml) {
    if (!rowsHtml) return '<div class="empty">No data yet — run the bot so SQLite fills.</div>';
    return `<table class="data"><thead><tr>${headers
      .map((h) => `<th>${h}</th>`)
      .join("")}</tr></thead><tbody>${rowsHtml}</tbody></table>`;
  }

  function setView(name) {
    state.view = name;
    $$(".view").forEach((v) => v.classList.remove("active"));
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    const view = $(`#view-${name}`);
    if (view) view.classList.add("active");
    const titles = {
      overview: ["Overview", "Market pulse for AD scale-ins"],
      tape: ["Tape & Heat", "Watchlist + live marks"],
      alerts: ["Targets", "One-shot price alerts"],
      memory: ["Memory", "Fires, labels, learning loop"],
      intel: ["Intel", "Fatal news · delist radar · source expertise"],
      agent: ["Agent", "Coach + session brief"],
      playbook: ["Playbook", "Your AD strategy, encoded"],
    };
    const t = titles[name] || ["Desk", ""];
    $("#viewTitle").textContent = t[0];
    $("#viewSub").textContent = t[1];
  }

  function renderMajors(majors) {
    const el = $("#majors");
    if (!majors || !majors.length) {
      el.innerHTML = '<div class="major"><span class="sym">—</span></div>';
      return;
    }
    el.innerHTML = majors
      .map((m) => {
        const up = Number(m.changePercent) >= 0;
        const sym = m.symbol.replace("USDT", "");
        return `<div class="major"><span class="sym">${sym}</span><span class="px">${fmtPx(
          m.price
        )}</span><span class="${up ? "up" : "dn"}">${fmtChg(m.changePercent)}</span></div>`;
      })
      .join("");
  }

  async function loadOverview() {
    const d = await api("/api/overview");
    renderMajors(d.market?.majors || []);
    $("#regimeValue").textContent = d.pulse?.regime || d.market?.regime || "—";
    $("#regimeBias").textContent = d.pulse?.ad_bias || "";
    $("#ruleText").textContent = d.pulse?.rule || "";
    const c = d.counts || {};
    $("#counters").innerHTML = [
      ["Targets", c.alerts_enabled],
      ["Watchlist", c.watchlist],
      ["Events", c.events],
      ["Investigations", c.investigations],
      ["News", c.news],
    ]
      .map(
        ([k, v]) =>
          `<div class="stat"><div class="k">${k}</div><div class="v">${v ?? 0}</div></div>`
      )
      .join("");

    const evRows = (d.recent_events || [])
      .map((e) => {
        const band = e.velocity_band || "—";
        const drop = e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : "—";
        return `<tr>
          <td>#${e.id}</td>
          <td>${e.symbol || ""}</td>
          <td class="dn">${drop}</td>
          <td class="band-${band}">${band}</td>
          <td>${e.mode || e.source || ""}</td>
          <td>${fmtTime(e.ts)}</td>
        </tr>`;
      })
      .join("");
    $("#ovEvents").innerHTML = table(
      ["ID", "Symbol", "Drop", "Band", "Mode", "When"],
      evRows
    );

    const invRows = (d.recent_investigations || [])
      .map((i) => {
        const conf = i.confidence != null ? Math.round(i.confidence * 100) + "%" : "—";
        return `<tr>
          <td>#${i.id}</td>
          <td>${i.symbol || ""}</td>
          <td class="dn">${i.drop_pct != null ? Number(i.drop_pct).toFixed(1) + "%" : "—"}</td>
          <td>${i.verdict || ""}</td>
          <td>${conf}</td>
          <td>${fmtTime(i.ts)}</td>
        </tr>`;
      })
      .join("");
    $("#ovInv").innerHTML = table(
      ["ID", "Symbol", "Drop", "Verdict", "Conf", "When"],
      invRows
    );
  }

  async function loadTape() {
    const d = await api("/api/watchlist");
    const s = d.settings;
    $("#mwSettings").textContent = s
      ? `thr ${s.threshold_percent}% · ${Math.round((s.lookback_seconds || 0) / 60)}m · ${
          s.enabled ? "ON" : "OFF"
        }`
      : "movers";
    const bySym = {};
    (d.tickers || []).forEach((t) => {
      bySym[t.symbol] = t;
    });
    const rows = (d.watchlist || [])
      .map((w) => {
        const key = String(w.symbol || "")
          .toUpperCase()
          .replace(/_/g, "");
        const t = bySym[key] || bySym[key + "USDT"];
        const chg = t ? Number(t.changePercent) : null;
        return `<tr>
          <td>${w.market === "futures" ? "F" : "S"}</td>
          <td>${w.symbol}</td>
          <td>${t ? fmtPx(t.price) : "—"}</td>
          <td class="${chg != null && chg < 0 ? "dn" : "up"}">${fmtChg(chg)}</td>
        </tr>`;
      })
      .join("");
    $("#tapeTable").innerHTML = table(["Mkt", "Symbol", "Mark", "24h"], rows);
  }

  async function loadAlerts() {
    const d = await api("/api/alerts");
    const rows = (d.alerts || [])
      .map(
        (a) => `<tr>
        <td>#${a.visual_id || a.id}</td>
        <td>${a.market === "futures" ? "F" : "S"}</td>
        <td>${a.symbol}</td>
        <td>${fmtPx(a.price)}</td>
        <td>${a.enabled ? "on" : "off"}</td>
      </tr>`
      )
      .join("");
    $("#alertsTable").innerHTML = table(["#", "Mkt", "Symbol", "Target", ""], rows);
  }

  async function loadMemory() {
    const d = await api("/api/events?limit=50");
    const rows = (d.events || [])
      .map((e) => {
        const band = e.velocity_band || "—";
        return `<tr>
          <td>#${e.id}</td>
          <td>${e.symbol}</td>
          <td class="dn">${e.drop_pct != null ? Number(e.drop_pct).toFixed(1) + "%" : "—"}</td>
          <td class="band-${band}">${band}</td>
          <td>${e.last_action || "unlabeled"}</td>
          <td>${e.last_bounce || "—"}</td>
          <td>${fmtTime(e.ts)}</td>
        </tr>`;
      })
      .join("");
    $("#memoryTable").innerHTML = table(
      ["ID", "Symbol", "Drop", "Band", "Action", "Bounce", "When"],
      rows
    );
  }

  async function loadIntel() {
    const [news, inv] = await Promise.all([
      api("/api/news"),
      api("/api/investigations"),
    ]);
    const nRows = (news.news || [])
      .map(
        (n) => `<tr>
        <td>${n.class || n.severity || ""}</td>
        <td>${n.symbol || "—"}</td>
        <td>${(n.title || "").slice(0, 80)}</td>
        <td>${n.source || ""}</td>
        <td>${fmtTime(n.ts)}</td>
      </tr>`
      )
      .join("");
    $("#newsTable").innerHTML = table(
      ["Class", "Sym", "Title", "Source", "When"],
      nRows
    );

    const dRows = (news.delist_cache || [])
      .map(
        (d) => `<tr>
        <td>${d.exchange || ""}</td>
        <td>${d.base || "—"}</td>
        <td>${d.kind || ""}</td>
        <td>${(d.title || "").slice(0, 70)}</td>
        <td>${fmtTime(d.ts)}</td>
      </tr>`
      )
      .join("");
    $("#delistTable").innerHTML = table(
      ["CEX", "Base", "Kind", "Title", "When"],
      dRows
    );

    const sRows = (inv.sources || [])
      .map(
        (s) => `<tr>
        <td>${s.source}</td>
        <td>${s.kind}</td>
        <td>${Number(s.weight).toFixed(2)}</td>
        <td>${s.hits}</td>
        <td>${s.confirmed_moves}</td>
        <td>${s.false_alarms}</td>
      </tr>`
      )
      .join("");
    $("#sourcesTable").innerHTML = table(
      ["Source", "Kind", "Weight", "Hits", "Confirmed", "False"],
      sRows
    );
  }

  async function loadPlaybook() {
    const d = await api("/api/strategy");
    $("#coreSentence").textContent = d.core || "";
    $("#workflowLine").textContent = "Workflow: " + (d.workflow || "");
    $("#preferList").innerHTML = (d.prefer || []).map((x) => `<li>${x}</li>`).join("");
    $("#avoidList").innerHTML = (d.avoid || []).map((x) => `<li>${x}</li>`).join("");
    const mods = d.modules || {};
    $("#moduleGrid").innerHTML = Object.entries(mods)
      .map(
        ([k, v]) =>
          `<div class="module"><div class="t">${k}</div><div class="d">${v}</div></div>`
      )
      .join("");
  }

  function appendChat(role, text) {
    const log = $("#chatLog");
    const div = document.createElement("div");
    div.className = "bubble " + role;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  async function askCoach(q) {
    appendChat("user", q);
    try {
      const d = await api("/api/coach", {
        method: "POST",
        body: JSON.stringify({ message: q }),
      });
      appendChat("bot", d.reply || "—");
      if (q.toLowerCase().includes("brief") || q.toLowerCase() === "brief") {
        $("#briefBox").textContent = d.reply || "";
      }
    } catch (e) {
      appendChat("bot", "Error: " + e.message);
    }
  }

  async function refreshAll() {
    try {
      const h = await api("/api/health");
      const st = $("#connStatus");
      st.textContent = h.db_exists ? "live · db ok" : "live · empty db";
      st.className = "status-pill ok";
    } catch (e) {
      const st = $("#connStatus");
      st.textContent = "offline";
      st.className = "status-pill err";
    }
    const loaders = {
      overview: loadOverview,
      tape: loadTape,
      alerts: loadAlerts,
      memory: loadMemory,
      intel: loadIntel,
      playbook: loadPlaybook,
      agent: async () => {
        await loadOverview();
      },
    };
    try {
      await (loaders[state.view] || loadOverview)();
      // always refresh majors via prices
      const p = await api("/api/prices");
      renderMajors(p.context?.majors || p.tickers || []);
    } catch (e) {
      console.error(e);
      toast(String(e.message || e));
    }
  }

  // nav
  $$(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      setView(btn.dataset.view);
      refreshAll();
    });
  });

  $("#btnRefresh").addEventListener("click", () => refreshAll());

  $$("[data-label]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const r = await api("/api/events/label", {
          method: "POST",
          body: JSON.stringify({ action: btn.dataset.label }),
        });
        toast(`Labeled event #${r.event_id} → ${btn.dataset.label}`);
        loadMemory();
      } catch (e) {
        toast(e.message);
      }
    });
  });

  $("#chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#chatInput");
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    askCoach(q);
  });

  $$(".chip-btn").forEach((b) => {
    b.addEventListener("click", () => askCoach(b.dataset.q));
  });

  setView("overview");
  refreshAll();
  setInterval(refreshAll, 45000);
})();
