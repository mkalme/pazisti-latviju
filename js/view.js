/* Viewport: world<->screen transform, pan/zoom/pinch input, fit-to-bbox. */
(function () {
  "use strict";
  window.App = window.App || {};

  var view = {
    scale: 1,      // screen px per world meter
    tx: 0,
    ty: 0,
    minScale: 0.01,
    maxScale: 4,
    canvas: null,
    cssW: 0,
    cssH: 0,
    onChange: null,
    onClick: null,   // (worldX, worldY, clientX, clientY)
    onHover: null,   // (worldX, worldY, clientX, clientY) or (null) when leaving
    bounds: null,
    lastFit: null,   // bbox of the most recent fitBbox — "Reset view" target
    topInset: 0,     // px hidden under the top HUD bar; fits avoid it
    panLock: false,  // settings: ignore USER pan/zoom input; programmatic
    zoomLock: false, //   moves (fitBbox, centerOn, reveals) still work
    fastClick: false // settings: a mouse press IS the click (needs panLock)
  };

  view.worldToScreen = function (x, y) {
    return [x * view.scale + view.tx, y * view.scale + view.ty];
  };
  view.screenToWorld = function (sx, sy) {
    return [(sx - view.tx) / view.scale, (sy - view.ty) / view.scale];
  };
  /* Visible world rect as a bbox [x0, y0, x1, y1]. */
  view.worldRect = function () {
    var a = view.screenToWorld(0, 0);
    var b = view.screenToWorld(view.cssW, view.cssH);
    return [a[0], a[1], b[0], b[1]];
  };

  function changed() {
    if (view.onChange) view.onChange();
  }

  function clampScale(s) {
    return Math.max(view.minScale, Math.min(view.maxScale, s));
  }

  view.fitBbox = function (bbox, padFrac) {
    haltMotion(); // a jump must not later get overridden by a stale ease/glide
    padFrac = padFrac === undefined ? 0.08 : padFrac;
    view.lastFit = bbox;
    var w = Math.max(1, bbox[2] - bbox[0]);
    var h = Math.max(1, bbox[3] - bbox[1]);
    var availH = Math.max(50, view.cssH - view.topInset);
    var s = Math.min(view.cssW / (w * (1 + 2 * padFrac)), availH / (h * (1 + 2 * padFrac)));
    view.scale = clampScale(s);
    view.tx = view.cssW / 2 - (bbox[0] + w / 2) * view.scale;
    view.ty = view.topInset + availH / 2 - (bbox[1] + h / 2) * view.scale;
    changed();
  };

  view.centerOn = function (x, y) {
    haltMotion();
    view.tx = view.cssW / 2 - x * view.scale;
    view.ty = view.cssH / 2 - y * view.scale;
    changed();
  };

  function zoomAt(sx, sy, factor) {
    if (view.zoomLock) return;
    var ns = clampScale(view.scale * factor);
    if (ns === view.scale) return;
    var w = view.screenToWorld(sx, sy);
    view.scale = ns;
    view.tx = sx - w[0] * ns;
    view.ty = sy - w[1] * ns;
    changed();
  }
  view.zoomAt = zoomAt;

  /* Eased wheel zoom + momentum panning, both driven by one view.tick(now)
     called from the renderer's rAF loop. Pinch and programmatic moves
     (fitBbox/centerOn) stay instant/unglided — matching how the reference
     camera (Voxelview/MapeeWeb) scopes ZoomAnim to wheel/dblclick and
     DragInertia to a released single-pointer drag, leaving direct
     manipulation 1:1. */
  var WHEEL_ZOOM_EXPONENT = 0.0024; // deltaY -> zoom exponent; higher = stronger per notch
  var ZOOM_TAU_MS = 55;
  var ZOOM_SNAP = 0.002;
  var zoomTarget = 0;  // 0 = unseeded, adopts view.scale on the next nudge
  var zoomActive = false;
  var zoomAnchorSx = 0, zoomAnchorSy = 0; // screen px the world point pins under
  var zoomAnchorWx = 0, zoomAnchorWy = 0; // that world point

  // Momentum glide after a released drag: a trailing window of recent
  // pointermove velocities is recency-weighted and coherence-gated (a
  // wobbly/reversing drag should not fling) into a release velocity, then
  // eased out over a duration scaled to that speed.
  var INERTIA_MAX_MS = 1250;
  var INERTIA_MIN_MS = 250;
  var INERTIA_POWER = 0.5;
  var INERTIA_MIN_SPEED = 0.15;   // px/ms release speed floor — below this, no glide
  var INERTIA_MAX_SPEED = 8;      // px/ms release speed ceiling — clamps wild flicks
  var INERTIA_SAMPLE_WINDOW_MS = 90;
  var INERTIA_RELEASE_GAP_MS = 80; // a pause before release cancels the fling
  var INERTIA_MIN_COHERENCE = 0.4;
  var INERTIA_FULL_COHERENCE = 0.85;
  var inertiaActive = false;
  var inertiaDurMs = 0, inertiaElapsedMs = 0, inertiaFPrev = 0;
  var inertiaDistX = 0, inertiaDistY = 0;
  var inertiaSamples = []; // flat [vx, vy, ts, ...] px/ms, trailing window
  var inertiaPrevMoveTs = 0;

  var lastTickT = 0;

  /* Cancels an in-flight ease/glide: call on a fresh gesture start or a
     programmatic jump, so neither fights the new motion. */
  function haltMotion() {
    zoomActive = false;
    zoomTarget = 0;
    inertiaActive = false;
    inertiaSamples.length = 0;
    inertiaPrevMoveTs = 0;
  }

  function nudgeZoom(sx, sy, factor) {
    if (view.zoomLock) return;
    if (zoomTarget === 0) zoomTarget = view.scale;
    var w = view.screenToWorld(sx, sy);
    zoomAnchorWx = w[0];
    zoomAnchorWy = w[1];
    zoomAnchorSx = sx;
    zoomAnchorSy = sy;
    zoomTarget = clampScale(zoomTarget * factor);
    zoomActive = true;
  }
  view.nudgeZoom = nudgeZoom;

  /* Feed one drag movement (screen px deltas, event.timeStamp ms). */
  function inertiaMove(dx, dy, ts) {
    if (inertiaPrevMoveTs > 0) {
      var dt = Math.max(1, ts - inertiaPrevMoveTs);
      inertiaSamples.push(dx / dt, dy / dt, ts);
    }
    inertiaPrevMoveTs = ts;
    var cutoff = ts - INERTIA_SAMPLE_WINDOW_MS;
    var drop = 0;
    while (drop + 2 < inertiaSamples.length && inertiaSamples[drop + 2] < cutoff) drop += 3;
    if (drop > 0) inertiaSamples.splice(0, drop);
  }

  /* Pointer released: derive a release velocity from the trailing samples
     and maybe start the glide. */
  function inertiaRelease(ts) {
    var s = inertiaSamples;
    inertiaPrevMoveTs = 0;
    if (s.length < 6) { s.length = 0; return; }
    var lastTs = s[s.length - 1];
    if (ts - lastTs > INERTIA_RELEASE_GAP_MS) { s.length = 0; return; }
    var firstTs = s[2];
    var span = Math.max(1, lastTs - firstTs);
    var sumX = 0, sumY = 0, sumW = 0, sumSpd = 0;
    for (var i = 0; i < s.length; i += 3) {
      var w = 1 + 3 * ((s[i + 2] - firstTs) / span); // recency-weighted
      sumX += s[i] * w;
      sumY += s[i + 1] * w;
      sumSpd += Math.sqrt(s[i] * s[i] + s[i + 1] * s[i + 1]) * w;
      sumW += w;
    }
    var vx = sumX / sumW, vy = sumY / sumW;
    s.length = 0;
    var speed = Math.sqrt(vx * vx + vy * vy);
    var meanSpd = sumSpd / sumW;
    // coherence: 1 if every sample pointed the same way as the average, less
    // if the drag wobbled or reversed — a wobble must not fling.
    var coherence = meanSpd > 1e-6 ? speed / meanSpd : 0;
    var cGate = Math.min(1, Math.max(0,
      (coherence - INERTIA_MIN_COHERENCE) / (INERTIA_FULL_COHERENCE - INERTIA_MIN_COHERENCE)));
    vx *= cGate; vy *= cGate; speed *= cGate;
    if (speed < INERTIA_MIN_SPEED) return;
    if (speed > INERTIA_MAX_SPEED) {
      vx *= INERTIA_MAX_SPEED / speed;
      vy *= INERTIA_MAX_SPEED / speed;
      speed = INERTIA_MAX_SPEED;
    }
    var T = Math.max(INERTIA_MIN_MS,
      INERTIA_MAX_MS * Math.pow(speed / INERTIA_MAX_SPEED, INERTIA_POWER));
    inertiaDurMs = T;
    inertiaElapsedMs = 0;
    inertiaFPrev = 0;
    inertiaDistX = vx * T / 3;
    inertiaDistY = vy * T / 3;
    inertiaActive = true;
  }

  /* Advance the eased zoom + momentum glide one frame (now = rAF timestamp);
     a cheap no-op while neither is in flight. */
  view.tick = function (now) {
    var dt = lastTickT ? Math.min(50, Math.max(0.1, now - lastTickT)) : 16;
    lastTickT = now;
    if (!zoomActive && !inertiaActive) return;
    if (zoomActive) {
      var k = 1 - Math.exp(-dt / ZOOM_TAU_MS);
      var lz = Math.log(view.scale), lt = Math.log(zoomTarget);
      if (Math.abs(lt - lz) < ZOOM_SNAP) {
        view.scale = zoomTarget;
        zoomActive = false;
      } else {
        view.scale = Math.exp(lz + (lt - lz) * k);
      }
      view.tx = zoomAnchorSx - zoomAnchorWx * view.scale;
      view.ty = zoomAnchorSy - zoomAnchorWy * view.scale;
    }
    if (inertiaActive) {
      inertiaElapsedMs += dt;
      var u = Math.min(1, inertiaElapsedMs / inertiaDurMs);
      var inv = 1 - u;
      var f = 1 - inv * inv * inv; // ease-out cubic
      view.tx += (f - inertiaFPrev) * inertiaDistX;
      view.ty += (f - inertiaFPrev) * inertiaDistY;
      inertiaFPrev = f;
      if (u >= 1) inertiaActive = false;
    }
    changed();
  };

  function updateMinScale() {
    if (!view.bounds || !(view.cssW > 0)) return;
    var w = view.bounds[2] - view.bounds[0], h = view.bounds[3] - view.bounds[1];
    view.minScale = 0.8 * Math.min(view.cssW / w, view.cssH / h);
  }

  /* Swap the world bounds (dataset switch): minScale must follow, or a map
     larger than the previous one can never fit on screen. */
  view.setBounds = function (bounds) {
    view.bounds = bounds;
    updateMinScale();
  };

  view.resize = function () {
    var c = view.canvas;
    view.cssW = c.clientWidth;
    view.cssH = c.clientHeight;
    var dpr = window.devicePixelRatio || 1;
    c.width = Math.round(view.cssW * dpr);
    c.height = Math.round(view.cssH * dpr);
    if (view.bounds) {
      updateMinScale();
      // First real layout after a zero-size init (e.g. hidden pane): fit the map
      if (view.cssW > 0 && (!isFinite(view.scale) || view.scale <= 0)) {
        view.fitBbox(view.bounds);
        return;
      }
    }
    changed();
  };

  view.init = function (canvas, bounds) {
    view.canvas = canvas;
    view.bounds = bounds;
    view.resize();
    window.addEventListener("resize", view.resize);

    var pointers = new Map();
    var moved = false;
    var downAt = null;
    var fastFired = false; // this gesture's click already fired on press
    var pinchDist = 0;

    function pos(e) {
      var r = canvas.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    }

    canvas.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      canvas.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, pos(e));
      if (pointers.size === 1) {
        moved = false;
        downAt = pos(e);
        fastFired = false;
        haltMotion(); // a fresh drag/pinch must not fight an in-flight ease/glide
        // Fast mode: the press is the click. Mouse-only (a touch may grow
        // into a pan/pinch) and only under panLock — with panning enabled
        // a press is ambiguously the start of a drag.
        if (view.fastClick && view.panLock &&
            e.pointerType === "mouse" && view.onClick) {
          fastFired = true;
          var fw = view.screenToWorld(downAt[0], downAt[1]);
          view.onClick(fw[0], fw[1], e.clientX, e.clientY);
        }
      }
      if (pointers.size === 2) {
        var pts = Array.from(pointers.values());
        pinchDist = Math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]);
      }
    });

    canvas.addEventListener("pointermove", function (e) {
      var p = pos(e);
      if (pointers.has(e.pointerId)) {
        var prev = pointers.get(e.pointerId);
        pointers.set(e.pointerId, p);
        if (pointers.size === 1) {
          // Drag slop: fast clicks carry a few px of jitter — the map must
          // not pan until the pointer clearly leaves the press point. Past
          // the slop the gesture is a drag and can no longer click; panLock
          // only keeps the map still, it does not keep the click alive.
          if (!moved) {
            if (downAt && Math.hypot(p[0] - downAt[0], p[1] - downAt[1]) > 5) {
              moved = true;
              if (!view.panLock) {
                view.tx += p[0] - downAt[0]; // catch up: a real drag loses nothing
                view.ty += p[1] - downAt[1];
                changed();
              }
            }
          } else if (!view.panLock) {
            view.tx += p[0] - prev[0];
            view.ty += p[1] - prev[1];
            inertiaMove(p[0] - prev[0], p[1] - prev[1], e.timeStamp);
            changed();
          }
        } else if (pointers.size === 2) {
          moved = true;
          var pts = Array.from(pointers.values());
          var d = Math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]);
          var mid = [(pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2];
          if (pinchDist > 0 && d > 0) zoomAt(mid[0], mid[1], d / pinchDist);
          pinchDist = d;
        }
      } else if (view.onHover) {
        var w = view.screenToWorld(p[0], p[1]);
        view.onHover(w[0], w[1], e.clientX, e.clientY);
      }
    });

    function release(e) {
      if (!pointers.has(e.pointerId)) return;
      var p = pointers.get(e.pointerId);
      pointers.delete(e.pointerId);
      pinchDist = 0;
      if (!moved && !fastFired && pointers.size === 0 && view.onClick) {
        var at = downAt || p; // clicks land where the press aimed
        var w = view.screenToWorld(at[0], at[1]);
        view.onClick(w[0], w[1], e.clientX, e.clientY);
      }
      if (moved && pointers.size === 0) inertiaRelease(e.timeStamp);
    }
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", function (e) { pointers.delete(e.pointerId); });
    canvas.addEventListener("pointerleave", function () {
      if (view.onHover) view.onHover(null);
    });

    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var p = pos(e);
      nudgeZoom(p[0], p[1], Math.exp(-e.deltaY * WHEEL_ZOOM_EXPONENT));
    }, { passive: false });
  };

  App.view = view;
})();
