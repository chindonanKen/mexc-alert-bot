/* Slate Machine — live / empty / needs. Seed names, no chart. */
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
    }, 1800);
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
    return "$" + (Math.abs(x) >= 1 ? String(Math.round(x)) : String(Number(x.toPrecision(4))));
  }

  function cashInt() {
    const x = Number(state.account.cash_usd);
    return Number.isFinite(x) ? Math.round(x) : 200;
  }

  function instrument(p) {
    return p.name || p.display || p.symbol || "";
  }

  function fut(p) {
    return p && p.market === "futures" ? " · FUT" : "";
  }

  function adTop(p) {
    return p && p.ad_status === "known" ? num(p.ad_top) : "—";
  }

  function adBot(p) {
    return p && p.ad_status === "known" ? num(p.ad_bottom) : "—";
  }

  function pips(reds) {
    const n = Number(reds);
    if (!Number.isFinite(n) || n <= 0) return "";
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

  function volText(p) {
    return p && p.volume && p.volume !== "unknown" ? esc(p.volume) : "—";
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

  function ghost(slot, liveN) {
    if (liveN > 0) {
      return `<div class="stage open">
        <p class="slot-lbl">SLOT ${slot}</p>
        <p class="ghost">open · $${cashInt()} of $200</p>
      </div>`;
    }
    const wait = slot === 1 ? "waiting · $200 book" : "waiting · max 2 live";
    return `<div class="stage open">
      <p class="slot-lbl">SLOT ${slot}</p>
      <p class="ghost">${wait}</p>
    </div>`;
  }

  function liveStage(p) {
    const newsCls = p.news ? " news-hot" : "";
    return `<article class="stage live" data-id="${p.id}">
      <div class="who">
        <span class="nm">${esc(instrument(p))}${esc(fut(p))}</span>
        <span class="tf">${tfText(p)}</span>
      </div>
      <div class="body">
        <div>
          <span class="lbl">AD</span>
          <div class="ad-pair">${adTop(p)}<br>${adBot(p)}</div>
        </div>
        <div>
          <span class="lbl">Next</span>
          <div class="fig">${money(p.next_layer_usd)}</div>
        </div>
        <div>
          <span class="lbl">Pips</span>
          <div class="pips">${pips(p.reds)}</div>
        </div>
        <div>
          <span class="lbl">Vol</span>
          <div class="fig">${volText(p)}</div>
        </div>
        <div>
          <span class="lbl">News</span>
          <div class="fig${newsCls}">${newsText(p)}</div>
        </div>
        <div>
          <span class="lbl">Rest</span>
          <div class="fig">${restText(p)}</div>
        </div>
      </div>
      <div class="recut-bar">
        <span>LINE ${adTop(p)}</span>
        <span>${esc(p.remaining_layers || 0)} LEFT</span>
        <button type="button" class="kill" data-kill="${p.id}">Kill</button>
      </div>
    </article>`;
  }

  function renderStages(plans) {
    const host = $("#liveStages");
    const live = (plans || []).filter((p) => p.live).slice(0, 2);
    const parts = live.map(liveStage);
    while (parts.length < 2) parts.push(ghost(parts.length + 1, live.length));
    host.innerHTML = parts.join("");
  }

  function emptyRanks() {
    return [1, 2, 3]
      .map(
        (i) => `<div class="rank skeleton">
          <span class="idx">${String(i).padStart(2, "0")}</span>
          <span class="bone"></span>
          <span class="bone short"></span>
          <span class="bone"></span>
        </div>`
      )
      .join("");
  }

  function renderRanks(plans) {
    const host = $("#rankList");
    const rows = (plans || []).filter((p) => !p.live);
    if (!rows.length) {
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
            ? `${adTop(p)}<br>${adBot(p)}`
            : "—";
        return `<button type="button" class="rank${killed}" data-id="${p.id}" style="animation-delay:${i * 30}ms">
          <span class="idx">${String(i + 1).padStart(2, "0")}</span>
          <span class="who"><span class="nm">${esc(instrument(p))}${esc(fut(p))}</span><span class="tf">${tfText(p)}</span></span>
          <span class="ad">${ad}</span>
          <span class="next">${money(p.next_layer_usd)}</span>
          <span class="pips">${pips(p.reds)}</span>
          <span class="vol">${volText(p)}</span>
          <span class="news${p.news ? " news-hot" : ""}">${newsText(p)}</span>
          <span class="rest">${restText(p)}</span>
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
    host.innerHTML = `
      <p class="plan-kicker"><span>Plan</span><button type="button" class="sheet-x" id="sheetClose">Close</button></p>
      <h3>${esc(instrument(p))}${esc(fut(p))}</h3>
      <dl>
        <div class="sheet-row"><dt>TF</dt><dd>${tfText(p)}</dd></div>
        <div class="sheet-row ad"><dt>AD</dt><dd>${adTop(p)} / ${adBot(p)}</dd></div>
        <div class="sheet-row"><dt>Next</dt><dd>${money(p.next_layer_usd)}</dd></div>
        <div class="sheet-row"><dt>Reds</dt><dd><span class="pips">${pips(p.reds)}</span></dd></div>
        <div class="sheet-row"><dt>Vol</dt><dd>${volText(p)}</dd></div>
        <div class="sheet-row"><dt>News</dt><dd>${newsText(p)}</dd></div>
        <div class="sheet-row"><dt>Resting</dt><dd>${restText(p)}</dd></div>
        <div class="sheet-row"><dt>Source</dt><dd>${esc(p.ad_source || "unknown")}</dd></div>
        <div class="sheet-row"><dt>Top bar</dt><dd>${esc(p.bar_top_label)}</dd></div>
        <div class="sheet-row"><dt>Bottom bar</dt><dd>${esc(p.bar_bottom_label)}</dd></div>
      </dl>
      <form class="console" id="recutForm">
        <label>Line<input name="ad_top" inputmode="decimal" value="${p.ad_top ?? ""}" placeholder="top" /></label>
        <label>Bottom<input name="ad_bottom" inputmode="decimal" value="${p.ad_bottom ?? ""}" /></label>
        <label>Left<input name="remaining_layers" type="number" min="1" max="12" value="${
          p.remaining_layers || 5
        }" /></label>
        <div class="sheet-actions">
          <button class="act" type="submit">Recut</button>
          <button class="kill" type="button" id="btnKill">Kill</button>
        </div>
      </form>`;
  }

  function paint() {
    const room = state.room || {};
    const liveN = room.live_count || 0;
    document.body.dataset.tone = room.tone || "empty";
    document.body.dataset.live = liveN > 0 ? "1" : "0";
    const book = $("#bookLine");
    if (book) {
      book.textContent =
        liveN > 0
          ? `$200 book · ${liveN} of 2 live`
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
    paint();
  }

  function onPick(e) {
    if (e.target.closest("[data-kill]")) return;
    const btn = e.target.closest("[data-id]");
    if (!btn || btn.classList.contains("open") || btn.classList.contains("skeleton")) return;
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

  load().catch((e) => {
    document.body.dataset.tone = "empty";
    document.body.dataset.live = "0";
    $("#bookLine").textContent = "$200 book idle · max 2 live";
    $("#rankList").className = "empty-ranks";
    $("#rankList").innerHTML = emptyRanks();
    $("#liveStages").innerHTML = ghost(1, 0) + ghost(2, 0);
    toast(e.message);
  });
})();
