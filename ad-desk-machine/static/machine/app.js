/* Machine page — SPEC-uncluster spirit. PRICE not last. Empty OUT allowed. */
(function () {
  const hdr = { Accept: "application/json" };
  let pollTimer = null;

  function showGate(msg) {
    const gate = document.getElementById("gate");
    const err = document.getElementById("gate-err");
    const out = document.getElementById("sign-out");
    if (gate) gate.classList.remove("hidden");
    if (out) out.classList.add("hidden");
    if (err) {
      if (msg) {
        err.textContent = msg;
        err.classList.remove("hidden");
      } else {
        err.textContent = "";
        err.classList.add("hidden");
      }
    }
  }

  function hideGate() {
    const gate = document.getElementById("gate");
    const out = document.getElementById("sign-out");
    if (gate) gate.classList.add("hidden");
    if (out) out.classList.remove("hidden");
  }

  // SPEC-uncluster decision list only — no enter/exit/miss or grind/panic aliases
  const TAPE_OK = new Set([
    "paper-buy", "paper-sell", "sit-out", "add-panic", "flatten-news",
    "recut", "kill", "met", "sell-layers", "board-grind", "board-panic"
  ]);

  let plans = [];
  let selected = null;

  async function api(path, opts) {
    const r = await fetch(path, Object.assign({
      headers: hdr,
      credentials: "same-origin",
    }, opts || {}));
    if (r.status === 401) {
      showGate();
      throw new Error(path + " 401");
    }
    if (!r.ok) throw new Error(path + " " + r.status);
    return r.json();
  }

  function fmtPx(p) {
    if (p == null) return "—";
    const n = Number(p);
    if (n >= 1) return n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
    return n.toPrecision(4);
  }

  // Exit-why thin paint: color kind word only. No sound on bounce kinds.
  const BOUNCE_KIND_CLASS = {
    GOOD: "bounce-good",
    WEAK: "bounce-weak",
    FAIL: "bounce-fail",
    TOO_EARLY: "bounce-early",
  };


  function fmtVol(v) {
    if (v == null || v === "" || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(2).replace(/\.?0+$/, "") + "M";
    if (n >= 1e3) return "$" + Math.round(n).toLocaleString("en-US");
    return "$" + Math.round(n);
  }

  function redsVolLine(p) {
    // Sheet only on watch/met/live. Hide on out/killed.
    if (p.state === "out" || p.killed) return "";
    const ctf = p.tf || "—";
    const cn = (p.chosen_tf_reds == null) ? "—" : String(p.chosen_tf_reds);
    const ftf = p.faster_tf || "—";
    const fn = (p.faster_tf_reds == null) ? "—" : String(p.faster_tf_reds);
    const vol = fmtVol(p.vol_usd);
    return '<div class="reds-vol">' + ctf + ' ' + cn + ' red · ' + ftf + ' ' + fn + ' red · ' + vol + '</div>';
  }

  function bounceKindHtml(kind) {
    const label = kind || "—";
    const cls = BOUNCE_KIND_CLASS[kind] || "bounce-mute";
    return '<span class="bounce-label">BOUNCE</span> · <span class="' + cls + '">' + label + '</span>';
  }

  function exitWhyBlock(p) {
    // Sheet only when live with sell layers. Empty sell layers stay mute.
    const sells = (p.sell_layers || []).filter(s => !s.status || s.status === "remaining" || s.status === "working");
    if (p.state !== "live" || !sells.length) return "";
    const sellWhy = (p.last_sell_why != null && p.last_sell_why !== "")
      ? String(p.last_sell_why)
      : "no sell yet";
    const sellCls = (p.last_sell_why != null && p.last_sell_why !== "") ? "exit-sell-why" : "exit-sell-mute";
    return '<div class="exit-why">' +
      '<div class="bounce-line">' + bounceKindHtml(p.bounce_kind) + '</div>' +
      '<div class="' + sellCls + '">' + sellWhy + '</div>' +
      '</div>';
  }


  function paintSlots(live) {
    for (let i = 0; i < 2; i++) {
      const el = document.getElementById("slot" + i);
      const p = live[i];
      if (!p) {
        el.className = "slot ghost";
        el.innerHTML = '<div class="slot-label">SLOT ' + (i + 1) + '</div><div class="open">open book</div>';
        continue;
      }
      const sells = (p.sell_layers || []).length;
      const buys = (p.layers || []).filter(l => l.status === "empty" || l.status === "next");
      const nextBuy = buys[0];
      const outLine = sells ? ("OUT " + sells + " left") : "OUT no sell layers";
      const inLine = nextBuy ? ("IN $" + nextBuy.usd + " next") : "IN flat";
      el.className = "slot occupied";
      el.innerHTML =
        '<div><span class="name">' + p.name + '</span><span class="tf">' + p.tf + '</span></div>' +
        '<div class="price">' + fmtPx(p.price) + '</div>' +
        '<div class="ad">' + fmtPx(p.ad_top) + '<br>' + fmtPx(p.ad_bottom) + '</div>' +
        '<div class="meta">' + inLine + ' · ' + outLine + '</div>' +
        '<div class="recut"><span class="line">LINE</span><span>' + buys.length + ' in</span><span>' +
        (sells ? sells + " out" : "—") + '</span><span class="kill">KILL</span></div>';
    }
  }

  function rankedRedsVol(p) {
    if (p.state === "out" || p.killed) return "";
    const ctf = p.tf || "—";
    const cn = (p.chosen_tf_reds == null) ? "—" : String(p.chosen_tf_reds);
    const ftf = p.faster_tf || "—";
    const fn = (p.faster_tf_reds == null) ? "—" : String(p.faster_tf_reds);
    return ctf + " " + cn + " red · " + ftf + " " + fn + " red · " + fmtVol(p.vol_usd);
  }

  function paintRanked(rows) {
    const box = document.getElementById("ranked-rows");
    box.innerHTML = "";
    rows.forEach((p, i) => {
      const rank = String(p.rank || i + 1).padStart(2, "0");
      const row = document.createElement("div");
      row.className = "rank-row";
      const reds = rankedRedsVol(p);
      // Kenneth hang: 36px strip — name·tf · PRICE · B · reds/$vol · state · next
      row.innerHTML =
        '<div class="rank">' + rank + '</div>' +
        '<div class="nm">' + p.name + ' <span class="tf">' + p.tf + '</span></div>' +
        '<div class="px">' + fmtPx(p.price) + '</div>' +
        '<div class="b">' + fmtPx(p.ad_bottom) + '</div>' +
        '<div class="rv">' + (reds || "") + '</div>' +
        '<div class="st ' + p.state + '">' + p.state + '</div>' +
        '<div class="nx">' + (p.next || "—") + '</div>';
      row.addEventListener("click", async () => {
        try {
          const detail = await api("/api/machine/plans/" + encodeURIComponent(p.id));
          openSheet(detail);
        } catch (err) {
          openSheet(p);
        }
      });
      box.appendChild(row);
    });
  }

  function openSheet(p) {
    selected = p.id;
    const sheet = document.getElementById("sheet");
    sheet.classList.remove("hidden");
    // When exit-why is on the head (live + sell layers), mute general why —
    // sell why is the only why there (no buy/sit restatement).
    const exitWhy = exitWhyBlock(p);
    const generalWhy = exitWhy
      ? ""
      : '<div class="why">' + (p.why || "") + '</div>';
    document.getElementById("sheet-head").innerHTML =
      '<div class="name">' + p.name + ' · ' + p.tf + '</div>' +
      '<div class="price">' + fmtPx(p.price) + '</div>' +
      '<div class="ad">' + fmtPx(p.ad_top) + '<br>' + fmtPx(p.ad_bottom) + '</div>' +
      redsVolLine(p) +
      exitWhy +
      generalWhy;

    const inEl = document.getElementById("sheet-in");
    // SPEC: remaining buy layers only (next/empty). Filled/cancelled recede.
    const showIn = (p.layers || []).filter(l => l.status === "empty" || l.status === "next");
    if (!showIn.length) {
      inEl.innerHTML = '<div class="empty">no buy layers</div>';
    } else {
      const ordered = showIn.slice().sort((a, b) => {
        const ra = a.role === "AD" ? 0 : 1;
        const rb = b.role === "AD" ? 0 : 1;
        return ra - rb || a.idx - b.idx;
      });
      inEl.innerHTML = ordered.map(l => {
        const st = l.status === "next" ? "next" : l.status;
        return '<div class="layer">' + fmtPx(l.price) + ' · $' + l.usd + ' · ' + l.role + ' · ' + st + '</div>';
      }).join("");
    }

    // SPEC: remaining sell layers only. Filled/cancelled recede. Empty allowed.
    const sells = (p.sell_layers || []).filter(s => !s.status || s.status === "remaining" || s.status === "working");
    const outEl = document.getElementById("sheet-out");
    if (!sells.length) {
      outEl.innerHTML = '<div class="empty">no sell layers yet</div>';
    } else {
      outEl.innerHTML = sells.map(s => {
        const why = (s.why || "").replace(/_/g, " ");
        return '<div class="layer">' + fmtPx(s.price) + ' · $' + s.usd + ' · ' + why + ' · remaining</div>';
      }).join("");
    }

    const remBuys = showIn.length;
    document.getElementById("sheet-foot").innerHTML =
      '<span class="line">LINE</span><span>' + remBuys + ' in</span><span>' +
      (sells.length ? sells.length + " out" : "—") + '</span><span class="kill">KILL</span>';
  }

  function paintTape(log) {
    const box = document.getElementById("tape-rows");
    const rows = (log || []).filter(e => TAPE_OK.has(e.action)).slice(-8).reverse();
    box.innerHTML = rows.map(e => {
      const name = e.name ? (" " + e.name) : "";
      const px = e.price != null ? (" · " + fmtPx(e.price)) : "";
      const sz = e.size_pct != null ? (" · " + e.size_pct + "%") : "";
      return '<div class="tape-row"><span class="t">' + (e.manila || "") +
        '</span><span class="a">' + e.action + '</span>' + name + px + sz +
        ' · ' + (e.why || "") + '</div>';
    }).join("");
  }

  function paintNeeds(rows) {
    const sec = document.getElementById("needs");
    if (!rows || !rows.length) {
      sec.classList.add("hidden");
      return;
    }
    sec.classList.remove("hidden");
    document.getElementById("needs-body").innerHTML = rows.map(r =>
      '<div>' + (r.name || "") + " — " + (r.why || "") + "</div>"
    ).join("");
  }

  async function refresh() {
    try {
      const [st, pl, lg, ny] = await Promise.all([
        api("/api/machine/status"),
        api("/api/machine/plans"),
        api("/api/machine/log"),
        api("/api/machine/needs-you"),
      ]);
      document.getElementById("book").textContent = st.book_usd;
      document.getElementById("liveN").textContent = st.live_count;
      plans = pl.plans || [];
      const live = plans.filter(p => p.state === "live").slice(0, 2);
      paintSlots(live);
      paintRanked(plans);
      paintTape(lg.log || []);
      paintNeeds(ny.needs_you || []);
      if (selected) {
        try {
          const detail = await api("/api/machine/plans/" + encodeURIComponent(selected));
          openSheet(detail);
        } catch (err) {
          const p = plans.find(x => x.id === selected);
          if (p) openSheet(p);
        }
      }
    } catch (e) {
      console.warn(e);
    }
  }

  function startPoll() {
    if (pollTimer) return;
    // Slow poll — tape only changes on decisions; page does not spam
    pollTimer = setInterval(refresh, 5000);
  }

  async function boot() {
    hideGate();
    await refresh();
    startPoll();
  }

  document.getElementById("sheet-close").addEventListener("click", () => {
    document.getElementById("sheet").classList.add("hidden");
    selected = null;
  });

  document.getElementById("gate-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("gate-token");
    const token = (input && input.value) ? input.value : "";
    try {
      const r = await fetch("/api/machine/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ token: token }),
      });
      if (input) input.value = "";
      if (!r.ok) {
        showGate("That token was not accepted.");
        return;
      }
      await boot();
    } catch (e) {
      if (input) input.value = "";
      showGate("Could not reach the Machine.");
    }
  });

  document.getElementById("sign-out").addEventListener("click", async () => {
    try {
      await fetch("/api/machine/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
    } catch (e) { /* still lock the page */ }
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    showGate();
  });

  api("/api/machine/status").then(function () {
    return boot();
  }).catch(function () {
    showGate();
  });
})();
