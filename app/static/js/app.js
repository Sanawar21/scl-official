/* Shared helpers + live-update renderers for viewer and manager pages. */
(function () {
  "use strict";

  /* ---------- toasts (flash -> toast) ---------- */
  function initToasts() {
    const wrap = document.getElementById("toast-wrap");
    if (!wrap) return;
    wrap.querySelectorAll(".toast").forEach(function (t) {
      setTimeout(function () { t.classList.add("show"); }, 30);
      const dismiss = function () {
        t.classList.remove("show");
        setTimeout(function () { t.remove(); }, 250);
      };
      t.addEventListener("click", dismiss);
      setTimeout(dismiss, 5000);
    });
  }

  /* ---------- mobile drawer ---------- */
  function initDrawer() {
    const toggle = document.getElementById("nav-toggle");
    const drawer = document.getElementById("drawer");
    const backdrop = document.getElementById("drawer-backdrop");
    const closeBtn = document.getElementById("drawer-close");
    if (!toggle || !drawer) return;
    function open() {
      drawer.classList.add("open");
      if (backdrop) backdrop.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    }
    function close() {
      drawer.classList.remove("open");
      if (backdrop) backdrop.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }
    toggle.addEventListener("click", open);
    if (closeBtn) closeBtn.addEventListener("click", close);
    if (backdrop) backdrop.addEventListener("click", close);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function el(id) { return document.getElementById(id); }

  function renderBudget(state) {
    const cards = el("budget-cards");
    const tbody = el("budget-board");
    if (!cards && !tbody) return;
    const takeover = function (t) {
      return t.control_status === "admin_takeover"
        ? ' <span class="chip chip-danger">admin</span>' : "";
    };
    if (cards) {
      cards.innerHTML = state.public_budget_board.map(function (t) {
        return '<div class="stat-tile">' +
          '<div class="stat-label">' + esc(t.team_name) + takeover(t) + "</div>" +
          '<div class="stat-value">' + esc(t.purse_remaining) + "</div>" +
          '<div class="small muted">' + esc(t.credits_remaining) + " credits · " +
          esc(t.active_count) + " XI / " + esc(t.bench_count) + " bench</div>" +
          "</div>";
      }).join("");
    }
    if (tbody) {
      tbody.innerHTML = state.public_budget_board.map(function (t) {
        return "<tr>" +
          "<td>" + esc(t.team_name) + takeover(t) + "</td>" +
          "<td>" + esc(t.purse_remaining) + "</td>" +
          "<td>" + esc(t.credits_remaining) + "</td>" +
          "<td>" + esc(t.active_count) + "</td>" +
          "<td>" + esc(t.bench_count) + "</td>" +
          "</tr>";
      }).join("");
    }
  }

  function renderLot(state) {
    const box = el("current-lot");
    if (!box) return;
    const p = state.current_player;
    if (!p) { box.innerHTML = '<div class="empty"><div class="empty-title">No player nominated</div><p class="small">The admin hasn’t opened a lot yet.</p></div>'; return; }
    const bidder = p.current_bidder_team_name && p.current_bidder_team_name !== "-"
      ? ' <span class="muted">by ' + esc(p.current_bidder_team_name) + "</span>" : "";
    box.innerHTML =
      '<div class="lot-box">' +
      '<div class="lot-name">' + esc(p.name) + " <span class='tag tag-active'>" + esc(p.tier) + "</span></div>" +
      '<div class="lot-bid">Current bid: <strong>' + esc(p.current_bid) + "</strong>" + bidder + "</div>" +
      '<div class="lot-base">Base: ' + esc(p.base_price) + " · Credits: " + esc(p.credits) + "</div>" +
      "</div>";
  }

  function renderFeed(state) {
    const feed = el("bid-feed");
    if (!feed) return;
    feed.innerHTML = state.bids.slice(0, 25).map(function (b) {
      const label = b.kind === "pass" ? "pass" : b.amount;
      return "<li><span class='chip'>" + esc(b.team_name) + "</span> " +
        esc(b.player_name) + " — " + esc(label) +
        ' <span class="muted">' + esc(b.ts_display) + "</span></li>";
    }).join("") || '<li class="muted">No bids yet.</li>';
  }

  function renderLotBids(state) {
    const feed = el("lot-bids");
    if (!feed) return;
    feed.innerHTML = state.current_lot_bids.map(function (b) {
      const label = b.kind === "pass" ? "pass" : b.amount;
      return "<li><span class='chip'>" + esc(b.team_name) + "</span> " + esc(label) +
        ' <span class="muted">' + esc(b.ts_display) + "</span></li>";
    }).join("") || '<li class="muted">No bids on this lot yet.</li>';
  }

  function phaseLabel(phase) {
    const m = phase.match(/^phase_a_(.+)$/);
    if (m) return m[1].charAt(0).toUpperCase() + m[1].slice(1);
    if (phase === "break") return "Trade break";
    if (phase === "phase_b") return "Phase B";
    return phase.charAt(0).toUpperCase() + phase.slice(1);
  }

  function renderPhase(state) {
    const stepper = el("phase-stepper");
    if (!stepper) return;
    const flow = state.flow || [];
    const current = state.phase;
    let html = "<div class='stepper'><ol>";
    flow.forEach(function (ph) {
      const idx = flow.indexOf(ph);
      const curIdx = flow.indexOf(current);
      let cls = "step";
      if (idx === curIdx) cls += " current";
      else if (idx < curIdx) cls += " done";
      html += "<li class='" + cls + "'><span class='step-dot'></span><span class='step-label'>" +
        esc(phaseLabel(ph)) + "</span></li>";
    });
    if (current === "complete") html += "<li class='step current'><span class='step-dot'></span><span class='step-label'>Complete</span></li>";
    else if (current === "transfers_open") html += "<li class='step current'><span class='step-dot'></span><span class='step-label'>Transfers</span></li>";
    html += "</ol></div>";
    stepper.innerHTML = html;
    const read = el("phase-readiness");
    if (read && current === "phase_b") {
      read.textContent = "Phase B: " + state.phase_b_readiness.unsold_players + " unsold, " +
        state.phase_b_readiness.incomplete_fill_needed + " needed to fill incomplete teams.";
    }
  }

  function refreshViewer(state) {
    renderBudget(state);
    renderLot(state);
    renderFeed(state);
    renderLotBids(state);
    renderPhase(state);
  }

  function startLive(stateUrl, seasonId) {
    async function tick() {
      try {
        const res = await fetch(stateUrl);
        if (!res.ok) return;
        refreshViewer(await res.json());
      } catch (err) { /* keep polling */ }
    }
    tick();
    setInterval(tick, 4000);
    connectSocket(function (payload) {
      if (!payload || payload.season_id === seasonId) tick();
    });
  }

  /* ---------- manager ---------- */
  function refreshManager(state, myTeamId, urls) {
    renderLot(state);
    renderBudget(state);
    renderManagerControls(state, myTeamId, urls);
    renderTradeRequests(state, myTeamId, urls);
  }

  function renderManagerControls(state, myTeamId, urls) {
    const box = el("bid-controls");
    if (!box) return;
    const p = state.current_player;
    const team = state.teams.find(function (t) { return t.id === myTeamId; });
    box.innerHTML = "";
    if (!team) return;

    const ruleset = state.ruleset;
    const inBiddingPhase = state.phase.startsWith("phase_a_") || state.phase === "phase_b";
    let disabled = false;
    let reason = "";
    if (team.control_status === "admin_takeover") { disabled = true; reason = "Team under admin control"; }
    else if (!p) { disabled = true; reason = "No player nominated"; }
    else if (!inBiddingPhase) { disabled = true; reason = "Bidding not open"; }
    else if (state.phase === "phase_b" && team.players.length < ruleset.required_players) {
      disabled = true; reason = "Incomplete teams cannot bid in Phase B";
    }
    else if (team.credits_remaining < p.credits) { disabled = true; reason = "Not enough credits for this player"; }

    if (disabled) {
      box.innerHTML = '<p class="muted small">' + esc(reason) + "</p>";
      return;
    }

    let minBid;
    if (state.phase === "phase_b") {
      minBid = ruleset.phase_b_price;
    } else {
      minBid = Math.max(p.base_price, (p.current_bid || 0) + ruleset.bid_increment);
    }
    const inc = ruleset.bid_increment;
    const wallet = team.wallet != null ? team.wallet : 0;  // purse == wallet since the drop
    const cantAfford = wallet < minBid;

    function btn(label, amount, cls) {
      const b = document.createElement("button");
      b.className = "btn " + (cls || "");
      b.textContent = label;
      b.disabled = cantAfford || (state.phase === "phase_b" && amount !== minBid);
      b.addEventListener("click", function () { doBid(urls.bidUrl, amount); });
      return b;
    }

    box.appendChild(btn("Bid " + minBid, minBid, "btn-primary"));
    if (state.phase !== "phase_b") box.appendChild(btn("Bid " + (minBid + inc), minBid + inc));

    const custom = document.createElement("input");
    custom.type = "number";
    custom.placeholder = "custom";
    custom.style.width = "110px";
    custom.min = minBid;
    box.appendChild(custom);
    const customBtn = document.createElement("button");
    customBtn.className = "btn";
    customBtn.textContent = "Bid";
    customBtn.disabled = cantAfford;
    customBtn.addEventListener("click", function () { doBid(urls.bidUrl, parseInt(custom.value, 10)); });
    box.appendChild(customBtn);

    const passBtn = document.createElement("button");
    passBtn.className = "btn";
    passBtn.textContent = "Pass";
    passBtn.addEventListener("click", function () {
      postForm(urls.passUrl, {}, function (data) { showBidError(data.error || ""); });
    });
    box.appendChild(passBtn);

    if (cantAfford) {
      const note = document.createElement("p");
      note.className = "muted small";
      note.textContent = "Your wallet cannot cover the minimum bid.";
      box.appendChild(note);
    }
  }

  function doBid(url, amount) {
    if (!amount || amount <= 0) { showBidError("Enter a valid amount."); return; }
    postForm(url, { amount: amount }, function (data) {
      if (data.ok) showBidError("");
      else showBidError(data.error || "Bid failed");
    });
  }

  function showBidError(msg) {
    const box = el("bid-error");
    if (box) box.textContent = msg;
  }

  function renderTradeRequests(state, myTeamId, urls) {
    const box = el("trade-requests");
    if (!box || !state.trade_requests) return;
    const tr = state.trade_requests;
    let html = "";
    if (state.phase !== "break") {
      html = '<p class="muted small">Trades are only possible during the break phase.</p>';
    } else {
      if (tr.incoming.length) {
        html += "<h3>Incoming</h3><ul class='feed'>";
        tr.incoming.forEach(function (r) {
          html += "<li>" + esc(r.from_team_name) + " offers " + esc(r.offered_player_name) +
            (r.requested_player_name !== "-" ? " for " + esc(r.requested_player_name) : "") +
            " <button class='btn btn-small js-trade-resp' data-id='" + esc(r.id) + "' data-action='accept'>Accept</button>" +
            " <button class='btn btn-small btn-danger js-trade-resp' data-id='" + esc(r.id) + "' data-action='reject'>Reject</button></li>";
        });
        html += "</ul>";
      }
      if (tr.outgoing.length) {
        html += "<h3>Outgoing</h3><ul class='feed'>";
        tr.outgoing.forEach(function (r) {
          html += "<li>" + esc(r.offered_player_name) + " → " + esc(r.to_team_name) +
            (r.requested_player_name !== "-" ? " for " + esc(r.requested_player_name) : "") +
            " <span class='tag'>" + esc(r.status) + "</span></li>";
        });
        html += "</ul>";
      }
      if (!tr.incoming.length && !tr.outgoing.length) html = '<p class="muted small">No trade requests.</p>';
    }
    box.innerHTML = html;
    box.querySelectorAll(".js-trade-resp").forEach(function (btn) {
      btn.addEventListener("click", function () {
        postForm(urls.tradeRespondUrl, { trade_id: btn.dataset.id, action: btn.dataset.action },
          function () {});
      });
    });

    const form = el("trade-form");
    if (form) {
      form.onsubmit = function (e) {
        e.preventDefault();
        postForm(urls.tradeUrl, new FormData(form), function () {});
      };
    }
  }

  function postForm(url, data, onDone) {
    let body;
    if (data instanceof FormData) body = data;
    else {
      body = new URLSearchParams();
      Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    }
    fetch(url, { method: "POST", body: body })
      .then(function (res) { return res.json().catch(function () { return {}; }); })
      .then(onDone)
      .catch(function () { onDone({ error: "Network error" }); });
  }

  function startManager(stateUrl, seasonId, myTeamId, urls) {
    async function tick() {
      try {
        const res = await fetch(stateUrl);
        if (!res.ok) return;
        refreshManager(await res.json(), myTeamId, urls);
      } catch (err) { /* keep polling */ }
    }
    tick();
    setInterval(tick, 4000);
    connectSocket(function (payload) {
      if (!payload || payload.season_id === seasonId) tick();
    });
  }

  function connectSocket(onUpdate) {
    if (typeof io === "undefined") return;
    try {
      const socket = io();
      socket.on("state_update", onUpdate);
    } catch (err) { /* polling fallback covers it */ }
  }

  window.startLive = startLive;
  window.startManager = startManager;

  document.addEventListener("DOMContentLoaded", function () {
    initToasts();
    initDrawer();
  });
})();
