/* Locate mode: Seterra-style click-the-named-street quiz. */
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
    inLevel: null,
    startTime: 0,
    timerId: 0,
    pending: 0
  };

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  function buildItems(level) {
    var data = App.data;
    var byName = new Map();
    level.ids.forEach(function (id) {
      var s = data.streets[id];
      if (!byName.has(s.name)) byName.set(s.name, []);
      byName.get(s.name).push(id);
    });
    var items = [];
    byName.forEach(function (ids, name) {
      var doms = new Set(ids.map(function (id) { return data.streets[id].dom; }));
      var hint = "";
      if (ids.length > 1 && doms.size > 1) {
        hint = Array.from(doms).map(function (h) { return data.hoods[h].name; }).join(" / ");
      }
      items.push({ name: name, ids: ids, idSet: new Set(ids), hint: hint, result: -1 });
    });
    return shuffle(items);
  }

  function hoverFilter(id) {
    return game.inLevel.has(id) && !game.colors.has(id);
  }

  function onHover(wx, wy) {
    if (game.phase === "guided") {
      App.renderer.setHover(-1);
      App.renderer.setGuideHover(wx !== null &&
        onTargetAt(game.items[game.idx], wx, wy, 8 / App.view.scale));
      return;
    }
    if (wx === null || game.phase !== "await") { App.renderer.setHover(-1); return; }
    // Target-first, like clicks: aiming at the asked street highlights it
    var item = game.items[game.idx];
    if (onTargetAt(item, wx, wy, 8 / App.view.scale)) {
      App.renderer.setHover(item.ids);
      return;
    }
    var hit = App.spatial.pick(wx, wy, 8 / App.view.scale, hoverFilter);
    App.renderer.setHover(hit ? hit.id : -1);
  }

  function onTargetAt(item, wx, wy, tol) {
    return item.ids.some(function (id) {
      return App.geom.distToStreet(App.data.streets[id], wx, wy) <= tol;
    });
  }

  function onClick(wx, wy, cx, cy) {
    if (game.phase !== "await" && game.phase !== "guided") return;
    var tol = 12 / App.view.scale;
    var item = game.items[game.idx];
    if (game.phase === "guided") {
      // The answer is pulsing: only clicking it moves the game on
      if (onTargetAt(item, wx, wy, tol)) completeReveal(item);
      return;
    }
    // Target-first: where geometries overlap (shared bridge decks, parallel
    // carriageways), a click within tolerance of the asked street counts,
    // even if another street is marginally nearer.
    var onTarget = onTargetAt(item, wx, wy, tol);
    var hit = App.spatial.pick(wx, wy, tol, hoverFilter);
    if (!onTarget && !hit) return;
    if (onTarget || item.idSet.has(hit.id)) {
      var color = "correct" + (game.attempts + 1);
      item.ids.forEach(function (id) { game.colors.set(id, color); });
      item.result = game.attempts;
      game.points += 3 - game.attempts;
      App.renderer.invalidate();
      App.renderer.flash(item.ids, color, 500, 1);
      App.ui.promptFeedback(true);
      App.sound.play("correct");
      advance(); // no pause: the next prompt appears immediately
    } else {
      game.attempts++;
      App.renderer.flash([hit.id], "missed", 400, 1);
      App.ui.tempTooltip(App.data.streets[hit.id].name, cx, cy, 1200);
      App.ui.promptFeedback(false);
      if (game.attempts >= 3) { reveal(item); } else { App.sound.play("wrong"); }
    }
  }

  function reveal(item) {
    game.phase = "guided";
    item.result = 3;
    App.renderer.setHover(-1);
    // Bring the answer into view if it is entirely off-screen
    var rect = App.view.worldRect();
    var visible = item.ids.some(function (id) {
      return App.geom.bboxIntersects(App.data.streets[id].bbox, rect);
    });
    if (!visible) {
      var a = App.geom.labelAnchor(App.data.streets[item.ids[0]]);
      App.view.centerOn(a[0], a[1]);
    }
    App.renderer.guide = { ids: item.ids };
    App.renderer.revealLabel = { ids: item.ids };
    App.renderer.invalidateOverlay();
    App.sound.play("reveal");
  }

  function completeReveal(item) {
    item.ids.forEach(function (id) { game.colors.set(id, "missed"); });
    App.renderer.guide = null;
    App.renderer.setGuideHover(false);
    App.renderer.revealLabel = null;
    App.renderer.invalidate();
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
    game.inLevel = new Set(level.ids);

    var cfg = App.renderer.config;
    cfg.inLevel = game.inLevel;
    cfg.activeHood = level.hoodId;
    cfg.colorOf = function (id) { return game.colors.get(id) || null; };
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
    App.renderer.guide = null;
    App.renderer.setGuideHover(false);
    App.renderer.revealLabel = null;
    App.renderer.setHover(-1);
  };

  App.game = game;
})();
