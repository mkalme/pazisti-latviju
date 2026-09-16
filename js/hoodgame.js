/* Neighborhoods quiz: Seterra-style click-the-named-apkaime. Mirrors the
   street engine in game.js, with polygon targets instead of streets. */
(function () {
  "use strict";
  window.App = window.App || {};

  var game = {
    active: false,
    level: null,
    items: [],
    idx: 0,
    attempts: 0,
    points: 0,
    phase: "await", // await | guided | done
    colors: new Map(),
    startTime: 0,
    timerId: 0,
    pending: 0
  };

  // Sub-levels (one novads' pagasti): clicks outside the level are inert,
  // like ocean clicks — the rest of the country is grayed out anyway
  var levelIds = null;

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  function buildItems(level) {
    var hoods = App.data.hoods;
    var ids = level.ids || hoods.map(function (h) { return h.id; });
    return shuffle(ids.map(function (id) {
      return { name: hoods[id].name, ids: [id], hint: "", result: -1 };
    }));
  }

  /* The unanswered hood under (or, for cities, near) the point, most
     specific first, or -1. */
  function hoodAt(wx, wy) {
    var hits = App.geom.hoodsAt(App.data.hoods, wx, wy, App.geom.cityPickTol());
    for (var i = 0; i < hits.length; i++) {
      if (levelIds && !levelIds.has(hits[i])) continue;
      if (!game.colors.has(hits[i])) return hits[i];
    }
    return -1;
  }

  function onHover(wx, wy) {
    if (game.phase === "guided") {
      App.renderer.setHoverHood(-1);
      App.renderer.setGuideHover(wx !== null &&
        clickedTarget(game.items[game.idx], wx, wy));
      return;
    }
    if (wx === null || game.phase !== "await") { App.renderer.setHoverHood(-1); return; }
    var item = game.items[game.idx];
    if (!game.colors.has(item.ids[0]) && clickedTarget(item, wx, wy)) {
      App.renderer.setHoverHood(item.ids[0]);
      return;
    }
    App.renderer.setHoverHood(hoodAt(wx, wy));
  }

  function clickedTarget(item, wx, wy) {
    var hoods = App.data.hoods;
    var hits = App.geom.hoodsAt(hoods, wx, wy, App.geom.cityPickTol());
    if (!hits.length) return false;
    var targetId = item.ids[0];
    if (hits[0] === targetId) return true;
    // Target-first exists so the asked hood wins shared-border slivers —
    // but a state city outranking the pick (exact hit or magnetic band)
    // is a genuine different answer, not a sliver: the city takes the
    // click even when the asked novads is under the cursor too.
    return hits.indexOf(targetId) >= 0 && !hoods[hits[0]].city;
  }

  function onClick(wx, wy, cx, cy) {
    if (game.phase !== "await" && game.phase !== "guided") return;
    var item = game.items[game.idx];
    if (game.phase === "guided") {
      // The answer is pulsing: only clicking it moves the game on
      if (clickedTarget(item, wx, wy)) completeReveal(item);
      return;
    }
    var targetId = item.ids[0];
    // Target-first: on a shared boundary the asked hood wins
    var hit = clickedTarget(item, wx, wy) ? targetId : hoodAt(wx, wy);
    if (hit < 0) return; // water / outside the city — like an ocean click
    if (hit === targetId) {
      var color = "correct" + (game.attempts + 1);
      game.colors.set(targetId, color);
      item.result = game.attempts;
      game.points += 3 - game.attempts;
      App.renderer.setHoverHood(-1);
      App.renderer.invalidate();
      App.renderer.flashHoods(item.ids, color, 500, 1);
      App.ui.promptFeedback(true);
      App.sound.play("correct");
      advance(); // no pause: the next prompt appears immediately
    } else {
      game.attempts++;
      App.renderer.flashHoods([hit], "missed", 400, 1);
      App.ui.tempTooltip(App.data.hoods[hit].name, cx, cy, 1200);
      App.ui.promptFeedback(false);
      if (game.attempts >= 3) { reveal(item); } else { App.sound.play("wrong"); }
    }
  }

  function reveal(item) {
    game.phase = "guided";
    item.result = 3;
    App.renderer.setHoverHood(-1);
    var hood = App.data.hoods[item.ids[0]];
    if (!App.geom.bboxIntersects(hood.bbox, App.view.worldRect())) {
      var a = App.geom.hoodAnchor(hood);
      App.view.centerOn(a[0], a[1]);
    }
    App.renderer.guide = { hood: item.ids[0] };
    App.renderer.revealLabel = { hood: item.ids[0] };
    App.renderer.invalidateOverlay();
    App.sound.play("reveal");
  }

  function completeReveal(item) {
    game.colors.set(item.ids[0], "missed");
    App.renderer.guide = null;
    App.renderer.setGuideHover(false);
    App.renderer.revealLabel = null;
    App.renderer.invalidate();
    App.sound.play("tick");
    advance();
  }

  function advance() {
    game.idx++;
    game.attempts = 0;
    if (game.idx >= game.items.length) { finish(); return; }
    game.phase = "await";
    App.ui.setPrompt(game.items[game.idx]);
    App.ui.setProgress(game.idx, game.items.length);
  }

  function finish() {
    game.phase = "done";
    clearInterval(game.timerId);
    var timeMs = performance.now() - game.startTime;
    var n = game.items.length;
    var pct = Math.round(100 * game.points / (3 * n));
    var firstTry = game.items.filter(function (it) { return it.result === 0; }).length;
    var missed = game.items.filter(function (it) { return it.result === 3; });
    var isBest = App.storage.record(game.level.id, pct, Math.round(timeMs));
    App.sound.play("finish");
    App.ui.showSummary({
      level: game.level, pct: pct, timeMs: timeMs,
      firstTry: firstTry, total: n, missed: missed, isBest: isBest
    });
  }

  game.start = function (level) {
    game.stop();
    game.active = true;
    game.level = level;
    game.items = buildItems(level);
    game.idx = 0;
    game.attempts = 0;
    game.points = 0;
    game.phase = "await";
    game.colors = new Map();
    levelIds = level.sub ? new Set(level.ids) : null;

    var cfg = App.renderer.config;
    cfg.focusIds = levelIds;
    cfg.focusRings = level.focusRings || null;
    cfg.inLevel = new Set(); // empty: every street draws as dimmed backdrop
    cfg.activeHood = -1;
    cfg.colorOf = null;
    cfg.hoodQuiz = true;
    cfg.hoodColorOf = function (id) { return game.colors.get(id) || null; };
    cfg.shadeHoods = false;
    cfg.majorsOnly = false;
    App.view.onClick = onClick;
    App.view.onHover = onHover;
    App.view.fitBbox(level.bbox);
    App.renderer.invalidate();

    game.startTime = performance.now();
    App.ui.setTimer(0);
    game.timerId = setInterval(function () {
      App.ui.setTimer(performance.now() - game.startTime);
    }, 500);
    App.ui.setPrompt(game.items[0]);
    App.ui.setProgress(0, game.items.length);
  };

  game.skip = function () {
    if (game.phase === "await") reveal(game.items[game.idx]);
    else if (game.phase === "guided") completeReveal(game.items[game.idx]);
  };

  game.stop = function () {
    game.active = false;
    clearInterval(game.timerId);
    clearTimeout(game.pending);
    levelIds = null;
    var cfg = App.renderer.config;
    cfg.hoodQuiz = false;
    cfg.hoodColorOf = null;
    cfg.focusIds = null;
    cfg.focusRings = null;
    App.renderer.guide = null;
    App.renderer.setGuideHover(false);
    App.renderer.revealLabel = null;
    App.renderer.setHoverHood(-1);
  };

  App.hoodgame = game;
})();
