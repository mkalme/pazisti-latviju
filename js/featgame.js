/* Feature quiz factory: Seterra-style click-the-named-feature, for datasets
   of scattered shapes (bridges, parks). Features mix lines and outline
   rings; small ones render and hit-test as dot markers. */
(function () {
  "use strict";
  window.App = window.App || {};

  App.createFeatureQuiz = function (getItems, opts) {

  // Pick radius in screen px — ONE radius shared by hover and clicks, so
  // the hover highlight is exactly what a click will select: a click where
  // nothing highlights must never hit. City-dot quizzes widen it a little.
  var PICK_PX = (opts && opts.pickPx) || 16;
  // First-try success color override (lakes turn water-blue, not green)
  var CORRECT1 = (opts && opts.correct1Key) || "correct1";

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
    timerId: 0
  };

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    return arr;
  }

  function buildItems() {
    return shuffle(getItems().map(function (b) {
      return { name: b.name, ids: [b.id], hint: b.hint || "", result: -1 };
    }));
  }

  /* Distance from the cursor to a bridge. Zoomed out, small bridges become
     dot markers, so distance is measured to the marker's anchor; otherwise
     0 inside an outline ring, else distance to the nearest deck line or
     outline edge — outline-only bridges get click tolerance too. */
  function bridgeDist(b, wx, wy) {
    var small = ((b.bbox[2] - b.bbox[0]) + (b.bbox[3] - b.bbox[1]))
      * App.view.scale < 12;
    if (small) {
      var a = App.geom.bridgeAnchor(b);
      return Math.hypot(wx - a[0], wy - a[1]);
    }
    if (b.rings.length && App.geom.pointInRings(b.rings, wx, wy)) return 0;
    var d = Infinity;
    if (b.segs.length) d = App.geom.distToStreet(b, wx, wy);
    if (b.rings.length) d = Math.min(d, App.geom.distToRings(b.rings, wx, wy));
    return d;
  }

  /* The NEAREST unanswered feature within tolerance, or -1 — with a generous
     radius, neighboring canal footbridges must resolve to the closest one.
     Distance ties (nested parks: a garden inside a park) go to the SMALLEST
     feature, so the more specific one stays selectable. */
  function bridgeAt(wx, wy, tol) {
    var items = getItems();
    var bestId = -1, bestDist = Infinity, bestSize = Infinity;
    for (var i = 0; i < items.length; i++) {
      var b = items[i];
      if (game.colors.has(b.id)) continue;
      var d = bridgeDist(b, wx, wy);
      if (d > tol) continue;
      var size = (b.bbox[2] - b.bbox[0]) + (b.bbox[3] - b.bbox[1]);
      if (d < bestDist - 1 || (Math.abs(d - bestDist) <= 1 && size < bestSize)) {
        bestDist = d; bestSize = size; bestId = b.id;
      }
    }
    return bestId;
  }

  /* Distance to the nearest already-answered feature within tolerance,
     or Infinity. */
  function answeredDist(wx, wy, tol) {
    var items = getItems(), best = Infinity;
    game.colors.forEach(function (_, id) {
      var d = bridgeDist(items[id], wx, wy);
      if (d <= tol && d < best) best = d;
    });
    return best;
  }

  /* THE pick resolution, shared by hover, clicks and guided reveals. The
     asked target wins overlaps and distance ties (a shared transit corridor
     resolves to the asked line; a lone city dot attracts nearby clicks) but
     never a click that lands strictly nearer something else: that resolves
     to the nearer feature — or to a no-op when it is already answered, so
     a done dot soaks up its own clicks instead of crediting the target. */
  function pickAt(target, wx, wy, tol) {
    var hit = bridgeAt(wx, wy, tol);
    var td = bridgeDist(target, wx, wy);
    if (td > tol) return hit;
    var hd = (hit < 0 || hit === target.id) ? Infinity
      : bridgeDist(getItems()[hit], wx, wy);
    var ad = answeredDist(wx, wy, tol);
    if (td <= Math.min(hd, ad) + 1) return target.id;
    return hd <= ad + 1 ? hit : -1;
  }

  function onHover(wx, wy) {
    if (game.phase !== "await" && game.phase !== "guided") {
      App.renderer.setHoverFeat(-1);
      return;
    }
    var target = getItems()[game.items[game.idx].ids[0]];
    var hit = wx === null ? -1 : pickAt(target, wx, wy, PICK_PX / App.view.scale);
    if (game.phase === "guided") {
      App.renderer.setHoverFeat(-1);
      App.renderer.setGuideHover(hit === target.id);
      return;
    }
    App.renderer.setHoverFeat(hit);
  }

  function onClick(wx, wy, cx, cy) {
    if (game.phase !== "await" && game.phase !== "guided") return;
    var item = game.items[game.idx];
    var target = getItems()[item.ids[0]];
    var hit = pickAt(target, wx, wy, PICK_PX / App.view.scale);
    if (game.phase === "guided") {
      if (hit === target.id) completeReveal(item);
      return;
    }
    if (hit < 0) return;
    if (hit === target.id) {
      var color = game.attempts ? "correct" + (game.attempts + 1) : CORRECT1;
      game.colors.set(target.id, color);
      item.result = game.attempts;
      game.points += 3 - game.attempts;
      App.renderer.setHoverFeat(-1);
      App.renderer.invalidate();
      App.renderer.flashFeats(getItems(), item.ids, color, 500, 1);
      App.ui.promptFeedback(true);
      App.sound.play("correct");
      advance();
    } else {
      game.attempts++;
      App.renderer.flashFeats(getItems(), [hit], "missed", 400, 1);
      App.ui.tempTooltip(getItems()[hit].name, cx, cy, 1200);
      App.ui.promptFeedback(false);
      if (game.attempts >= 3) { reveal(item); } else { App.sound.play("wrong"); }
    }
  }

  function reveal(item) {
    game.phase = "guided";
    item.result = 3;
    App.renderer.setHoverFeat(-1);
    var b = getItems()[item.ids[0]];
    if (!App.geom.bboxIntersects(b.bbox, App.view.worldRect())) {
      var a = b.rings.length ? App.geom.hoodAnchor(b) : App.geom.labelAnchor(b);
      App.view.centerOn(a[0], a[1]);
    }
    App.renderer.guide = { featItems: getItems(), ids: item.ids };
    App.renderer.revealLabel = { featItems: getItems(), ids: item.ids };
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
    game.items = buildItems();
    game.idx = 0;
    game.attempts = 0;
    game.points = 0;
    game.phase = "await";
    game.colors = new Map();

    var cfg = App.renderer.config;
    cfg.inLevel = new Set(); // empty: every street draws as dimmed backdrop
    cfg.activeHood = -1;
    cfg.colorOf = null;
    cfg.hoodQuiz = false;
    cfg.hoodColorOf = null;
    cfg.featQuiz = {
      items: getItems(),
      colorOf: function (id) { return game.colors.get(id) || null; }
    };
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
    App.renderer.config.featQuiz = null;
    App.renderer.guide = null;
    App.renderer.setGuideHover(false);
    App.renderer.revealLabel = null;
    App.renderer.setHoverFeat(-1);
  };

  return game;
  };

  App.bridgegame = App.createFeatureQuiz(function () { return App.data.bridges; });
  App.parkgame = App.createFeatureQuiz(function () { return App.data.parks; });
  // Latvia city dots (LATVIA_DATA.cities / cities5k); items are tiny rings,
  // so they render and hit-test as dot markers at any sane zoom
  var CITY_PICK = { pickPx: 22 };
  App.lvCityGame = App.createFeatureQuiz(function () { return App.data.cities || []; },
    CITY_PICK);
  App.lvCityGame5k = App.createFeatureQuiz(function () { return App.data.cities5k || []; },
    CITY_PICK);
  App.lvCityGameAll = App.createFeatureQuiz(function () { return App.data.citiesAll || []; },
    CITY_PICK);
  // Latvia countrywide feature quizzes; castles are dots like the cities,
  // the rest are lines (rivers/roads) and areas (lakes/nature)
  App.lvRiverGame = App.createFeatureQuiz(function () { return App.data.rivers || []; });
  App.lvLakeGame = App.createFeatureQuiz(function () { return App.data.lakes || []; },
    { correct1Key: "correctBlue" });
  App.lvRoadGame = App.createFeatureQuiz(function () { return App.data.roads || []; });
  App.lvCastleGame = App.createFeatureQuiz(function () { return App.data.castles || []; },
    CITY_PICK);
  App.lvNatureGame = App.createFeatureQuiz(function () { return App.data.nature || []; });
  App.lvRegionGame = App.createFeatureQuiz(function () { return App.data.regions || []; });
  App.transitGames = {
    tram: App.createFeatureQuiz(function () { return App.data.transit.tram; }),
    trolleybus: App.createFeatureQuiz(function () { return App.data.transit.trolleybus; }),
    busday: App.createFeatureQuiz(function () { return App.data.transit.busDay; }),
    busnight: App.createFeatureQuiz(function () { return App.data.transit.busNight; }),
    rail: App.createFeatureQuiz(function () { return App.data.transit.rail; })
  };
})();
