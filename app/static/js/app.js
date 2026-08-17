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
    const logo = function (t) {
      return t.logo_url ? '<img class="team-logo-sm" src="' + esc(t.logo_url) + '" alt="">' : "";
    };
    if (cards) {
      cards.innerHTML = state.public_budget_board.map(function (t) {
        return '<div class="stat-tile">' +
          '<div class="stat-label">' + logo(t) + esc(t.team_name) + takeover(t) + "</div>" +
          '<div class="stat-value">' + esc(t.purse_remaining) + "</div>" +
          '<div class="small muted">' + esc(t.credits_remaining) + " credits · " +
          esc(t.active_count) + " XI / " + esc(t.bench_count) + " bench</div>" +
          "</div>";
      }).join("");
    }
    if (tbody) {
      tbody.innerHTML = state.public_budget_board.map(function (t) {
        return "<tr>" +
          "<td>" + logo(t) + esc(t.team_name) + takeover(t) + "</td>" +
          "<td>" + esc(t.purse_remaining) + "</td>" +
          "<td>" + esc(t.credits_remaining) + "</td>" +
          "<td>" + esc(t.active_count) + "</td>" +
          "<td>" + esc(t.bench_count) + "</td>" +
          "</tr>";
      }).join("");
    }
  }

  function playerStatsHtml(p) {
    const s = p && p.stats;
    if (!s) return "";
    const fmt = function (v, d) { return (v === undefined || v === null || v === 0) ? d : v; };
    const two = function (v) { return v ? Number(v).toFixed(2) : "—"; };
    return '<div class="lot-stats stat-row">' +
      '<div class="stat-tile"><div class="stat-label">Matches</div><div class="stat-value">' + esc(s.matches) + "</div></div>" +
      '<div class="stat-tile"><div class="stat-label">Runs</div><div class="stat-value">' + esc(s.runs) + "</div></div>" +
      '<div class="stat-tile"><div class="stat-label">Avg</div><div class="stat-value">' + two(s.batting_average) + "</div></div>" +
      '<div class="stat-tile"><div class="stat-label">SR</div><div class="stat-value">' + two(s.strike_rate) + "</div></div>" +
      '<div class="stat-tile"><div class="stat-label">Wkts</div><div class="stat-value">' + esc(s.wickets) + "</div></div>" +
      '<div class="stat-tile"><div class="stat-label">Econ</div><div class="stat-value">' + two(s.economy) + "</div></div>" +
      '<div class="stat-tile"><div class="stat-label">Fantasy</div><div class="stat-value">' + esc(s.fantasy_score) + "</div></div>" +
      "</div>";
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
      playerStatsHtml(p) +
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

  function renderUpcoming(state) {
    const feed = el("upcoming-feed");
    if (!feed) return;
    const list = state.upcoming || [];
    if (!list.length) {
      feed.innerHTML = '<li class="muted">No players left in this phase.</li>';
      return;
    }
    feed.innerHTML = list.map(function (p) {
      return "<li><span class='chip'>" + esc(p.tier) + "</span> " + esc(p.name) +
        ' <span class="muted">base ' + esc(p.base_price) + " · " + esc(p.credits) + " cr</span></li>";
    }).join("");
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
    renderUpcoming(state);
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
    renderLotBids(state);
    renderUpcoming(state);
    renderBudget(state);
    renderManagerControls(state, myTeamId, urls);
    renderTradeRequests(state, myTeamId, urls);
    renderSquad(state, myTeamId);
    renderOpponents(state, myTeamId);
    const badge = el("phase-badge");
    if (badge && badge.textContent !== state.phase) badge.textContent = state.phase;
  }

  /* Other teams' squads (XI/bench), live-updated from state.teams which
     already carries player_labels / bench_labels per team. */
  function renderOpponents(state, myTeamId) {
    const box = el("opponents-box");
    if (!box) return;
    const others = state.teams.filter(function (t) {
      return t.id !== myTeamId && t.is_active;
    });
    if (!others.length) {
      box.innerHTML = '<p class="muted small">No other teams in this season.</p>';
      return;
    }
    box.innerHTML = others.map(function (t) {
      let html = "<div style='padding:10px 0;border-bottom:1px solid var(--border)'>" +
        "<div class='row' style='align-items:center;gap:8px;margin-bottom:6px'>" +
        "<span class='chip'>" + esc(t.name) + "</span>" +
        "<span class='muted small'>" + esc(t.wallet) + " purse · " +
        esc(t.credits_remaining) + " credits</span></div>";
      if (t.player_labels && t.player_labels.length) {
        html += "<div class='row'>" + t.player_labels.map(function (l) {
          return '<span class="chip">' + esc(l) + "</span>";
        }).join("") + "</div>";
      } else {
        html += '<p class="muted small">No players bought yet.</p>';
      }
      if (t.bench_labels && t.bench_labels.length) {
        html += "<div class='row' style='margin-top:6px'>" + t.bench_labels.map(function (l) {
          return '<span class="chip chip-bench">' + esc(l) + "</span>";
        }).join("") + "</div>";
      }
      return html + "</div>";
    }).join("");
  }

  /* Re-render the manager's squad (XI/bench) + wallet/credits/spent tiles when
     a lot closes and a player is sold to this team. The state JSON already
     carries the enriched team (player_labels / bench_labels). */
  function renderSquad(state, myTeamId) {
    const box = el("squad-box");
    if (!box) return;
    const team = state.teams.find(function (t) { return t.id === myTeamId; });
    if (!team) { box.innerHTML = ""; return; }
    let html = "<h3>XI</h3>";
    if (team.player_labels && team.player_labels.length) {
      html += '<div class="row">' + team.player_labels.map(function (l) {
        return '<span class="chip">' + esc(l) + "</span>";
      }).join("") + "</div>";
    } else {
      html += '<p class="muted small">No players bought yet.</p>';
    }
    if (team.bench_labels && team.bench_labels.length) {
      html += "<h3>Bench</h3><div class=\"row\">" + team.bench_labels.map(function (l) {
        return '<span class="chip chip-bench">' + esc(l) + "</span>";
      }).join("") + "</div>";
    }
    box.innerHTML = html;
    const wallet = el("stat-wallet");
    const credits = el("stat-credits");
    const spent = el("stat-spent");
    if (wallet && team.wallet != null) wallet.textContent = team.wallet;
    if (credits && team.credits_remaining != null) credits.textContent = team.credits_remaining;
    if (spent && team.spent != null) spent.textContent = team.spent;
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
    const holdsTop = p && p.current_bidder_team_id === myTeamId;
    let disabled = false;
    let reason = "";
    if (team.control_status === "admin_takeover") { disabled = true; reason = "Team under admin control"; }
    else if (!p) { disabled = true; reason = "No player nominated"; }
    else if (holdsTop) { disabled = true; reason = "You hold the highest bid — wait for another team to bid"; }
    else if (!inBiddingPhase) { disabled = true; reason = "Bidding not open"; }
    else if (state.phase === "phase_b" && team.players.length < ruleset.required_players) {
      disabled = true; reason = "Incomplete teams cannot bid in Phase B";
    }
    else if (team.credits_remaining < p.credits) { disabled = true; reason = "Not enough credits for this player"; }

    // Pass is always available: it signals you're done with this player — and
    // as the high bidder, that you won't go higher.
    const passBtn = document.createElement("button");
    passBtn.className = "btn";
    passBtn.textContent = "Pass";
    passBtn.addEventListener("click", function () {
      postForm(urls.passUrl, {}, function (data) { showBidError(data.error || ""); });
    });

    if (disabled) {
      box.innerHTML = '<p class="muted small">' + esc(reason) + "</p>";
      if (holdsTop) box.appendChild(passBtn);
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
      const wrap = form.closest("details");
      if (state.phase !== "break") {
        if (wrap) wrap.style.display = "none";
      } else {
        if (wrap) wrap.style.display = "";
        rebuildTradeForm(form, state, myTeamId);
      }
      form.onsubmit = function (e) {
        e.preventDefault();
        postForm(urls.tradeUrl, new FormData(form), function () {});
      };
    }
  }

  /* Rebuild the trade form's selects from live state so the rosters shown
     (offer / request) reflect trades and auction results without a refresh. */
  function rebuildTradeForm(form, state, myTeamId) {
    const others = state.teams.filter(function (t) { return t.id !== myTeamId && t.is_active; });
    const mine = state.teams.find(function (t) { return t.id === myTeamId; });
    const prev = {};
    Array.prototype.forEach.call(form.elements, function (el2) {
      if (el2.name) prev[el2.name] = el2.value;
    });

    let html = '<div class="row">';
    html += '<select name="to_team_id" required style="width:auto"><option value="">— with team —</option>';
    others.forEach(function (t) {
      html += '<option value="' + esc(t.id) + '">' + esc(t.name) + "</option>";
    });
    html += "</select>";
    html += '<select name="offered_player_id" required style="width:auto"><option value="">— offer —</option>';
    if (mine) {
      mine.players.forEach(function (pid, i) {
        html += '<option value="' + esc(pid) + '">' + esc(mine.player_labels[i] || pid) + "</option>";
      });
      mine.bench.forEach(function (pid, i) {
        html += '<option value="' + esc(pid) + '">' + esc(mine.bench_labels[i] || pid) + "</option>";
      });
    }
    html += "</select>";
    html += '<select name="requested_player_id" style="width:auto"><option value="">— request (optional) —</option>';
    others.forEach(function (t) {
      t.players.forEach(function (pid, i) {
        html += '<option value="' + esc(pid) + '">' + esc(t.player_labels[i] || pid) + "</option>";
      });
    });
    html += "</select>";
    html += '<input type="number" name="cash_from_initiator" placeholder="cash I pay" value="0" style="width:100px">';
    html += '<input type="number" name="cash_from_target" placeholder="cash they pay" value="0" style="width:100px">';
    html += '<button class="btn btn-primary btn-small" type="submit">Request trade</button>';
    html += "</div>";
    form.innerHTML = html;

    /* keep the manager's selections where the option still exists */
    Array.prototype.forEach.call(form.elements, function (el2) {
      if (el2.name && prev[el2.name] && el2.tagName === "SELECT") {
        var exists = Array.prototype.some.call(el2.options, function (o) {
          return o.value === prev[el2.name];
        });
        if (exists) el2.value = prev[el2.name];
      }
    });
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

  /* ---------- "More" dropdown ---------- */
  function initNavMore() {
    const btn = document.getElementById("nav-more-btn");
    const menu = document.getElementById("nav-more-menu");
    if (!btn || !menu) return;
    function setOpen(open) {
      menu.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!menu.classList.contains("open"));
    });
    document.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setOpen(false);
    });
  }

  /* ---------- scroll restoration on same-page reloads ---------- */
  function initScrollRestore() {
    const key = "scl-scroll:" + location.pathname + location.search;
    window.addEventListener("pagehide", function () {
      sessionStorage.setItem(key, String(window.scrollY || 0));
    });
    window.addEventListener("load", function () {
      const y = sessionStorage.getItem(key);
      if (y !== null && y !== "0") window.scrollTo(0, parseInt(y, 10));
    });
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

  /* Admin auction page: re-render the current-lot box + bid feed live on
     every state push (no reload), and only full-reload when the phase or the
     current player actually changed (scroll preserved by the app). */
  function startAdminLive() {
    const root = el("admin-auction-root");
    if (!root || typeof io === "undefined") return;
    const seasonId = root.dataset.season;
    const stateUrl = root.dataset.stateUrl;
    let last = { phase: root.dataset.phase, lot: root.dataset.lot };
    let reloading = false;

    function renderAdminLive(state) {
      const lotBox = el("admin-current-lot");
      if (lotBox) {
        const p = state.current_player;
        if (p) {
          const bidder = p.current_bidder_team_name && p.current_bidder_team_name !== "-"
            ? ' <span class="muted">by ' + esc(p.current_bidder_team_name) + "</span>" : "";
          lotBox.innerHTML =
            '<div class="lot-box">' +
            '<div class="lot-name">' + esc(p.name) + " <span class='tag'>" + esc(p.tier) + "</span></div>" +
            '<div class="lot-bid">Current bid: <strong>' + esc(p.current_bid) + "</strong>" + bidder + "</div>" +
            '<div class="lot-base">Base: ' + esc(p.base_price) + "</div>" +
            playerStatsHtml(p) +
            "</div>";
        } else {
          lotBox.innerHTML = '<p class="muted">No player nominated.</p>';
        }
      }
      const feed = el("admin-bid-feed");
      if (feed) {
        const curId = state.current_player ? state.current_player.id : "";
        feed.innerHTML = (state.bids || []).slice(0, 15).map(function (b) {
          const label = b.kind === "pass" ? "pass" : b.amount;
          const del = curId && b.player_id === curId
            ? ' <button class="btn btn-small btn-danger js-delete-bid" data-bid-id="' +
              esc(b.id) + '" title="Delete this bid (top bid reverts)" ' +
              'style="margin-left:6px;padding:1px 7px">✕</button>' : "";
          return "<li><span class='chip'>" + esc(b.team_name) + "</span> " +
            esc(b.player_name) + " — " + esc(label) +
            ' <span class="muted">' + esc(b.ts_display) + "</span>" + del + "</li>";
        }).join("") || '<li class="muted">No bids yet.</li>';
      }
    }

    function maybeReload(state) {
      if (reloading || !state) return;
      const lot = state.current_player ? state.current_player.id : "";
      if (state.phase !== last.phase || lot !== last.lot) {
        last = { phase: state.phase, lot: lot };
        reloading = true;
        window.location.reload();
      }
    }

    function check() {
      fetch(stateUrl).then(function (r) { return r.json(); })
        .then(function (state) {
          renderAdminLive(state);
          maybeReload(state);
        })
        .catch(function () {});
    }

    const socket = io();
    socket.on("state_update", function (payload) {
      if (!payload || payload.season_id !== seasonId) return;
      check();
    });
    /* Catch anything that changed between page render and socket connect. */
    setTimeout(check, 1500);

    /* Deleting a mistaken bid on the current lot (event delegation so it
       survives the live feed re-renders). */
    document.addEventListener("click", function (e) {
      const btn = e.target && e.target.closest ? e.target.closest(".js-delete-bid") : null;
      if (!btn) return;
      e.preventDefault();
      if (!window.confirm("Delete this bid? The lot's top bid reverts to the previous one.")) return;
      const bidId = btn.dataset.bidId;
      fetch("/admin/season/" + encodeURIComponent(seasonId) +
            "/bid/" + encodeURIComponent(bidId) + "/delete", { method: "POST" })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (d) {
          if (d && d.error) window.alert(d.error);
          /* success: the state_update push refreshes the feed + lot */
        })
        .catch(function () {});
    });
  }

  window.startLive = startLive;
  window.startManager = startManager;
  window.startAdminLive = startAdminLive;

  document.addEventListener("DOMContentLoaded", function () {
    initToasts();
    initDrawer();
    initNavMore();
    initScrollRestore();
  });
})();
