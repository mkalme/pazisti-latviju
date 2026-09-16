/* DOM: screens, menu grid with map thumbnails, HUD, tooltip, summary. */
(function () {
  "use strict";
  window.App = window.App || {};

  var els = {};
  var tempTooltipUntil = 0;
  var tooltipTimer = 0;
  var summaryLevel = null;
  var THUMB = 96; // CSS px
  var thumbCache = { theme: "", canvas: null };
  var thumbCanvasCache = new Map(); // "theme:levelId" -> rendered canvas

  /* Per-kind lookups: card unit, summary wording, feature dataset, thumb hue */
  var KIND_UNIT = { hoods: " areas", latvia: " territories",
    lvcities: " cities", lvcities5k: " cities",
    bridges: " bridges", parks: " parks",
    tram: " lines", trolleybus: " lines", busday: " lines",
    busnight: " lines", rail: " lines" };
  var KIND_MISSED = { hoods: "Missed neighborhoods", latvia: "Missed territories",
    lvcities: "Missed cities", lvcities5k: "Missed cities",
    bridges: "Missed bridges",
    parks: "Missed parks", tram: "Missed lines", trolleybus: "Missed lines",
    busday: "Missed lines", busnight: "Missed lines", rail: "Missed lines" };
  var KIND_POLY = { hoods: 1, latvia: 1 }; // polygon-quiz kinds (hoodgame engine)
  var TRANSIT_HUE = { tram: "hsla(0, 65%, 55%, 0.9)",
    trolleybus: "hsla(140, 50%, 45%, 0.9)", busday: "hsla(215, 65%, 55%, 0.9)",
    busnight: "hsla(270, 55%, 60%, 0.9)", rail: "hsla(0, 0%, 60%, 0.95)" };

  function featItemsFor(kind) {
    if (kind === "bridges") return App.data.bridges;
    if (kind === "parks") return App.data.parks;
    if (kind === "lvcities") return App.data.cities;
    if (kind === "lvcities5k") return App.data.cities5k;
    var key = { tram: "tram", trolleybus: "trolleybus", busday: "busDay",
                busnight: "busNight", rail: "rail" }[kind];
    return key && App.data.transit ? App.data.transit[key] : null;
  }

  function $(id) { return document.getElementById(id); }

  function formatTime(ms) {
    var s = Math.floor(ms / 1000);
    var m = Math.floor(s / 60);
    return m + ":" + String(s % 60).padStart(2, "0");
  }

  function showScreen(name) {
    els.menu.classList.toggle("hidden", name !== "menu");
    els.hud.classList.toggle("hidden", name !== "game");
    els.studyBar.classList.toggle("hidden", name !== "study");
    els.summary.classList.add("hidden");
    // Fits must not hide the map's top under the floating bar
    var bar = name === "game" ? els.hud : name === "study" ? els.studyBar : null;
    App.view.topInset = bar
      ? Math.ceil(bar.querySelector(".hud-bar").getBoundingClientRect().bottom) : 0;
  }

  /* --- menu thumbnails: tiny city map, this level highlighted --- */

  function thumbTransform(size, data) {
    var b = data.meta.bounds;
    var w = b[2] - b[0], h = b[3] - b[1];
    var s = (size * 0.92) / Math.max(w, h);
    // Offset by the bbox origin: Latvia's bounds do not start at [0, 0]
    return { s: s, ox: (size - w * s) / 2 - b[0] * s, oy: (size - h * s) / 2 - b[1] * s };
  }

  function traceRingsT(ctx, rings, t) {
    for (var r = 0; r < rings.length; r++) {
      var ring = rings[r];
      ctx.moveTo(ring[0][0] * t.s + t.ox, ring[0][1] * t.s + t.oy);
      for (var i = 1; i < ring.length; i++) {
        ctx.lineTo(ring[i][0] * t.s + t.ox, ring[i][1] * t.s + t.oy);
      }
      ctx.closePath();
    }
  }

  function cityBaseThumb(dpr) {
    var C = App.renderer.COLORS;
    var cv = document.createElement("canvas");
    cv.width = cv.height = THUMB * dpr;
    var ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    var t = thumbTransform(THUMB, App.data);
    ctx.beginPath();
    App.data.hoods.forEach(function (h) { traceRingsT(ctx, h.rings, t); });
    ctx.fillStyle = C.thumbBase;
    ctx.fill();
    ctx.beginPath();
    App.data.water.forEach(function (w) { traceRingsT(ctx, w.rings, t); });
    ctx.fillStyle = C.water;
    ctx.fill("evenodd");
    return cv;
  }

  function drawThumb(canvas, level, dpr) {
    canvas.width = canvas.height = THUMB * dpr;
    canvas.style.width = canvas.style.height = THUMB + "px";
    var ctx = canvas.getContext("2d");
    var data = level.ds || App.data;
    if (!level.ds) ctx.drawImage(thumbCache.canvas, 0, 0); // Riga city base
    ctx.scale(dpr, dpr);
    var t = thumbTransform(THUMB, data);
    var C = App.renderer.COLORS;
    if (level.kind === "latvia") {
      // Pastel mosaic like the Riga Neighborhoods card: every territory
      // over the land base in its shading tint, water on top. Per-unit
      // fills so enclave holes and carved cities tile correctly.
      data.hoods.forEach(function (h) {
        ctx.beginPath();
        traceRingsT(ctx, h.rings, t);
        ctx.fillStyle = C.thumbBase;
        ctx.fill("evenodd");
        ctx.fillStyle = "hsla(" + ((h.id * 47) % 360) + ", 45%, 55%, 0.45)";
        ctx.fill("evenodd");
        ctx.strokeStyle = C.hoodLine;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      });
      ctx.beginPath();
      data.water.forEach(function (w) { traceRingsT(ctx, w.rings, t); });
      ctx.fillStyle = C.water;
      ctx.fill("evenodd");
    } else if (level.kind === "lvcities" || level.kind === "lvcities5k") {
      // City-dot cards: land-filled units with borders, water, and an
      // accent dot per quiz city.
      data.hoods.forEach(function (h) {
        ctx.beginPath();
        traceRingsT(ctx, h.rings, t);
        ctx.fillStyle = C.thumbBase;
        ctx.fill("evenodd");
        ctx.strokeStyle = C.hoodLine;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      });
      ctx.beginPath();
      data.water.forEach(function (w) { traceRingsT(ctx, w.rings, t); });
      ctx.fillStyle = C.water;
      ctx.fill("evenodd");
      var dots = level.kind === "lvcities" ? data.cities : data.cities5k;
      dots.forEach(function (d) {
        var a = App.geom.hoodAnchor(d);
        ctx.beginPath();
        ctx.arc(a[0] * t.s + t.ox, a[1] * t.s + t.oy, 1.6, 0, Math.PI * 2);
        ctx.fillStyle = C.thumbAccent;
        ctx.fill();
      });
    } else if (level.kind === "hoods") {
      // Pastel mosaic: every neighborhood in its shading tint
      data.hoods.forEach(function (h) {
        ctx.beginPath();
        traceRingsT(ctx, h.rings, t);
        ctx.fillStyle = "hsla(" + ((h.id * 47) % 360) + ", 45%, 55%, 0.45)";
        ctx.fill("evenodd");
        ctx.strokeStyle = C.hoodLine;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      });
    } else if (featItemsFor(level.kind)) {
      var items = featItemsFor(level.kind);
      var green = level.kind === "parks";
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      items.forEach(function (b) {
        ctx.beginPath();
        b.segs.forEach(function (seg) {
          ctx.moveTo(seg[0][0] * t.s + t.ox, seg[0][1] * t.s + t.oy);
          for (var i = 1; i < seg.length; i++) {
            ctx.lineTo(seg[i][0] * t.s + t.ox, seg[i][1] * t.s + t.oy);
          }
        });
        traceRingsT(ctx, b.rings, t);
        if (green) {
          ctx.fillStyle = "hsla(130, 45%, 45%, 0.85)";
          ctx.fill("evenodd");
        }
        ctx.strokeStyle = TRANSIT_HUE[level.kind] ||
          (green ? "hsla(130, 45%, 40%, 0.9)" : C.thumbAccent);
        ctx.lineWidth = TRANSIT_HUE[level.kind] ? 1.4 : 2;
        ctx.stroke();
      });
    } else if (level.hoodId >= 0) {
      ctx.beginPath();
      traceRingsT(ctx, data.hoods[level.hoodId].rings, t);
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = C.thumbAccent;
      ctx.fill();
      ctx.globalAlpha = 1;
    } else {
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      var majorsOnly = level.id === "majors";
      var step2 = Math.pow(1.0 / t.s, 2); // skip sub-pixel detail
      data.streets.forEach(function (s) {
        if (majorsOnly && s.cls !== 0) return;
        for (var g = 0; g < s.segs.length; g++) {
          var seg = s.segs[g];
          var last = seg.length - 1;
          var px = seg[0][0], py = seg[0][1];
          ctx.moveTo(px * t.s + t.ox, py * t.s + t.oy);
          for (var i = 1; i < last; i++) {
            var dx = seg[i][0] - px, dy = seg[i][1] - py;
            if (dx * dx + dy * dy < step2) continue;
            px = seg[i][0];
            py = seg[i][1];
            ctx.lineTo(px * t.s + t.ox, py * t.s + t.oy);
          }
          if (last > 0) ctx.lineTo(seg[last][0] * t.s + t.ox, seg[last][1] * t.s + t.oy);
        }
      });
      ctx.strokeStyle = majorsOnly ? C.thumbAccent : C.thumbStreet;
      ctx.lineWidth = majorsOnly ? 1.1 : 0.35;
      ctx.stroke();
    }
  }

  function levelCard(level, dpr, theme) {
    var btn = document.createElement("button");
    btn.className = "level-card";
    if (level.note) btn.title = level.note;
    var key = theme + ":" + level.id;
    var cv = thumbCanvasCache.get(key);
    if (!cv) {
      cv = document.createElement("canvas");
      cv.className = "thumb";
      drawThumb(cv, level, dpr);
      thumbCanvasCache.set(key, cv);
    }
    btn.appendChild(cv);
    var nm = document.createElement("span");
    nm.className = "lv-name";
    nm.textContent = level.name;
    btn.appendChild(nm);
    var meta = document.createElement("span");
    meta.className = "lv-meta";
    meta.textContent = level.itemCount + (KIND_UNIT[level.kind] || " streets");
    var b = App.storage.bestFor(level.id);
    if (b) {
      var best = document.createElement("span");
      best.className = "lv-best";
      best.textContent = " · ★ " + b.bestPct + "%";
      best.title = "Best: " + b.bestPct + "% in " + formatTime(b.bestTimeMs);
      meta.appendChild(best);
    }
    btn.appendChild(meta);
    btn.addEventListener("click", function () { App.startGame(level); });
    return btn;
  }

  var hoodCards = []; // {el, key} for the search filter

  /* Case- and diacritic-insensitive: "agens" matches "Āgenskalns". */
  function searchKey(s) {
    return s.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function applyHoodFilter() {
    var q = searchKey(els.hoodSearch.value.trim());
    var visible = 0;
    hoodCards.forEach(function (c) {
      var show = !q || c.key.indexOf(q) >= 0;
      c.el.classList.toggle("hidden", !show);
      if (show) visible++;
    });
    els.hoodNone.classList.toggle("hidden", visible > 0);
  }

  function buildMenu(levels) {
    var theme = document.documentElement.dataset.theme || "light";
    var dpr = window.devicePixelRatio || 1;
    if (thumbCache.theme !== theme || !thumbCache.canvas) {
      thumbCache = { theme: theme, canvas: cityBaseThumb(dpr) };
    }
    els.menuSpecials.innerHTML = "";
    els.menuTransport.innerHTML = "";
    els.menuLatvia.innerHTML = "";
    els.menuHoods.innerHTML = "";
    hoodCards = [];
    levels.specials.forEach(function (l) { els.menuSpecials.appendChild(levelCard(l, dpr, theme)); });
    levels.transport.forEach(function (l) { els.menuTransport.appendChild(levelCard(l, dpr, theme)); });
    // Latvia section vanishes as a whole when its data file is absent
    var lvLevels = levels.latvia || [];
    $("menu-latvia-h2").classList.toggle("hidden", !lvLevels.length);
    els.menuLatvia.classList.toggle("hidden", !lvLevels.length);
    lvLevels.forEach(function (l) { els.menuLatvia.appendChild(levelCard(l, dpr, theme)); });
    levels.hoods.forEach(function (l) {
      var card = levelCard(l, dpr, theme);
      els.menuHoods.appendChild(card);
      hoodCards.push({ el: card, key: searchKey(l.name) });
    });
    applyHoodFilter();
  }

  /* --- HUD --- */

  function setPrompt(item) {
    els.promptName.textContent = item.name;
    els.promptHint.textContent = item.hint ? "(" + item.hint + ")" : "";
  }

  function promptFeedback(ok) {
    var el = els.prompt;
    el.classList.remove("flash-ok", "flash-bad");
    void el.offsetWidth; // restart the CSS animation
    el.classList.add(ok ? "flash-ok" : "flash-bad");
  }

  function setProgress(done, total) {
    els.progress.textContent = done + " / " + total;
  }

  function setTimer(ms) {
    els.timer.textContent = formatTime(ms);
  }

  /* --- tooltip --- */

  function placeTooltip(x, y) {
    var t = els.tooltip;
    t.style.left = Math.min(x + 14, window.innerWidth - t.offsetWidth - 8) + "px";
    t.style.top = Math.min(y + 18, window.innerHeight - t.offsetHeight - 8) + "px";
  }

  function showTooltip(text, x, y, also) {
    if (performance.now() < tempTooltipUntil) return;
    var t = els.tooltip;
    t.textContent = text;
    if (also && also.length) {
      var sub = document.createElement("div");
      sub.className = "tt-also";
      sub.textContent = "also here: " + also.join(" · ");
      t.appendChild(sub);
    }
    t.classList.remove("hidden", "tooltip-bad");
    placeTooltip(x, y);
  }

  function hideTooltip() {
    if (performance.now() < tempTooltipUntil) return;
    els.tooltip.classList.add("hidden");
  }

  function tempTooltip(text, x, y, ms) {
    tempTooltipUntil = performance.now() + ms;
    els.tooltip.textContent = text;
    els.tooltip.classList.remove("hidden");
    els.tooltip.classList.add("tooltip-bad");
    placeTooltip(x, y);
    clearTimeout(tooltipTimer);
    tooltipTimer = setTimeout(function () {
      tempTooltipUntil = 0;
      els.tooltip.classList.add("hidden");
      els.tooltip.classList.remove("tooltip-bad");
    }, ms);
  }

  /* --- summary --- */

  function showSummary(res) {
    summaryLevel = res.level;
    els.hud.classList.add("hidden");
    els.sumTitle.textContent = res.level.name;
    els.sumScore.textContent = res.pct + "%";
    els.sumBest.classList.toggle("hidden", !res.isBest);
    els.sumDetail.textContent =
      formatTime(res.timeMs) + " · " + res.firstTry + " of " + res.total + " on the first try";
    els.sumMissedWrap.classList.toggle("hidden", res.missed.length === 0);
    els.sumMissed.innerHTML = "";
    $("sum-missed-word").textContent = KIND_MISSED[res.level.kind] || "Missed streets";
    var flashFn;
    var featItems = featItemsFor(res.level.kind);
    if (KIND_POLY[res.level.kind]) {
      flashFn = App.renderer.flashHoods;
    } else if (featItems) {
      flashFn = function (ids, color, dur, pulses) {
        App.renderer.flashFeats(featItems, ids, color, dur, pulses);
      };
    } else {
      flashFn = App.renderer.flash;
    }
    res.missed.forEach(function (item) {
      var li = document.createElement("li");
      li.textContent = item.name;
      li.addEventListener("mouseenter", function () {
        flashFn(item.ids, "missed", 900, 2);
      });
      els.sumMissed.appendChild(li);
    });
    els.summary.classList.remove("hidden");
  }

  function init() {
    els.menu = $("menu");
    els.hud = $("hud");
    els.studyBar = $("study-bar");
    els.summary = $("summary");
    els.menuSpecials = $("menu-specials");
    els.menuTransport = $("menu-transport");
    els.menuLatvia = $("menu-latvia");
    els.menuHoods = $("menu-hoods");
    els.hoodSearch = $("hood-search");
    els.hoodNone = $("hood-none");
    els.prompt = $("prompt");
    els.promptName = $("prompt-name");
    els.promptHint = $("prompt-hint");
    els.progress = $("progress");
    els.timer = $("timer");
    els.tooltip = $("tooltip");
    els.sumTitle = $("sum-title");
    els.sumScore = $("sum-score");
    els.sumBest = $("sum-best");
    els.sumDetail = $("sum-detail");
    els.sumMissedWrap = $("sum-missed-wrap");
    els.sumMissed = $("sum-missed");

    els.hoodSearch.addEventListener("input", applyHoodFilter);
    els.hoodSearch.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && els.hoodSearch.value) {
        els.hoodSearch.value = "";
        applyHoodFilter();
        e.stopPropagation();
      }
    });
    $("btn-study").addEventListener("click", function () { App.startStudy(); });
    $("btn-quit").addEventListener("click", function () { App.showMenu(); });
    $("btn-restart").addEventListener("click", function () { App.restartLevel(); });
    $("btn-skip").addEventListener("click", function () { App.skipActive(); });
    $("btn-study-back").addEventListener("click", function () { App.showMenu(); });
    $("study-districts").addEventListener("change", App.study.onToggle);
    $("study-shade").addEventListener("change", App.study.onToggle);
    $("study-majors").addEventListener("change", App.study.onToggle);
    $("sum-again").addEventListener("click", function () { App.startGame(summaryLevel); });
    $("sum-menu").addEventListener("click", function () { App.showMenu(); });
    $("sum-study").addEventListener("click", function () {
      App.startStudy({ bbox: summaryLevel.bbox,
        districts: !!KIND_POLY[summaryLevel.kind] });
    });
  }

  App.ui = {
    init: init,
    showScreen: showScreen,
    buildMenu: buildMenu,
    setPrompt: setPrompt,
    promptFeedback: promptFeedback,
    setProgress: setProgress,
    setTimer: setTimer,
    showTooltip: showTooltip,
    hideTooltip: hideTooltip,
    tempTooltip: tempTooltip,
    showSummary: showSummary
  };
})();
