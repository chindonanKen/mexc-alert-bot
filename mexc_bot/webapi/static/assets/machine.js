/* Slate Machine room. Numbers only — no chart, no waveform. */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const state = {
    token: localStorage.getItem("desk_token") || "",
    plans: [],
    needs: [],
    room: {},
    account: {},
    selected: null,
  };

  const qp = new URLSearchParams(location.search);
  if (qp.get("token")) {
    state.token = qp.get("token");
    localStorage.setItem("desk_token", state.token);
    history.replaceState({}, "", location.pathname);
  }

  function headers() {
    const h = { "Content-Type": "application/json" };
    if (state.token) h["X-Desk-Token"] = state.token;
    return h;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { ...headers(), ...(opts.headers || {}) } });
    if (res.status === 401) {
      const t = prompt("Desk API token:");
      if (t) {
        state.token = t;
        localStorage.setItem("desk_token", t);
        return api(path, opts);
      }
      throw new Error("Unauthorized");
    }
    if (res.status === 404) throw new Error("Machine is off");
    if (!res.ok) {
      let msg = await res.text();
      try {
        const j = JSON.parse(msg);
        msg = typeof j.detail === "string" ? j.detail : msg;
      } catch (_) {}
      throw new Error(msg || res.statusText);
    }
    return res.json();
  }

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.hidden = true;
    }, 2200);
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function num(v) {
    if (v == null || v === "unknown") return "—";
    const x = Number(v);
    if (Number.isNaN(x)) return esc(v);
    if (x >= 100) return x.toFixed(2);
    if (x >= 1) return String(Number(x.toPrecision(6)));
    return String(x);
  }

  function money(v) {
    if (v == null) return "—";
    const x = Number(v);
    if (Number.isNaN(x)) return "—";
    return "$" + (x >= 100 ? x.toFixed(2) : String(Number(x.toPrecision(6))));
  }

  function instrument(p) {
    return p.symbol || p.name || p.display || "";
  }

  function adTop(p) {
    return p && p.ad_status === "known" ? num(p.ad_top) : "—";
  }

  function adBot(p) {
    return p && p.ad_status === "known" ? num(p.ad_bottom) : "—";
  }

  function pips(reds) {
    const n = Number(reds);
    if (!Number.isFinite(n) || n <= 0) return '<span class="pip-none">—</span>';
    const count = Math.min(6, Math.round(n));
    return Array.from({ length: count }, () => '<span class="pip"></span>').join("");
  }

  function newsText(p) {
    return p && p.news ? esc(p.news) : "CLEAR";
  }

  function restText(p) {
    return p && p.resting ? "rest" : "—";
  }

  function tfText(p) {
    return p && p.tf && p.tf !== "unknown" ? esc(p.tf) : "—";
  }

  function select(id) {
    state.selected = Number(id);
    renderSheet(state.plans.find((x) => x.id === state.selected) || null);
  }

  function closeSheet() {
    state.selected = null;
    const sheet = $("#planSheet");
    const back = $("#sheetBack");
    sheet.hidden = true;
    sheet.innerHTML = "";
    back.hidden = true;
  }

  function renderNeeds(needs) {
    const host = $("#needsStack");
    if (!needs || !needs.length) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    host.innerHTML =
      "<h2>Needs you</h2>" +
      needs
        .map((n) => {
          const kind = n.kind === "approve_name" ? "approve name" : "line change";
          const line =
            n.kind === "approve_name"
              ? `${esc(n.symbol)} ${esc(n.market)}`
              : `${esc(n.symbol)} ${num((n.payload || {}).ad_top)} → ${num(
                  (n.payload || {}).ad_bottom
                )}`;
          return `<article class="need" data-need="${n.id}">
            <p><span class="kind">${kind}</span>${line}</p>
            <button type="button" class="act" data-acc="${n.id}">Accept</button>
            <button type="button" class="act ghost" data-rej="${n.id}">Reject</button>
          </article>`;
        })
        .join("");
  }

  function ghost() {
    return `<div class="stage open"><p class="ghost">open · $200</p></div>`;
  }

  function liveStage(p) {
    const newsCls = p.news ? " news-hot" : "";
    return `<button type="button" class="stage live" data-id="${p.id}">
      <div class="who"><span class="nm">${esc(instrument(p))}</span><span class="tf">${tfText(p)}</span></div>
      <div class="body">
        <div class="col ad-stack">
          <span class="lbl">AD</span>
          <div class="pair"><span>Top</span>${adTop(p)}</div>
          <div class="pair"><span>Bottom</span>${adBot(p)}</div>
        </div>
        <div class="col">
          <span class="lbl">Next layer</span>
          <div class="fig">${money(p.next_layer_usd)}</div>
        </div>
        <div class="col">
          <span class="lbl">Pips</span>
          <div class="pips">${pips(p.reds)}</div>
        </div>
        <div class="col col-vol">
          <span class="lbl">Volume</span>
          <div class="fig">${esc(p.volume && p.volume !== "unknown" ? p.volume : "—")}</div>
        </div>
        <div class="col col-news">
          <span class="lbl">News</span>
          <div class="fig${newsCls}">${newsText(p)}</div>
        </div>
        <div class="col">
          <span class="lbl">Resting</span>
          <div class="fig">${restText(p)}</div>
        </div>
      </div>
      <div class="recut-bar">
        Recut
        <span class="line">LINE ${adTop(p)} / ${adBot(p)} · ${esc(p.remaining_layers || 0)} LEFT</span>
        <button type="button" class="kill" data-kill="${p.id}">Kill</button>
      </div>
    </button>`;
  }

  function renderStages(plans) {
    const host = $("#liveStages");
    const live = (plans || []).filter((p) => p.live).slice(0, 2);
    const parts = live.map(liveStage);
    while (parts.length < 2) parts.push(ghost());
    host.innerHTML = parts.join("");
  }

  function emptyRanks() {
    return [1, 2, 3]
      .map(
        (i) => `<div class="rank">
          <span class="idx">${String(i).padStart(2, "0")}</span>
          <span class="dash">—</span>
          <span></span><span class="dash">—</span><span class="dash">—</span>
          <span class="dash">—</span><span class="dash">—</span>
          <span class="dash">—</span><span class="dash">—</span><span></span>
        </div>`
      )
      .join("");
  }

  function renderRanks(plans) {
    const host = $("#rankList");
    const rows = (plans || []).filter((p) => !p.live);
    if (!rows.length && !(plans || []).length) {
      host.className = "empty-ranks";
      host.innerHTML = emptyRanks();
      return;
    }
    host.className = "";
    host.innerHTML = rows
      .map((p, i) => {
        const killed = p.status === "killed" ? " killed" : "";
        const ad =
          p.ad_status === "known"
            ? `${adTop(p)}<small>${adBot(p)}</small>`
            : "—<small>uncut</small>";
        return `<button type="button" class="rank${killed}" data-id="${p.id}" style="animation-delay:${i * 30}ms">
          <span class="idx">${String(i + 1).padStart(2, "0")}</span>
          <span class="nm">${esc(instrument(p))}</span>
          <span class="tf">${tfText(p)}</span>
          <span class="ad">${ad}</span>
          <span class="next">${money(p.next_layer_usd)}</span>
          <span class="pips">${pips(p.reds)}</span>
          <span class="vol">${esc(p.volume && p.volume !== "unknown" ? p.volume : "—")}</span>
          <span class="news${p.news ? " news-hot" : ""}">${newsText(p)}</span>
          <span class="rest">${restText(p)}</span>
          <span class="go">›</span>
        </button>`;
      })
      .join("");
  }

  function renderSheet(p) {
    const host = $("#planSheet");
    const back = $("#sheetBack");
    if (!p) {
      closeSheet();
      return;
    }
    back.hidden = false;
    host.hidden = false;
    const layers = (p.layers || [])
      .map((l) => `${l.idx} ${num(l.price)} · ${money(l.usd)}`)
      .join("  ·  ");
    host.innerHTML = `
      <button type="button" class="sheet-x" id="sheetClose">Close</button>
      <h3>${esc(instrument(p))}</h3>
      <p class="sym">${esc(p.symbol)} · ${esc(p.market)} · ${esc(p.status)}</p>
      <dl class="facts">
        <div class="fact"><dt>TF</dt><dd>${tfText(p)}</dd></div>
        <div class="fact ad"><dt>AD top → bottom</dt><dd>${adTop(p)} → ${adBot(p)}</dd></div>
        <div class="fact"><dt>Source</dt><dd>${esc(p.ad_source || "unknown")}</dd></div>
        <div class="fact"><dt>Top bar</dt><dd>${esc(p.bar_top_label)}</dd></div>
        <div class="fact"><dt>Bottom bar</dt><dd>${esc(p.bar_bottom_label)}</dd></div>
        <div class="fact"><dt>Reds</dt><dd>${esc(p.reds)}</dd></div>
        <div class="fact"><dt>Volume</dt><dd>${esc(p.volume)}</dd></div>
        <div class="fact"><dt>News</dt><dd>${newsText(p)}</dd></div>
        <div class="fact"><dt>Resting</dt><dd>${restText(p)}</dd></div>
        <div class="fact"><dt>Next layer</dt><dd>${money(p.next_layer_usd)}</dd></div>
        <div class="fact"><dt>Allocated</dt><dd>${money(p.allocated_usd)}</dd></div>
        <div class="fact"><dt>Layers left</dt><dd>${esc(p.remaining_layers)}</dd></div>
      </dl>
      <p class="note">${esc(p.ad_note || "")}</p>
      <p class="layer-line">${layers || "no layers — AD uncut"}</p>
      <form class="console" id="recutForm">
        <label>Line top<input name="ad_top" inputmode="decimal" value="${p.ad_top ?? ""}" /></label>
        <label>Line bottom<input name="ad_bottom" inputmode="decimal" value="${p.ad_bottom ?? ""}" /></label>
        <label>Layers<input name="remaining_layers" type="number" min="1" max="12" value="${
          p.remaining_layers || 5
        }" /></label>
        <button class="act" type="submit">Recut</button>
        <button class="kill" type="button" id="btnKill">Kill</button>
      </form>`;
  }

  function tickLocal() {
    const el = $("#localClock");
    if (!el) return;
    const t = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date());
    el.textContent = "LOCAL " + t;
  }

  function paint() {
    const room = state.room || {};
    const liveN = room.live_count || 0;
    document.body.dataset.tone = room.tone || "empty";
    document.body.dataset.live = liveN > 0 ? "1" : "0";
    const idle = $("#idleLine");
    if (idle) {
      idle.textContent =
        liveN > 0
          ? `$${Number(state.account.equity_usd || 200).toFixed(0)} book · ${liveN}/2 live`
          : "$200 book idle · max 2 live";
    }
    renderNeeds(state.needs);
    renderStages(state.plans);
    renderRanks(state.plans);
    if (state.selected) {
      const p = state.plans.find((x) => x.id === state.selected);
      if (p) renderSheet(p);
    }
  }

  async function load() {
    const data = await api("/api/machine/plans");
    state.plans = data.plans || [];
    state.needs = data.needs_you || [];
    state.room = data.room || {};
    state.account = data.account || {};
    const acc = state.account;
    $("#bookAmt").textContent = `$${Number(acc.equity_usd || 200).toFixed(2)}`;
    paint();
  }

  function onPick(e) {
    if (e.target.closest("[data-kill]")) return;
    const btn = e.target.closest("[data-id]");
    if (!btn || btn.classList.contains("open")) return;
    select(btn.getAttribute("data-id"));
  }

  async function killPlan(id) {
    await api(`/api/machine/plans/${id}/kill`, { method: "POST", body: "{}" });
    toast("Killed");
    closeSheet();
    await load();
  }

  $("#liveStages").addEventListener("click", async (e) => {
    const k = e.target.closest("[data-kill]");
    if (k) {
      e.preventDefault();
      e.stopPropagation();
      try {
        await killPlan(k.getAttribute("data-kill"));
      } catch (err) {
        toast(err.message);
      }
      return;
    }
    onPick(e);
  });
  $("#rankList").addEventListener("click", onPick);
  $("#sheetBack").addEventListener("click", closeSheet);
  $("#planSheet").addEventListener("click", async (e) => {
    if (e.target.id === "sheetClose") {
      closeSheet();
      return;
    }
    if (e.target.id !== "btnKill") return;
    try {
      await killPlan(state.selected);
    } catch (err) {
      toast(err.message);
    }
  });

  $("#needsStack").addEventListener("click", async (e) => {
    const acc = e.target.getAttribute("data-acc");
    const rej = e.target.getAttribute("data-rej");
    try {
      if (acc) await api(`/api/machine/needs-you/${acc}/accept`, { method: "POST", body: "{}" });
      if (rej) await api(`/api/machine/needs-you/${rej}/reject`, { method: "POST", body: "{}" });
      if (acc || rej) await load();
    } catch (err) {
      toast(err.message);
    }
  });

  $("#planSheet").addEventListener("submit", async (e) => {
    if (e.target.id !== "recutForm") return;
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api(`/api/machine/plans/${state.selected}/recut`, {
        method: "POST",
        body: JSON.stringify({
          ad_top: fd.get("ad_top") ? Number(fd.get("ad_top")) : null,
          ad_bottom: fd.get("ad_bottom") ? Number(fd.get("ad_bottom")) : null,
          remaining_layers: fd.get("remaining_layers")
            ? Number(fd.get("remaining_layers"))
            : null,
        }),
      });
      toast("Recut");
      await load();
    } catch (err) {
      toast(err.message);
    }
  });

  tickLocal();
  setInterval(tickLocal, 30000);

  load().catch((e) => {
    document.body.dataset.tone = "empty";
    document.body.dataset.live = "0";
    $("#idleLine").textContent = "$200 book idle · max 2 live";
    $("#rankList").className = "empty-ranks";
    $("#rankList").innerHTML = emptyRanks();
    $("#liveStages").innerHTML = ghost() + ghost();
    toast(e.message);
  });
})();
