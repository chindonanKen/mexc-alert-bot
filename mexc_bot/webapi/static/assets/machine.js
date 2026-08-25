/* Isolated AD Machine page — numbers only, no chart. */
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const state = {
    token: localStorage.getItem("desk_token") || "",
    plans: [],
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
      const t = prompt("Desk API token (DESK_API_TOKEN):");
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
        msg = j.detail || j.message || msg;
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
    }, 2400);
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
    if (x >= 1) return String(x);
    return String(x);
  }

  function renderNeeds(needs) {
    const host = $("#needsYou");
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
          const line =
            n.kind === "approve_name"
              ? `Approve new name ${esc(n.symbol)} ${esc(n.market)}`
              : `Line change ${esc(n.symbol)} → ${num((n.payload || {}).ad_top)} → ${num(
                  (n.payload || {}).ad_bottom
                )}`;
          return `<div class="need-row" data-need="${n.id}">
            <span>${line}</span>
            <button type="button" class="btn sm" data-acc="${n.id}">Accept</button>
            <button type="button" class="btn soft sm" data-rej="${n.id}">Reject</button>
          </div>`;
        })
        .join("");
  }

  function renderRows(plans) {
    const body = $("#rankBody");
    if (!plans.length) {
      body.innerHTML = '<tr><td colspan="8" class="mute">No plans</td></tr>';
      return;
    }
    body.innerHTML = plans
      .map((p) => {
        const on = state.selected === p.id ? " on" : "";
        const live = p.live ? " live" : "";
        return `<tr class="${live}${on}" data-id="${p.id}">
          <td>${esc(p.name || p.display)}${p.live ? " · live" : ""}</td>
          <td class="mono">${esc(p.tf)}</td>
          <td class="mono">${esc(p.ad)}</td>
          <td class="mono">${p.next_layer_usd == null ? "—" : "$" + num(p.next_layer_usd)}</td>
          <td class="mono">${esc(p.reds)}</td>
          <td>${esc(p.volume)}</td>
          <td>${p.news ? esc(p.news) : "—"}</td>
          <td>${p.resting ? "yes" : "no"}</td>
        </tr>`;
      })
      .join("");
  }

  function renderDetail(p) {
    const host = $("#detail");
    if (!p) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    const layers = (p.layers || [])
      .map((l) => `${l.idx}. ${num(l.price)} · $${num(l.usd)}`)
      .join(" · ");
    host.innerHTML = `
      <h2>${esc(p.name)} <span class="mute">${esc(p.symbol)} ${esc(p.market)}</span></h2>
      <div class="grid">
        <div class="kv"><b>TF</b>${esc(p.tf)}</div>
        <div class="kv"><b>AD</b>${esc(p.ad)}</div>
        <div class="kv"><b>Source</b>${esc(p.ad_source || "unknown")}</div>
        <div class="kv"><b>Top bar</b>${esc(p.bar_top_label)}</div>
        <div class="kv"><b>Bottom bar</b>${esc(p.bar_bottom_label)}</div>
        <div class="kv"><b>Reds</b>${esc(p.reds)}</div>
        <div class="kv"><b>Volume</b>${esc(p.volume)}</div>
        <div class="kv"><b>News</b>${p.news ? esc(p.news) : "—"}</div>
        <div class="kv"><b>Resting</b>${p.resting ? "yes" : "no"}</div>
        <div class="kv"><b>Allocated</b>$${num(p.allocated_usd)}</div>
        <div class="kv"><b>Remaining layers</b>${esc(p.remaining_layers)}</div>
      </div>
      <p class="mute">${esc(p.ad_note || "")}</p>
      <p class="layers">${layers || "no layers (AD unknown)"}</p>
      <form class="recut" id="recutForm">
        <label>Line top<input name="ad_top" inputmode="decimal" value="${p.ad_top ?? ""}" /></label>
        <label>Line bottom<input name="ad_bottom" inputmode="decimal" value="${p.ad_bottom ?? ""}" /></label>
        <label>Layers<input name="remaining_layers" type="number" min="1" max="12" value="${
          p.remaining_layers || 5
        }" /></label>
        <button class="btn" type="submit">Recut</button>
        <button class="btn danger" type="button" id="btnKill">Kill</button>
      </form>`;
  }

  async function load() {
    const data = await api("/api/machine/plans");
    state.plans = data.plans || [];
    const acc = data.account || {};
    $("#accountLine").textContent =
      `$${acc.equity_usd} equity · $${acc.max_per_play_usd}/play · ${acc.live_plays}/${acc.max_live_plays} live · 1x · cash $${acc.cash_usd}`;
    renderNeeds(data.needs_you || []);
    renderRows(state.plans);
    if (state.selected) {
      const p = state.plans.find((x) => x.id === state.selected);
      renderDetail(p || null);
    }
  }

  $("#rankBody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    state.selected = Number(tr.getAttribute("data-id"));
    renderRows(state.plans);
    renderDetail(state.plans.find((x) => x.id === state.selected));
  });

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

  $("#detail").addEventListener("submit", async (e) => {
    if (e.target.id !== "recutForm") return;
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
      ad_top: fd.get("ad_top") ? Number(fd.get("ad_top")) : null,
      ad_bottom: fd.get("ad_bottom") ? Number(fd.get("ad_bottom")) : null,
      remaining_layers: fd.get("remaining_layers") ? Number(fd.get("remaining_layers")) : null,
    };
    try {
      await api(`/api/machine/plans/${state.selected}/recut`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      toast("Recut");
      await load();
    } catch (err) {
      toast(err.message);
    }
  });

  $("#detail").addEventListener("click", async (e) => {
    if (e.target.id !== "btnKill") return;
    try {
      await api(`/api/machine/plans/${state.selected}/kill`, { method: "POST", body: "{}" });
      toast("Killed");
      await load();
    } catch (err) {
      toast(err.message);
    }
  });

  $("#btnRefresh").addEventListener("click", () => load().catch((e) => toast(e.message)));

  load().catch((e) => {
    $("#rankBody").innerHTML = `<tr><td colspan="8" class="mute">${esc(e.message)}</td></tr>`;
  });
})();
