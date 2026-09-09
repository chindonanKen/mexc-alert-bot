
(function () {
  const data = window.BRAIN_MAP;
  if (!data) return;
  const svg = document.getElementById("brain");
  const tip = document.getElementById("tip");
  const sheet = document.getElementById("sheet");
  const sheetBody = document.getElementById("sheet-body");
  const sheetClose = document.getElementById("sheet-close");
  const upgrade = document.getElementById("upgrade");
  const legend = document.getElementById("legend");
  const byId = {};
  (data.layers || []).forEach(function (l) { byId[l.id] = l; });

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function tagChip(tag) {
    return tag === "kenneth_locked"
      ? '<span class="tag tag-locked">Kenneth-locked</span>'
      : '<span class="tag tag-proposed">Staff-proposed</span>';
  }
  function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
  function edgePath(a, b, kind, waypoints) {
    if (waypoints && waypoints.length) {
      var d = "M " + a.x + " " + a.y;
      waypoints.forEach(function (p) { d += " L " + p[0] + " " + p[1]; });
      d += " L " + b.x + " " + b.y;
      return d;
    }
    const dx = b.x - a.x, dy = b.y - a.y;
    if (kind === "conflict") {
      const mx = (a.x + b.x) / 2 + 36, my = (a.y + b.y) / 2;
      return "M " + a.x + " " + a.y + " Q " + mx + " " + my + " " + b.x + " " + b.y;
    }
    if (kind === "takeover") {
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2 - 28;
      return "M " + a.x + " " + a.y + " Q " + mx + " " + my + " " + b.x + " " + b.y;
    }
    if (Math.abs(dx) < 8 || Math.abs(dy) < 8) {
      return "M " + a.x + " " + a.y + " L " + b.x + " " + b.y;
    }
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    return "M " + a.x + " " + a.y + " Q " + mx + " " + (my + (dy > 0 ? 10 : -10)) + " " + b.x + " " + b.y;
  }
  function labelPoint(a, b, kind, waypoints) {
    if (waypoints && waypoints.length) {
      var midWp = waypoints[Math.floor((waypoints.length - 1) / 2)];
      return { x: midWp[0] + (kind === "refuse" ? 14 : 0), y: midWp[1] - 8 };
    }
    var m = mid(a, b);
    if (kind === "conflict") return { x: m.x + 18, y: m.y - 6 };
    return { x: m.x, y: m.y - 8 };
  }

  function draw() {
    const defs =
      '<defs>' +
      ['handoff','takeover','feeds','conflict','refuse'].map(function (k) {
        return '<marker id="arrow-' + k + '" class="marker ' + k + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker>';
      }).join("") +
      "</defs>";
    let edges = '<g id="edges">';
    (data.edges || []).forEach(function (e, i) {
      const a = byId[e.from], b = byId[e.to];
      if (!a || !b) return;
      const d = edgePath(a, b, e.kind, e.waypoints);
      const m = mid(a, b);
      const lp = labelPoint(a, b, e.kind, e.waypoints);
      const marker = e.kind === "conflict" ? "" : ' marker-end="url(#arrow-' + e.kind + ')"';
      edges += '<path class="edge ' + e.kind + '" data-i="' + i + '" data-from="' + e.from + '" data-to="' + e.to + '" d="' + d + '"' + marker + "/>";
      edges += '<text class="edge-label ' + (e.kind === "conflict" || e.kind === "refuse" ? e.kind : "") + '" x="' + lp.x + '" y="' + lp.y + '">' + esc(e.label || "") + "</text>";
      if (e.kind === "conflict") {
        edges += '<text class="conflict-x" x="' + m.x + '" y="' + (m.y + 5) + '">╳</text>';
      }
      if (e.kind === "refuse" && e.waypoints && e.waypoints.length) {
        var wx = e.waypoints[Math.floor(e.waypoints.length / 2)][0];
        var wy = e.waypoints[Math.floor(e.waypoints.length / 2)][1];
        edges += '<text class="refuse-x" x="' + wx + '" y="' + (wy + 4) + '">╳</text>';
      }
    });
    edges += "</g>";

    let nodes = '<g id="nodes">';
    (data.layers || []).forEach(function (l) {
      const locked = l.tag === "kenneth_locked";
      const cls = locked ? "locked" : "proposed";
      const pip = locked ? "locked" : "proposed";
      const wide = (l.name || "").length > 12;
      const w = wide ? 150 : 140;
      const hx = w / 2;
      nodes +=
        '<g class="node-hit" data-seat="' + l.id + '" transform="translate(' + l.x + " " + l.y + ')">' +
          '<rect class="node-body ' + cls + '" x="' + (-hx) + '" y="-28" rx="18" ry="18" width="' + w + '" height="56"/>' +
          '<circle class="pip ' + pip + '" cx="' + (hx - 12) + '" cy="-18" r="4"/>' +
          '<text class="node-label" x="0" y="-2">' + esc(l.name) + "</text>" +
          '<text class="node-when" x="0" y="16">' + esc(l.when || "") + "</text>" +
        "</g>";
    });
    nodes += "</g>";
    svg.innerHTML = defs + edges + nodes;

    svg.querySelectorAll(".node-hit").forEach(function (g) {
      const id = g.getAttribute("data-seat");
      const layer = byId[id];
      g.addEventListener("mouseenter", function (ev) { showTip(layer, ev); highlight(id, true); });
      g.addEventListener("mousemove", function (ev) { placeTip(ev); });
      g.addEventListener("mouseleave", function () { hideTip(); highlight(id, false); });
      g.addEventListener("click", function () { openSheet(layer); });
    });
  }

  function highlight(id, on) {
    svg.querySelectorAll(".edge").forEach(function (p) {
      const hit = p.getAttribute("data-from") === id || p.getAttribute("data-to") === id;
      p.classList.toggle("hot", on && hit);
    });
    svg.querySelectorAll(".node-hit").forEach(function (g) {
      const body = g.querySelector(".node-body");
      body.classList.toggle("hot", on && g.getAttribute("data-seat") === id);
    });
  }

  function showTip(layer, ev) {
    tip.classList.remove("hidden");
    const verbs = (layer.prints || []).map(function (p) { return p.verb; }).join(" · ") || (layer.when || "");
    const shape = (layer.prints && layer.prints[0] && layer.prints[0].shape) || "";
    tip.innerHTML =
      '<div class="t-name">' + esc(layer.name) + "</div>" +
      '<div class="t-owns">' + esc(verbs) + "</div>" +
      (shape ? '<div class="t-owns" style="margin-top:6px">' + esc(shape) + "</div>" : "");
    placeTip(ev);
  }
  function placeTip(ev) {
    const stage = document.getElementById("stage").getBoundingClientRect();
    tip.style.left = (ev.clientX - stage.left + 14) + "px";
    tip.style.top = (ev.clientY - stage.top + 14) + "px";
  }
  function hideTip() { tip.classList.add("hidden"); }

  function openSheet(layer) {
    document.body.classList.add("sheet-open");
    sheet.classList.remove("hidden");
    const rules = layer.rules || [];
    const prints = layer.prints || [];
    const edges = (data.edges || []).filter(function (e) { return e.from === layer.id || e.to === layer.id; });
    function printsBlock(list) {
      if (!list.length) return '<div class="empty">none yet</div>';
      return list.map(function (p) {
        return (
          '<div class="rule-row">' +
            '<span class="tag tag-proposed">' + esc(p.verb) + "</span>" +
            '<div class="rule-text">' + esc(p.when || "") + "<br>" + esc(p.shape || "") + "</div>" +
          "</div>"
        );
      }).join("");
    }
    function rulesBlock(list) {
      if (!list.length) return '<div class="empty">none yet</div>';
      return list.map(function (r) {
        const date = r.kenneth_date ? '<span class="rule-date">Kenneth ' + esc(r.kenneth_date) + "</span>" : "";
        return '<div class="rule-row">' + tagChip(r.tag) + '<div class="rule-text">' + esc(r.text) + date + "</div></div>";
      }).join("");
    }
    function modsBlock(mods) {
      if (!mods || !mods.length) return '<div class="empty">none yet</div>';
      return mods.map(function (m) {
        return '<div class="mod-row"><span class="sym">' + esc(m.path) + "</span>" + (m.symbols ? " · " + esc(m.symbols) : "") + "</div>";
      }).join("");
    }
    function scenBlock(ids) {
      if (!ids || !ids.length) return '<div class="empty">none yet</div>';
      return '<div class="scen-row">' + ids.map(esc).join(" · ") + "</div>";
    }
    function edgeBlock(list) {
      if (!list.length) return '<div class="empty">none yet</div>';
      return list.map(function (e) {
        return '<div class="edge-row"><span class="sym">' + esc(e.from) + " → " + esc(e.to) + "</span> · " + esc(e.kind) + " · " + esc(e.label || "") + "</div>";
      }).join("");
    }
    const ownsMute = layer.owns
      ? '<div class="owns" style="opacity:.65">' + esc(layer.owns) + "</div>"
      : "";
    sheetBody.innerHTML =
      '<div id="sheet-head"><div class="name">' + esc(layer.name) + " " + tagChip(layer.tag) + '</div>' +
      '<div class="when">' + esc(layer.when || "") + "</div>" +
      ownsMute + "</div>" +
      '<div class="block"><div class="kicker">' + (["lock_pass","hang","outcome_writeback"].indexOf(layer.id) >= 0 ? "Gate" : "Decision prints") + '</div>' + printsBlock(prints) + "</div>" +
      '<div class="block"><div class="kicker">Rules</div>' + rulesBlock(rules) + "</div>" +
      '<div class="block"><div class="kicker">Code modules</div>' + modsBlock(layer.modules) + "</div>" +
      '<div class="block"><div class="kicker">Scenario seats</div>' + scenBlock(layer.scenarios) + "</div>" +
      '<div class="block"><div class="kicker">Edges</div>' + edgeBlock(edges) + "</div>" +
      '<div id="sheet-foot">Close</div>';
    document.getElementById("sheet-foot").addEventListener("click", closeSheet);
  }

function closeSheet() {
    sheet.classList.add("hidden");
    document.body.classList.remove("sheet-open");
  }

  function renderUpgrade() {
    const items = data.upgrades || [];
    const help = data.upgrade_help || "suggestions stay staff-proposed until Kenneth locks";
    const cards = items.length
      ? items.map(function (u) {
          return '<div class="upgrade-card"><div class="title">' + esc(u.title) + " " + tagChip("staff_proposed") + '</div><div class="body">' + esc(u.text) + "</div></div>";
        }).join("")
      : '<div class="empty">none yet</div>';
    upgrade.innerHTML = '<div class="kicker">Upgrade</div><div class="help">' + esc(help) + "</div>" + cards;
  }

  legend.textContent = data.legend || "";
  sheetClose.addEventListener("click", closeSheet);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSheet(); });
  draw();
  renderUpgrade();
})();
