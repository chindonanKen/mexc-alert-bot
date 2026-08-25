/* Isolated Machine chamber. Numbers only. Own room — not Desk JS patterns. */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const state = {
    token: localStorage.getItem("desk_token") || "",
    plans: [],
    needs: [],
    room: {},
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
    if (v == null || v === "unknown") return "unknown";
    const x = Number(v);
    if (Number.isNaN(x)) return esc(v);
    if (x >= 100) return x.toFixed(2);
    if (x >= 1) return String(Number(x.toPrecision(6)));
    return String(x);
  }

  function adClass(p) {
    return p && p.ad_status === "known" ? "ad" : "ad uncut";
  }

  function adText(p) {
    if (!p || p.ad_status !== "known") return "uncut";
    return esc(p.ad);
  }

  function nextLayer(p) {
    return p && p.next_layer_usd != null ? "$" + num(p.next_layer_usd) : "—";
  }

  function select(id) {
    state.selected = Number(id);
    paint();
    renderPlan(state.plans.find((x) => x.id === state.selected) || null);
  }

  function renderNeeds(needs) {
    const host = $("#needsYou");
    const clear = $("#needsClear");
    if (!needs || !needs.length) {
      host.hidden = true;
      host.innerHTML = "";
      if (clear) {
        clear.textContent = "Needs-you · clear";
        clear.setAttribute("data-hot", "0");
      }
      return;
    }
    if (clear) {
      clear.textContent = "Needs-you · " + needs.length;
      clear.setAttribute("data-hot", "1");
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

  function renderLive(plans, room) {
    const host = $("#liveDeck");
    const live = (plans || []).filter((p) => p.live).slice(0, 2);
    const open = Math.max(0, 2 - live.length);
    const parts = live.map((p, i) => berth(p, i + 1));
    for (let i = 0; i < open; i += 1) {
      parts.push(
        `<div class="berth open">
          <p class="slot">Slot ${live.length + i + 1} · open</p>
          <p class="name">Nothing resting</p>
          <p class="meta">A live play can take this berth. Max two. $100 into the layers.</p>
        </div>`
      );
    }
    if (!live.length && (room || {}).empty) {
      host.innerHTML =
        '<div class="berth open" style="grid-column:1/-1"><p class="slot">Empty book</p><p class="name">No seed plans</p></div>';
      return;
    }
    host.innerHTML = parts.join("");
  }

  function berth(p, slot) {
    const on = state.selected === p.id ? " on" : "";
    return `<button type="button" class="berth live${on}" data-id="${p.id}">
      <p class="slot">Live ${slot}${p.resting ? " · resting" : ""}</p>
      <p class="name">${esc(p.name || p.display)}</p>
      <p class="${adClass(p)}">${adText(p)}</p>
      <p class="meta"><b>${esc(p.tf)}</b> · reds ${esc(p.reds)} · vol ${esc(p.volume)} · news ${
        p.news ? esc(p.news) : "—"
      } · next ${nextLayer(p)}</p>
    </button>`;
  }

  function renderHangar(plans) {
    const host = $("#hangarList");
    const hangar = (plans || []).filter((p) => !p.live);
    if (!hangar.length) {
      host.innerHTML = '<p class="empty-hangar">Hangar is empty — both names are live, or the book is bare.</p>';
      return;
    }
    host.innerHTML = hangar
      .map((p) => {
        const on = state.selected === p.id ? " on" : "";
        const killed = p.status === "killed" ? " killed" : "";
        return `<button type="button" class="slip${on}${killed}" data-id="${p.id}">
          <span class="nm">${esc(p.name || p.display)}</span>
          <span class="tf">${esc(p.tf)}</span>
          <span class="adline ${p.ad_status === "known" ? "" : "uncut"}">${adText(p)}</span>
          <span class="fig">${nextLayer(p)}</span>
          <span class="fig">${esc(p.reds)}</span>
          <span class="fig">${esc(p.volume)}</span>
          <span class="fig ${p.news ? "news-hot" : ""}">${p.news ? esc(p.news) : "—"}</span>
          <span class="fig">${p.resting ? "resting" : "dry"}</span>
        </button>`;
      })
      .join("");
  }

  function renderPlan(p) {
    const host = $("#planRoom");
    if (!p) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    const layers = (p.layers || [])
      .map((l) => `${l.idx} ${num(l.price)} · $${num(l.usd)}`)
      .join("  ·  ");
    host.innerHTML = `
      <h3>${esc(p.name)}</h3>
      <p class="sym">${esc(p.symbol)} · ${esc(p.market)} · ${esc(p.status)}</p>
      <dl class="facts">
        <div class="fact"><dt>TF</dt><dd>${esc(p.tf)}</dd></div>
        <div class="fact"><dt>AD top → bottom</dt><dd>${adText(p)}</dd></div>
        <div class="fact"><dt>Source</dt><dd>${esc(p.ad_source || "unknown")}</dd></div>
        <div class="fact"><dt>Top bar</dt><dd>${esc(p.bar_top_label)}</dd></div>
        <div class="fact"><dt>Bottom bar</dt><dd>${esc(p.bar_bottom_label)}</dd></div>
        <div class="fact"><dt>Reds</dt><dd>${esc(p.reds)}</dd></div>
        <div class="fact"><dt>Volume</dt><dd>${esc(p.volume)}</dd></div>
        <div class="fact"><dt>News</dt><dd>${p.news ? esc(p.news) : "—"}</dd></div>
        <div class="fact"><dt>Resting</dt><dd>${p.resting ? "yes" : "no"}</dd></div>
        <div class="fact"><dt>Next layer</dt><dd>${nextLayer(p)}</dd></div>
        <div class="fact"><dt>Allocated</dt><dd>$${num(p.allocated_usd)}</dd></div>
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
        <button class="act kill" type="button" id="btnKill">Kill</button>
      </form>`;
  }

  function paint() {
    document.body.dataset.tone = state.room.tone || "watch";
    renderNeeds(state.needs);
    renderLive(state.plans, state.room);
    renderHangar(state.plans);
  }

  async function load() {
    const data = await api("/api/machine/plans");
    state.plans = data.plans || [];
    state.needs = data.needs_you || [];
    state.room = data.room || {};
    const acc = data.account || {};
    $("#vault").textContent =
      `$${acc.equity_usd} · ${acc.live_plays}/${acc.max_live_plays} live · 1x · cash $${acc.cash_usd}`;
    paint();
    if (state.selected) {
      renderPlan(state.plans.find((x) => x.id === state.selected) || null);
    }
  }

  function onPick(e) {
    const btn = e.target.closest("[data-id]");
    if (!btn || btn.classList.contains("open")) return;
    select(btn.getAttribute("data-id"));
  }

  $("#liveDeck").addEventListener("click", onPick);
  $("#hangarList").addEventListener("click", onPick);

  $("#needsYou").addEventListener("click", async (e) => {
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

  $("#planRoom").addEventListener("submit", async (e) => {
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

  $("#planRoom").addEventListener("click", async (e) => {
    if (e.target.id !== "btnKill") return;
    try {
      await api(`/api/machine/plans/${state.selected}/kill`, { method: "POST", body: "{}" });
      toast("Killed");
      await load();
    } catch (err) {
      toast(err.message);
    }
  });

  load().catch((e) => {
    document.body.dataset.tone = "empty";
    $("#hangarList").innerHTML = `<p class="empty-book">${esc(e.message)}</p>`;
  });
})();
