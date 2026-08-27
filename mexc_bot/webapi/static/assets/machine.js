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

  const GLOSS = {
    AD: "this copy, top → bottom",
    LAST: "official last price",
    NEXT: "dollars for the next layer",
    REDS: "red candles on this TF",
    VOL: "last bar size",
    NEWS: "delist/scam or clear",
    REST: "time since the play armed",
    LINE: "next layer price",
  };

  function newsText(p) {
    return p && p.news ? esc(p.news) : "CLEAR";
  }

  function lastText(p) {
    if (!p || p.last_price == null || p.last_price === "") return "—";
    return num(p.last_price);
  }

  function redsText(p) {
    const n = Number(p && p.reds);
    if (!Number.isFinite(n)) return "—";
    return String(n);
  }

  function layerLine(L) {
    return `layer ${L.idx} · ${money(L.usd)} at ${num(L.price)}`;
  }

  function field(key, value, extra) {
    return `<div class="field">
      <span class="lbl">${key}</span>
      <span class="gloss">${esc(GLOSS[key] || "")}</span>
      <div class="fig">${value}${extra || ""}</div>
    </div>`;
  }

  function linePrice(p) {
    const orders = (p.working_orders || [])
      .filter((o) => o && o.price != null)
      .slice()
      .sort((a, b) => Number(a.idx || 0) - Number(b.idx || 0));
    if (orders[0]) return num(orders[0].price);
    const layers = p.layers || [];
    if (layers[0] && layers[0].price != null) return num(layers[0].price);
    if (p.recut_line != null) return num(p.recut_line);
    return "—";
  }

  function restClock(p) {
    const start = Number(p && p.armed_at);
    if (!p || !p.resting || !Number.isFinite(start) || start <= 0) return "";
    const mins = Math.max(0, Math.round((Date.now() / 1000 - start) / 60));
    if (mins < 60) return mins + "m";
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m ? h + "h" + m + "m" : h + "h";
  }

  function fmtVol(n) {
    if (n >= 1e6) {
      const x = n / 1e6;
      return (x >= 10 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, "")) + "M";
    }
    if (n >= 1e3) {
      const x = n / 1e3;
      return (x >= 10 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, "")) + "K";
    }
    return String(Math.round(n));
  }

  function tfText(p) {
    return p && p.tf && p.tf !== "unknown" ? esc(p.tf) : "—";
  }

  function whyText(p) {
    const s = p && p.decision;
    if (s == null || s === "") return "";
    return String(s);
  }

  function whyLine(p) {
    const s = whyText(p);
    return s ? `<span class="why">${esc(s)}</span>` : "";
  }

  function volText(p) {
    const labels = /^(dry|normal|elevated|climax|unknown)$/i;
    const candidates = [p && p.volume_n, p && p.vol, p && p.quote_volume, p && p.volume];
    for (const c of candidates) {
      if (c == null || c === "") continue;
      if (typeof c === "string" && labels.test(c.trim())) continue;
      const n = Number(c);
      if (Number.isFinite(n) && n > 0) return fmtVol(n);
      if (typeof c === "string" && /^\d+(\.\d+)?[KMB]$/i.test(c.trim())) return esc(c.trim());
    }
    return "";
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
        ${whyLine(p)}
      </div>
      <div class="body">
        <div class="field">
          <span class="lbl">AD</span>
          <span class="gloss">${esc(GLOSS.AD)}</span>
          <div class="ad-pair">${adTop(p)}<br>${adBot(p)}</div>
        </div>
        ${field("LAST", lastText(p))}
        ${field("NEXT", money(p.next_layer_usd))}
        ${field("REDS", redsText(p), `<span class="pips">${pips(p.reds)}</span>`)}
        ${field("VOL", volText(p) || "—")}
        <div class="field">
          <span class="lbl">NEWS</span>
          <span class="gloss">${esc(GLOSS.NEWS)}</span>
          <div class="fig${newsCls}">${newsText(p)}</div>
        </div>
        ${field("REST", restClock(p) || "—")}
      </div>
      <div class="recut-bar">
        <span><span class="lbl">LINE</span> <span class="gloss">${esc(GLOSS.LINE)}</span> ${linePrice(p)}</span>
        <span>${esc(p.remaining_layers || 0)} LEFT</span>
        <button type="button" class="kill" data-kill="${p.id}">Kill</button>
      </div>
    </article>`;
  }

  function renderStages(plans) {
    const host = $("#liveStages");
    const live = (plans || []).filter((p) => p.live).slice(0, 2);
    const ids = live.map((p) => String(p.id)).join(",");
    if (host.dataset.ids === ids && host.querySelector(".stage.live, .stage.open")) {
      live.forEach((p) => {
        const el = host.querySelector(`.stage.live[data-id="${p.id}"]`);
        if (!el) return;
        const last = el.querySelector(".body .field:nth-child(2) .fig");
        if (last) last.textContent = lastText(p);
        const rest = el.querySelector(".body .field:last-child .fig");
        if (rest) rest.textContent = restClock(p) || "—";
      });
      return;
    }
    host.dataset.ids = ids;
    const parts = live.map(liveStage);
    while (parts.length < 2) parts.push(ghost(parts.length + 1, live.length));
    host.innerHTML = parts.join("");
  }

  function emptyRanks() {
    return `<p class="rank-empty">No ranked names</p>`;
  }

  function rankAd(p) {
    return p.ad_status === "known" ? `${adTop(p)}<br>${adBot(p)}` : "unknown";
  }

  function patchRank(el, p, i) {
    if (!el) return;
    el.classList.toggle("killed", p.status === "killed");
    const set = (sel, html) => {
      const n = el.querySelector(sel);
      if (n && n.innerHTML !== html) n.innerHTML = html;
    };
    set(".idx", String(i + 1).padStart(2, "0"));
    set(".who .nm", `${esc(instrument(p))}${esc(fut(p))}`);
    set(".who .tf", tfText(p));
    set(".ad", rankAd(p));
    set(".last", lastText(p));
    set(".next", money(p.next_layer_usd));
    set(".reds", `${redsText(p)}<span class="pips">${pips(p.reds)}</span>`);
    set(".vol", volText(p) || "—");
    const news = el.querySelector(".news");
    if (news) {
      news.classList.toggle("news-hot", !!p.news);
      const t = newsText(p);
      if (news.textContent !== t) news.textContent = t;
    }
    set(".rest", restClock(p) || "—");
    const why = whyText(p);
    let w = el.querySelector(".why");
    if (why) {
      if (!w) {
        w = document.createElement("span");
        w.className = "why";
        el.appendChild(w);
      }
      if (w.textContent !== why) w.textContent = why;
    } else if (w) {
      w.remove();
    }
  }

  function renderRanks(plans) {
    const host = $("#rankList");
    const rows = (plans || []).filter((p) => !p.live);
    if (!rows.length) {
      host.className = "empty-ranks";
      if (!host.querySelector(".rank-empty")) {
        host.dataset.ids = "";
        host.innerHTML = emptyRanks();
      }
      return;
    }
    const ids = rows.map((p) => String(p.id)).join(",");
    if (host.dataset.ids === ids && host.querySelector(".rank[data-id]")) {
      rows.forEach((p, i) => patchRank(host.querySelector(`[data-id="${p.id}"]`), p, i));
      return;
    }
    host.className = "";
    host.dataset.ids = ids;
    const head = `<div class="rank-head">
      <span></span>
      <span></span>
      <span><span class="lbl">AD</span><span class="gloss">${esc(GLOSS.AD)}</span></span>
      <span><span class="lbl">LAST</span><span class="gloss">${esc(GLOSS.LAST)}</span></span>
      <span><span class="lbl">NEXT</span><span class="gloss">${esc(GLOSS.NEXT)}</span></span>
      <span><span class="lbl">REDS</span><span class="gloss">${esc(GLOSS.REDS)}</span></span>
      <span><span class="lbl">VOL</span><span class="gloss">${esc(GLOSS.VOL)}</span></span>
      <span><span class="lbl">NEWS</span><span class="gloss">${esc(GLOSS.NEWS)}</span></span>
      <span><span class="lbl">REST</span><span class="gloss">${esc(GLOSS.REST)}</span></span>
    </div>`;
    host.innerHTML =
      head +
      rows
        .map((p, i) => {
          const killed = p.status === "killed" ? " killed" : "";
          return `<button type="button" class="rank is-enter${killed}" data-id="${p.id}">
          <span class="idx">${String(i + 1).padStart(2, "0")}</span>
          <span class="who"><span class="nm">${esc(instrument(p))}${esc(fut(p))}</span><span class="tf">${tfText(p)}</span></span>
          <span class="ad">${rankAd(p)}</span>
          <span class="last">${lastText(p)}</span>
          <span class="next">${money(p.next_layer_usd)}</span>
          <span class="reds">${redsText(p)}<span class="pips">${pips(p.reds)}</span></span>
          <span class="vol">${volText(p) || "—"}</span>
          <span class="news${p.news ? " news-hot" : ""}">${newsText(p)}</span>
          <span class="rest">${restClock(p) || "—"}</span>
          ${whyLine(p)}
        </button>`;
        })
        .join("");
    setTimeout(() => {
      host.querySelectorAll(".rank.is-enter").forEach((el) => el.classList.remove("is-enter"));
    }, 220);
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
    const adKnown = p.ad_status === "known";
    const layers = adKnown ? p.layers || [] : [];
    let ladder;
    if (!adKnown) {
      ladder = `<p class="ladder-empty">unknown — no layers</p>`;
    } else if (!layers.length) {
      ladder = `<p class="ladder-empty">unknown — no layers</p>`;
    } else {
      ladder = `<ol class="ladder">${layers
        .map((L) => {
          const cls = L.next ? " next" : "";
          const mark = L.next ? " · next" : "";
          return `<li class="${cls}">${esc(layerLine(L))}${mark}</li>`;
        })
        .join("")}</ol>`;
    }
    host.innerHTML = `
      <p class="plan-kicker"><span>Plan</span><button type="button" class="sheet-x" id="sheetClose">Close</button></p>
      <h3>${esc(instrument(p))}${esc(fut(p))}</h3>
      ${p.decision ? `<p class="why">${esc(p.decision)}</p>` : ""}
      <dl>
        <div class="sheet-row"><dt>TF</dt><dd>${tfText(p)}</dd></div>
        <div class="sheet-row ad"><dt>AD</dt><dd>${
          adKnown ? `${adTop(p)} → ${adBot(p)}` : "unknown"
        }<span class="gloss">${esc(GLOSS.AD)}</span></dd></div>
        <div class="sheet-row"><dt>LAST</dt><dd>${lastText(p)}<span class="gloss">${esc(GLOSS.LAST)}</span></dd></div>
        <div class="sheet-row"><dt>NEXT</dt><dd>${money(p.next_layer_usd)}<span class="gloss">${esc(GLOSS.NEXT)}</span></dd></div>
        <div class="sheet-row"><dt>REDS</dt><dd>${redsText(p)} <span class="pips">${pips(p.reds)}</span><span class="gloss">${esc(GLOSS.REDS)}</span></dd></div>
        <div class="sheet-row"><dt>VOL</dt><dd>${volText(p) || "—"}<span class="gloss">${esc(GLOSS.VOL)}</span></dd></div>
        <div class="sheet-row"><dt>NEWS</dt><dd>${newsText(p)}<span class="gloss">${esc(GLOSS.NEWS)}</span></dd></div>
        <div class="sheet-row"><dt>REST</dt><dd>${restClock(p) || "—"}<span class="gloss">${esc(GLOSS.REST)}</span></dd></div>
        <div class="sheet-row"><dt>LINE</dt><dd>${linePrice(p)}<span class="gloss">${esc(GLOSS.LINE)}</span></dd></div>
        <div class="sheet-row"><dt>Source</dt><dd>${esc(p.ad_source || "unknown")}</dd></div>
        <div class="sheet-row"><dt>Top bar</dt><dd>${esc(p.bar_top_label)}</dd></div>
        <div class="sheet-row"><dt>Bottom bar</dt><dd>${esc(p.bar_bottom_label)}</dd></div>
      </dl>
      <h4 class="ladder-h">Layers</h4>
      ${ladder}
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

  function recutDirty() {
    const f = $("#recutForm");
    if (!f) return false;
    const a = document.activeElement;
    if (a && f.contains(a) && (a.tagName === "INPUT" || a.tagName === "TEXTAREA")) {
      return true;
    }
    const top = f.querySelector("[name=ad_top]");
    const bot = f.querySelector("[name=ad_bottom]");
    const left = f.querySelector("[name=remaining_layers]");
    const p = state.plans.find((x) => x.id === state.selected);
    if (!p) return false;
    const curTop = String(p.ad_top ?? "");
    const curBot = String(p.ad_bottom ?? "");
    const curLeft = String(p.remaining_layers || 5);
    return (
      (top && top.value !== curTop) ||
      (bot && bot.value !== curBot) ||
      (left && left.value !== curLeft)
    );
  }

  function patchSheet(p) {
    const host = $("#planSheet");
    if (!p || host.hidden || recutDirty()) return;
    const setDd = (dt, html) => {
      const row = [...host.querySelectorAll(".sheet-row")].find(
        (r) => r.querySelector("dt") && r.querySelector("dt").textContent === dt
      );
      const dd = row && row.querySelector("dd");
      if (!dd) return;
      const gloss = dd.querySelector(".gloss");
      const g = gloss ? gloss.outerHTML : "";
      const pipsEl = dd.querySelector(".pips");
      if (dt === "LAST") dd.innerHTML = lastText(p) + g;
      else if (dt === "REST") dd.innerHTML = (restClock(p) || "—") + g;
      else if (dt === "REDS")
        dd.innerHTML = `${redsText(p)} <span class="pips">${pips(p.reds)}</span>${g}`;
      else if (dt === "VOL") dd.innerHTML = (volText(p) || "—") + g;
      else if (dt === "NEWS") dd.innerHTML = newsText(p) + g;
      else if (dt === "NEXT") dd.innerHTML = money(p.next_layer_usd) + g;
      else if (dt === "LINE") dd.innerHTML = linePrice(p) + g;
      void pipsEl;
    };
    setDd("LAST");
    setDd("REST");
    setDd("REDS");
    setDd("VOL");
    setDd("NEWS");
    setDd("NEXT");
    setDd("LINE");
  }

  function paint(opts) {
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
      if (p) {
        if (opts && opts.refreshSheet) renderSheet(p);
        else patchSheet(p);
      }
    }
  }

  async function load(opts) {
    const data = await api("/api/machine/plans");
    state.plans = data.plans || [];
    state.needs = data.needs_you || [];
    state.room = data.room || {};
    state.account = data.account || {};
    paint(opts);
  }

  function onPick(e) {
    if (e.target.closest("[data-kill]")) return;
    const btn = e.target.closest("[data-id]");
    if (!btn || btn.classList.contains("open") || btn.classList.contains("skeleton")) return;
    select(btn.getAttribute("data-id"));
  }

  async function killPlan(id) {
    if (killPlan._busy) return;
    killPlan._busy = true;
    const btns = document.querySelectorAll("[data-kill], #btnKill");
    btns.forEach((b) => {
      b.disabled = true;
    });
    try {
      await api(`/api/machine/plans/${id}/kill`, { method: "POST", body: "{}" });
      toast("Killed");
      closeSheet();
      await load();
    } finally {
      killPlan._busy = false;
      btns.forEach((b) => {
        b.disabled = false;
      });
    }
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
    if (e.target._busy) return;
    e.target._busy = true;
    const fd = new FormData(e.target);
    const sub = e.target.querySelector("[type=submit]");
    if (sub) sub.disabled = true;
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
      await load({ refreshSheet: true });
    } catch (err) {
      toast(err.message);
    } finally {
      e.target._busy = false;
      if (sub) sub.disabled = false;
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
  setInterval(() => {
    load().catch(() => {});
  }, 2000);
})();
