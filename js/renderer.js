/* Canvas renderer: cached offscreen base layer + light per-frame overlay. */
(function () {
  "use strict";
  window.App = window.App || {};

  var LIGHT = {
    bg: "#f7f4ee",
    water: "#b9d6e2",
    hoodLine: "rgba(90, 80, 60, 0.13)",
    hoodStrong: "rgba(90, 80, 60, 0.55)",
    activeHoodFill: "rgba(37, 99, 235, 0.045)",
    activeHoodLine: "rgba(37, 99, 235, 0.35)",
    neutral: "#8a9199",
    dimmed: "#d8d3ca",
    hover: "#2563eb",
    correct1: "#2e9e44",
    correct2: "#d4b90c",
    correct3: "#e07b1f",
    missed: "#c0392b",
    label: "#1f2937",
    labelHalo: "rgba(255,255,255,0.9)",
    thumbBase: "#e9e3d5",
    thumbAccent: "#2563eb",
    thumbStreet: "#9aa0a6"
  };
  var DARK = {
    bg: "#14171c",
    water: "#1e3644",
    hoodLine: "rgba(255, 255, 255, 0.09)",
    hoodStrong: "rgba(255, 255, 255, 0.45)",
    activeHoodFill: "rgba(96, 165, 250, 0.06)",
    activeHoodLine: "rgba(96, 165, 250, 0.45)",
    neutral: "#97a1ad",
    dimmed: "#363c45",
    hover: "#60a5fa",
    correct1: "#3fb950",
    correct2: "#d4b90c",
    correct3: "#e8863a",
    missed: "#e5534b",
    label: "#e6e9ee",
    labelHalo: "rgba(20, 23, 28, 0.92)",
    thumbBase: "#2a3139",
    thumbAccent: "#60a5fa",
    thumbStreet: "#808a96"
  };
  var COLORS = LIGHT;

  /* Dynamic colors are stored as palette KEYS ("correct1", "missed", ...) so a
     theme switch recolors everything already answered. */
  function resolve(c) { return COLORS[c] || c; }

  // Visual street width in world meters by class (motorway..pedestrian)
  var CLS_WIDTH = [8, 6, 4.5];

  var renderer = {
    canvas: null, ctx: null,
    base: null, bctx: null,
    data: null,
    baseDirty: true,
    overlayDirty: true,
    hoverIds: [],
    hoverHood: -1,      // hood id tinted during the neighborhoods quiz
    hoverFeat: -1,      // feature id highlighted during a feature quiz
    flashes: [],        // {ids, color, t0, dur, pulses [, hood:true]}
    pins: [],           // street ids with permanent labels (study mode)
    hoodPins: [],       // hood ids with permanent labels (districts study)
    revealLabel: null,  // {ids} street labels | {hood: id} during reveals
    guide: null,        // {ids} | {hood}: persistent pulse until clicked
    guideHover: false,  // cursor is on the guided target: steady hover color
    config: {
      inLevel: null,        // Set of street ids, or null = everything active
      activeHood: -1,       // hood id to tint (game level), -1 = none
      colorOf: null,        // fn(id) -> css color or null (quiz state colors)
      hoodQuiz: false,      // neighborhoods quiz presentation
      hoodColorOf: null,    // fn(hoodId) -> palette key | null
      featQuiz: null,       // {items, colorOf}: bridges/parks quiz layer
      shadeHoods: false,    // study-mode pastel shading
      majorsOnly: false     // study-mode filter
    }
  };
  renderer.COLORS = COLORS;

  function widthFor(street, scale) {
    return Math.max(1.25, Math.min(14, CLS_WIDTH[street.cls] * scale));
  }
  renderer.widthFor = widthFor;

  /* The view transform the cached base layer was rendered at. Between full
     redraws the base is blitted with an offset/scale, so pan and zoom stay
     cheap even without GPU acceleration. */
  var baseView = null;
  var baseWorld = null; // world rect the base covers (incl. overscan)
  var baseQuality = "full"; // "fast" for mid-gesture redraws
  var settleTimer = 0;
  renderer.lastBaseMs = 0;

  /* minStep2: skip points closer together than ~1.2 screen px (squared world
     meters) — zoomed out, most Douglas-Peucker detail is sub-pixel. */
  function traceSeg(ctx, seg, view, minStep2) {
    var s = view.scale, tx = view.tx, ty = view.ty;
    var last = seg.length - 1;
    var px = seg[0][0], py = seg[0][1];
    ctx.moveTo(px * s + tx, py * s + ty);
    for (var i = 1; i < last; i++) {
      var dx = seg[i][0] - px, dy = seg[i][1] - py;
      if (dx * dx + dy * dy < minStep2) continue;
      px = seg[i][0];
      py = seg[i][1];
      ctx.lineTo(px * s + tx, py * s + ty);
    }
    if (last > 0) ctx.lineTo(seg[last][0] * s + tx, seg[last][1] * s + ty);
  }

  function tracePath(ctx, street, view, minStep2) {
    for (var g = 0; g < street.segs.length; g++) {
      traceSeg(ctx, street.segs[g], view, minStep2);
    }
  }

  function traceRings(ctx, rings, view) {
    for (var r = 0; r < rings.length; r++) {
      var ring = rings[r];
      ctx.moveTo(ring[0][0] * view.scale + view.tx, ring[0][1] * view.scale + view.ty);
      for (var i = 1; i < ring.length; i++) {
        ctx.lineTo(ring[i][0] * view.scale + view.tx, ring[i][1] * view.scale + view.ty);
      }
      ctx.closePath();
    }
  }

  function drawBase() {
    var t0 = performance.now();
    var view = App.view, ctx = renderer.bctx, data = renderer.data, cfg = renderer.config;
    var dpr = window.devicePixelRatio || 1;
    // Overscan: render well past the viewport so pans and zoom-outs land on
    // cached content instead of blank background.
    var M = Math.max(150, Math.min(400, Math.round(0.35 * Math.min(view.cssW, view.cssH))));
    var bw = view.cssW + 2 * M, bh = view.cssH + 2 * M;
    var bview = { scale: view.scale, tx: view.tx + M, ty: view.ty + M };
    // Mid-gesture redraws use reduced resolution and coarser detail: software
    // rasterization cost scales with pixels, and motion hides the softness.
    // The settle redraw re-renders sharp at full resolution.
    var fast = baseQuality === "fast";
    var res = fast ? Math.max(0.6, dpr / 2) : dpr;
    renderer.base.width = Math.round(bw * res);
    renderer.base.height = Math.round(bh * res);
    ctx.setTransform(res, 0, 0, res, 0, 0);
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, bw, bh);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    var scale = view.scale;
    var minStep2 = Math.pow((fast ? 2.4 : 1.2) / scale, 2);
    var cull = [
      -bview.tx / scale, -bview.ty / scale,
      (bw - bview.tx) / scale, (bh - bview.ty) / scale
    ];

    // Water: all bodies are disjoint, so one batched even-odd fill
    ctx.beginPath();
    for (var w = 0; w < data.water.length; w++) {
      traceRings(ctx, data.water[w].rings, bview);
    }
    ctx.fillStyle = COLORS.water;
    ctx.fill("evenodd");

    // Neighborhood shading (study mode): per-hood tints
    var h, hood;
    if (cfg.shadeHoods) {
      for (h = 0; h < data.hoods.length; h++) {
        hood = data.hoods[h];
        if (!App.geom.bboxIntersects(hood.bbox, cull)) continue;
        ctx.beginPath();
        traceRings(ctx, hood.rings, bview);
        ctx.fillStyle = "hsla(" + ((hood.id * 47) % 360) + ", 45%, 55%, 0.12)";
        ctx.fill("evenodd");
      }
    }
    // Answered hoods in the neighborhoods quiz: state-colored fills
    if (cfg.hoodColorOf) {
      for (h = 0; h < data.hoods.length; h++) {
        hood = data.hoods[h];
        if (!App.geom.bboxIntersects(hood.bbox, cull)) continue;
        var hcolor = cfg.hoodColorOf(hood.id);
        if (!hcolor) continue;
        ctx.beginPath();
        traceRings(ctx, hood.rings, bview);
        ctx.globalAlpha = 0.35;
        ctx.fillStyle = resolve(hcolor);
        ctx.fill("evenodd");
        ctx.globalAlpha = 0.9;
        ctx.strokeStyle = resolve(hcolor);
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
    }
    // Neighborhood boundaries: one batched stroke
    ctx.beginPath();
    for (h = 0; h < data.hoods.length; h++) {
      hood = data.hoods[h];
      if (hood.id === cfg.activeHood || !App.geom.bboxIntersects(hood.bbox, cull)) continue;
      traceRings(ctx, hood.rings, bview);
    }
    ctx.strokeStyle = cfg.hoodQuiz ? COLORS.hoodStrong : COLORS.hoodLine;
    ctx.lineWidth = cfg.hoodQuiz ? 2 : 1;
    ctx.stroke();
    if (cfg.activeHood >= 0) {
      hood = data.hoods[cfg.activeHood];
      ctx.beginPath();
      traceRings(ctx, hood.rings, bview);
      ctx.fillStyle = COLORS.activeHoodFill;
      ctx.fill("evenodd");
      ctx.strokeStyle = COLORS.activeHoodLine;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Streets: dimmed (out of level) first, then active by class (majors on
    // top), batching same-styled streets into one path. Streets smaller than
    // ~1.5px are skipped, except answered ones (their color is information).
    var streets = data.streets;
    var colored = [];
    var cls, i, st;
    function tiny(b) {
      return (b[2] - b[0]) * scale < 1.5 && (b[3] - b[1]) * scale < 1.5;
    }
    // Context roads (unnamed segments, ramps): keep the network visually
    // connected — bridge decks and junction pieces often carry no name.
    // Never clickable, so drawn in the dimmed style beneath everything.
    // In the neighborhoods/bridges/parks quizzes the whole street backdrop
    // fades further so the quiz shapes stay dominant.
    if (cfg.hoodQuiz || cfg.featQuiz) ctx.globalAlpha = 0.45;
    var roads = data.ctx || [];
    for (cls = 2; cls >= 0; cls--) {
      ctx.beginPath();
      for (i = 0; i < roads.length; i++) {
        var rd = roads[i];
        if (rd.c !== cls) continue;
        if (!App.geom.bboxIntersects(rd.b, cull) || tiny(rd.b)) continue;
        traceSeg(ctx, rd.s, bview, minStep2);
      }
      ctx.strokeStyle = COLORS.dimmed;
      ctx.lineWidth = Math.max(1, widthFor({ cls: cls }, scale) * 0.85);
      ctx.stroke();
    }
    if (cfg.inLevel) {
      for (cls = 2; cls >= 0; cls--) {
        ctx.beginPath();
        for (i = 0; i < streets.length; i++) {
          st = streets[i];
          if (st.cls !== cls || cfg.inLevel.has(st.id)) continue;
          if (!App.geom.bboxIntersects(st.bbox, cull) || tiny(st.bbox)) continue;
          tracePath(ctx, st, bview, minStep2);
        }
        ctx.strokeStyle = COLORS.dimmed;
        ctx.lineWidth = Math.max(1, widthFor({ cls: cls }, scale) * 0.7);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
    for (cls = 2; cls >= 0; cls--) {
      ctx.beginPath();
      for (i = 0; i < streets.length; i++) {
        st = streets[i];
        if (st.cls !== cls) continue;
        if (cfg.inLevel && !cfg.inLevel.has(st.id)) continue;
        if (cfg.majorsOnly && st.cls !== 0) continue;
        if (!App.geom.bboxIntersects(st.bbox, cull)) continue;
        var color = cfg.colorOf ? cfg.colorOf(st.id) : null;
        if (color) { colored.push(st); continue; }
        if (tiny(st.bbox)) continue;
        tracePath(ctx, st, bview, minStep2);
      }
      ctx.strokeStyle = COLORS.neutral;
      ctx.lineWidth = widthFor({ cls: cls }, scale);
      ctx.stroke();
    }
    for (i = 0; i < colored.length; i++) {
      st = colored[i];
      ctx.beginPath();
      tracePath(ctx, st, bview, minStep2);
      ctx.strokeStyle = resolve(cfg.colorOf(st.id));
      ctx.lineWidth = widthFor(st, scale) + 0.75;
      ctx.stroke();
    }
    // Neighborhoods quiz: re-stroke district borders ON TOP of the street
    // layer, so they stay the dominant shapes to aim at.
    if (cfg.hoodQuiz) {
      ctx.beginPath();
      for (h = 0; h < data.hoods.length; h++) {
        hood = data.hoods[h];
        if (!App.geom.bboxIntersects(hood.bbox, cull)) continue;
        traceRings(ctx, hood.rings, bview);
      }
      ctx.strokeStyle = COLORS.hoodStrong;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    // Feature quiz (bridges/parks): every item drawn on top — lines and/or
    // outlines, neutral until answered, then in its state color.
    if (cfg.featQuiz) {
      var feats = cfg.featQuiz.items;
      var bw2 = Math.max(3, Math.min(12, 8 * scale));
      for (var bi = 0; bi < feats.length; bi++) {
        var br = feats[bi];
        if (!App.geom.bboxIntersects(br.bbox, cull)) continue;
        var bkey = cfg.featQuiz.colorOf ? cfg.featQuiz.colorOf(br.id) : null;
        var bcol = resolve(bkey || "neutral");
        if (bridgeSmall(br, scale)) {
          var ba = App.geom.bridgeAnchor(br);
          ctx.beginPath();
          ctx.arc(ba[0] * scale + bview.tx, ba[1] * scale + bview.ty,
                  bkey ? 6 : 5, 0, Math.PI * 2);
          ctx.fillStyle = bcol;
          ctx.fill();
          ctx.strokeStyle = COLORS.bg;
          ctx.lineWidth = 1.5;
          ctx.stroke();
          continue;
        }
        if (br.rings.length) {
          ctx.beginPath();
          traceRings(ctx, br.rings, bview);
          ctx.globalAlpha = bkey ? 0.4 : 0.18;
          ctx.fillStyle = bcol;
          ctx.fill("evenodd");
          ctx.globalAlpha = 1;
          ctx.strokeStyle = bcol;
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        if (br.segs.length) {
          ctx.beginPath();
          tracePath(ctx, br, bview, minStep2);
          ctx.strokeStyle = bcol;
          ctx.lineWidth = bkey ? bw2 + 1.5 : bw2;
          ctx.stroke();
        }
      }
    }
    baseView = { tx: bview.tx, ty: bview.ty, scale: scale, res: res };
    baseWorld = cull;
    renderer.lastBaseMs = performance.now() - t0;
  }

  function drawLabel(ctx, street, view) {
    var a = App.geom.labelAnchor(street);
    var sx = a[0] * view.scale + view.tx, sy = a[1] * view.scale + view.ty;
    ctx.font = "600 13px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.strokeStyle = COLORS.labelHalo;
    ctx.lineWidth = 4;
    ctx.strokeText(street.name, sx, sy - 6);
    ctx.fillStyle = COLORS.label;
    ctx.fillText(street.name, sx, sy - 6);
  }

  /* A bridge too small on screen is drawn as a dot marker instead. */
  function bridgeSmall(br, scale) {
    return ((br.bbox[2] - br.bbox[0]) + (br.bbox[3] - br.bbox[1])) * scale < 12;
  }

  /* Bridges mix deck lines and outline rings; draw whichever exist. */
  function drawBridgeShape(ctx, br, view, minStep2, color, fillFactor, width) {
    var base = ctx.globalAlpha;
    if (bridgeSmall(br, view.scale)) {
      var a = App.geom.bridgeAnchor(br);
      ctx.beginPath();
      ctx.arc(a[0] * view.scale + view.tx, a[1] * view.scale + view.ty,
              Math.max(6, width * 0.8), 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = COLORS.bg;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      return;
    }
    if (br.rings.length) {
      ctx.beginPath();
      traceRings(ctx, br.rings, view);
      ctx.globalAlpha = base * fillFactor;
      ctx.fillStyle = color;
      ctx.fill("evenodd");
      ctx.globalAlpha = base;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }
    if (br.segs.length) {
      ctx.beginPath();
      tracePath(ctx, br, view, minStep2);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.stroke();
    }
  }

  function bridgeWidth(scale) {
    return Math.max(3, Math.min(12, 8 * scale));
  }

  function drawBridgeLabel(ctx, br, view) {
    var a = br.rings.length ? App.geom.hoodAnchor(br) : App.geom.labelAnchor(br);
    var sx = a[0] * view.scale + view.tx, sy = a[1] * view.scale + view.ty;
    ctx.font = "700 15px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = COLORS.labelHalo;
    ctx.lineWidth = 4;
    ctx.strokeText(br.name, sx, sy);
    ctx.fillStyle = COLORS.label;
    ctx.fillText(br.name, sx, sy);
  }

  function drawHoodLabel(ctx, hood, view) {
    var a = App.geom.hoodAnchor(hood);
    var sx = a[0] * view.scale + view.tx, sy = a[1] * view.scale + view.ty;
    ctx.font = "700 15px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = COLORS.labelHalo;
    ctx.lineWidth = 4;
    ctx.strokeText(hood.name, sx, sy);
    ctx.fillStyle = COLORS.label;
    ctx.fillText(hood.name, sx, sy);
  }

  function composite(now) {
    var view = App.view, ctx = renderer.ctx, data = renderer.data;
    var dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = COLORS.bg;
    ctx.fillRect(0, 0, renderer.canvas.width, renderer.canvas.height);
    if (baseView) {
      // Reuse the cached base during pan/zoom: blit it translated/scaled to
      // the current view; a sharp redraw follows once the gesture settles.
      var k = view.scale / baseView.scale;
      var f = k * dpr / baseView.res; // base backing px -> screen backing px
      ctx.drawImage(renderer.base,
        (view.tx - k * baseView.tx) * dpr,
        (view.ty - k * baseView.ty) * dpr,
        renderer.base.width * f,
        renderer.base.height * f);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    var minStep2 = Math.pow(1.2 / view.scale, 2);

    if (renderer.hoverHood >= 0) {
      var hh = data.hoods[renderer.hoverHood];
      ctx.beginPath();
      traceRings(ctx, hh.rings, view);
      ctx.globalAlpha = 0.18;
      ctx.fillStyle = COLORS.hover;
      ctx.fill("evenodd");
      ctx.globalAlpha = 1;
      ctx.strokeStyle = COLORS.hover;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    if (renderer.hoverFeat >= 0 && renderer.config.featQuiz) {
      drawBridgeShape(ctx, renderer.config.featQuiz.items[renderer.hoverFeat],
        view, minStep2, COLORS.hover, 0.35, bridgeWidth(view.scale) + 2.5);
    }

    for (var hv = 0; hv < renderer.hoverIds.length; hv++) {
      var hs = data.streets[renderer.hoverIds[hv]];
      ctx.beginPath();
      tracePath(ctx, hs, view, minStep2);
      ctx.strokeStyle = COLORS.hover;
      ctx.lineWidth = widthFor(hs, view.scale) + 2.5;
      ctx.stroke();
    }

    var alive = [];
    for (var f = 0; f < renderer.flashes.length; f++) {
      var fl = renderer.flashes[f];
      var t = (now - fl.t0) / fl.dur;
      if (t >= 1) continue;
      alive.push(fl);
      var alpha = 0.35 + 0.65 * Math.abs(Math.sin(fl.pulses * Math.PI * t));
      if (fl.featItems) {
        ctx.globalAlpha = alpha;
        for (var qb = 0; qb < fl.ids.length; qb++) {
          drawBridgeShape(ctx, fl.featItems[fl.ids[qb]], view, minStep2,
            resolve(fl.color), 0.5, bridgeWidth(view.scale) + 3);
        }
        ctx.globalAlpha = 1;
        continue;
      }
      ctx.beginPath();
      if (fl.hood) {
        for (var q0 = 0; q0 < fl.ids.length; q0++) {
          traceRings(ctx, data.hoods[fl.ids[q0]].rings, view);
        }
        ctx.globalAlpha = alpha * 0.45;
        ctx.fillStyle = resolve(fl.color);
        ctx.fill("evenodd");
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = resolve(fl.color);
        ctx.lineWidth = 2.5;
        ctx.stroke();
      } else {
        ctx.globalAlpha = alpha;
        for (var q = 0; q < fl.ids.length; q++) {
          tracePath(ctx, data.streets[fl.ids[q]], view, minStep2);
        }
        ctx.strokeStyle = resolve(fl.color);
        ctx.lineWidth = widthFor(data.streets[fl.ids[0]], view.scale) + 3;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    renderer.flashes = alive;

    // Guided reveal: the missed answer pulses until the player clicks it;
    // hovering it goes steady in the hover color to read as clickable
    if (renderer.guide) {
      var ga = renderer.guideHover
        ? 0.9 : 0.4 + 0.35 * Math.abs(Math.sin(now / 200));
      var gcolor = resolve(renderer.guideHover ? "hover" : "missed");
      if (renderer.guide.hood != null) {
        ctx.beginPath();
        traceRings(ctx, data.hoods[renderer.guide.hood].rings, view);
        ctx.globalAlpha = ga * 0.5;
        ctx.fillStyle = gcolor;
        ctx.fill("evenodd");
        ctx.globalAlpha = ga;
        ctx.strokeStyle = gcolor;
        ctx.lineWidth = 3;
        ctx.stroke();
      } else if (renderer.guide.featItems) {
        ctx.globalAlpha = ga;
        for (var gb = 0; gb < renderer.guide.ids.length; gb++) {
          drawBridgeShape(ctx, renderer.guide.featItems[renderer.guide.ids[gb]],
            view, minStep2, gcolor, 0.5, bridgeWidth(view.scale) + 4);
        }
      } else {
        ctx.globalAlpha = ga;
        ctx.beginPath();
        for (var gi = 0; gi < renderer.guide.ids.length; gi++) {
          tracePath(ctx, data.streets[renderer.guide.ids[gi]], view, minStep2);
        }
        ctx.strokeStyle = gcolor;
        ctx.lineWidth = widthFor(data.streets[renderer.guide.ids[0]], view.scale) + 4;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    for (var p = 0; p < renderer.pins.length; p++) {
      drawLabel(ctx, data.streets[renderer.pins[p]], view);
    }
    for (var hp = 0; hp < renderer.hoodPins.length; hp++) {
      drawHoodLabel(ctx, data.hoods[renderer.hoodPins[hp]], view);
    }
    if (renderer.revealLabel) {
      if (renderer.revealLabel.hood != null) {
        drawHoodLabel(ctx, data.hoods[renderer.revealLabel.hood], view);
      } else if (renderer.revealLabel.featItems) {
        for (var rb = 0; rb < renderer.revealLabel.ids.length; rb++) {
          drawBridgeLabel(ctx,
            renderer.revealLabel.featItems[renderer.revealLabel.ids[rb]], view);
        }
      } else {
        for (var r = 0; r < renderer.revealLabel.ids.length; r++) {
          drawLabel(ctx, data.streets[renderer.revealLabel.ids[r]], view);
        }
      }
    }
  }

  function frame(now) {
    var c = renderer.canvas;
    if (c.clientWidth > 0 &&
        (c.clientWidth !== App.view.cssW || c.clientHeight !== App.view.cssH)) {
      App.view.resize();
    }
    if (!c.width || !c.height) {
      requestAnimationFrame(frame);
      return;
    }
    if (!baseView) {
      renderer.baseDirty = true;
    } else if (!renderer.baseDirty && renderer.overlayDirty) {
      // View escaped the cached (overscanned) area — redraw NOW, in this
      // frame, so no blank background ever reaches the screen. Mid-gesture,
      // so a cheap low-res redraw is enough; settle sharpens it later.
      var vr = App.view.worldRect();
      if (vr[0] < baseWorld[0] || vr[1] < baseWorld[1] ||
          vr[2] > baseWorld[2] || vr[3] > baseWorld[3]) {
        baseQuality = "fast";
        renderer.baseDirty = true;
      }
    }
    if (renderer.baseDirty) {
      drawBase();
      renderer.baseDirty = false;
      renderer.overlayDirty = true;
    }
    if (renderer.overlayDirty || renderer.flashes.length || renderer.guide) {
      composite(now);
      renderer.overlayDirty = false;
    }
    requestAnimationFrame(frame);
  }

  renderer.flash = function (ids, color, dur, pulses) {
    renderer.flashes.push({
      ids: ids, color: color, dur: dur, pulses: pulses || 1,
      t0: performance.now()
    });
  };

  renderer.flashHoods = function (ids, color, dur, pulses) {
    renderer.flashes.push({
      ids: ids, color: color, dur: dur, pulses: pulses || 1,
      t0: performance.now(), hood: true
    });
  };

  renderer.setHoverHood = function (id) {
    if (id === renderer.hoverHood) return;
    renderer.hoverHood = id;
    renderer.overlayDirty = true;
    renderer.canvas.style.cursor = id >= 0 ? "pointer" : "";
  };

  renderer.flashFeats = function (items, ids, color, dur, pulses) {
    renderer.flashes.push({
      ids: ids, color: color, dur: dur, pulses: pulses || 1,
      t0: performance.now(), featItems: items
    });
  };

  renderer.setHoverFeat = function (id) {
    if (id === renderer.hoverFeat) return;
    renderer.hoverFeat = id;
    renderer.overlayDirty = true;
    renderer.canvas.style.cursor = id >= 0 ? "pointer" : "";
  };

  renderer.setGuideHover = function (on) {
    if (on === renderer.guideHover) return;
    renderer.guideHover = on;
    renderer.overlayDirty = true;
    renderer.canvas.style.cursor = on ? "pointer" : "";
  };

  /* Accepts an array of street ids, a single id, or -1/null for none. */
  renderer.setHover = function (ids) {
    if (typeof ids === "number") ids = ids >= 0 ? [ids] : [];
    ids = ids || [];
    var cur = renderer.hoverIds;
    if (ids.length === cur.length &&
        ids.every(function (v, i) { return v === cur[i]; })) return;
    renderer.hoverIds = ids;
    renderer.overlayDirty = true;
    renderer.canvas.style.cursor = ids.length ? "pointer" : "";
  };

  /* Content changed (answer colors, theme, level): full redraw next frame. */
  renderer.invalidate = function () {
    clearTimeout(settleTimer);
    baseQuality = "full";
    renderer.baseDirty = true;
  };
  renderer.invalidateOverlay = function () { renderer.overlayDirty = true; };

  /* View moved (pan/zoom): blit the stale base now, sharpen after settling. */
  renderer.viewChanged = function () {
    renderer.overlayDirty = true;
    clearTimeout(settleTimer);
    settleTimer = setTimeout(function () {
      baseQuality = "full";
      renderer.baseDirty = true;
    }, 120);
  };

  renderer.setTheme = function (theme) {
    COLORS = theme === "dark" ? DARK : LIGHT;
    renderer.COLORS = COLORS;
    renderer.baseDirty = true;
  };

  renderer.init = function (canvas, data) {
    renderer.canvas = canvas;
    renderer.ctx = canvas.getContext("2d");
    renderer.base = document.createElement("canvas");
    renderer.bctx = renderer.base.getContext("2d");
    renderer.data = data;
    requestAnimationFrame(frame);
  };

  App.renderer = renderer;
})();
