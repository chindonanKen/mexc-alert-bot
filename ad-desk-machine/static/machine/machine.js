(function () {
  const token = window.MACHINE_TOKEN || localStorage.getItem("MACHINE_TOKEN") || "";

  function headers() {
    return token ? { Authorization: "Bearer " + token } : {};
  }

  function fmt(n, d) {
    if (n == null || n === "") return "—";
    const x = Number(n);
    if (!Number.isFinite(x)) return "—";
    return x.toFixed(d == null ? 6 : d);
  }

  function chips(layers, cls) {
    return (layers || [])
      .map(function (L) {
        return (
          '<span class="chip ' +
          cls +
          '">' +
          fmt(L.price, 6) +
          (L.pct != null ? " · " + L.pct + "%" : L.half_pct != null ? " · " + L.half_pct + "% half" : "") +
          "</span>"
        );
      })
      .join("");
  }

  function render(data) {
    const board = document.getElementById("board");
    const plans = (data && data.hung_plans) || [];
    if (!plans.length) {
      board.textContent = "No hung plans.";
      return;
    }
    board.innerHTML = plans
      .map(function (p) {
        return (
          '<article class="card">' +
          "<h2>" +
          (p.symbol || p.id) +
          " " +
          (p.tf || "") +
          "</h2>" +
          '<div class="meta">current price ' +
          fmt(p.current_price) +
          " · T " +
          fmt(p.ad_top) +
          " · B " +
          fmt(p.ad_bottom) +
          (p.met ? ' · <span class="flag">MET</span>' : "") +
          (p.habit_ready ? " · habit ready" : "") +
          (p.watch_only ? " · watch only" : "") +
          "</div>" +
          '<div class="layers"><strong>buy layers</strong> ' +
          chips(p.buy_layers, "buy") +
          "</div>" +
          '<div class="layers"><strong>sell layers</strong> ' +
          chips(p.sell_layers, "sell") +
          "</div>" +
          "</article>"
        );
      })
      .join("");
  }

  fetch("/plays", { headers: headers() })
    .then(function (r) {
      if (r.status === 401) throw new Error("Set MACHINE_TOKEN in localStorage.");
      return r.json();
    })
    .then(render)
    .catch(function (err) {
      document.getElementById("board").textContent = String(err.message || err);
    });
})();
