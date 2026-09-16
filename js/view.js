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
    bounds: null
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
    padFrac = padFrac === undefined ? 0.08 : padFrac;
    var w = Math.max(1, bbox[2] - bbox[0]);
    var h = Math.max(1, bbox[3] - bbox[1]);
    var s = Math.min(view.cssW / (w * (1 + 2 * padFrac)), view.cssH / (h * (1 + 2 * padFrac)));
    view.scale = clampScale(s);
    view.tx = view.cssW / 2 - (bbox[0] + w / 2) * view.scale;
    view.ty = view.cssH / 2 - (bbox[1] + h / 2) * view.scale;
    changed();
  };

  view.centerOn = function (x, y) {
    view.tx = view.cssW / 2 - x * view.scale;
    view.ty = view.cssH / 2 - y * view.scale;
    changed();
  };

  function zoomAt(sx, sy, factor) {
    var ns = clampScale(view.scale * factor);
    if (ns === view.scale) return;
    var w = view.screenToWorld(sx, sy);
    view.scale = ns;
    view.tx = sx - w[0] * ns;
    view.ty = sy - w[1] * ns;
    changed();
  }
  view.zoomAt = zoomAt;

  view.resize = function () {
    var c = view.canvas;
    view.cssW = c.clientWidth;
    view.cssH = c.clientHeight;
    var dpr = window.devicePixelRatio || 1;
    c.width = Math.round(view.cssW * dpr);
    c.height = Math.round(view.cssH * dpr);
    if (view.bounds) {
      var w = view.bounds[2] - view.bounds[0], h = view.bounds[3] - view.bounds[1];
      view.minScale = 0.8 * Math.min(view.cssW / w, view.cssH / h);
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
          // not pan until the pointer clearly leaves the press point.
          if (!moved) {
            if (downAt && Math.hypot(p[0] - downAt[0], p[1] - downAt[1]) > 5) {
              moved = true;
              view.tx += p[0] - downAt[0]; // catch up: a real drag loses nothing
              view.ty += p[1] - downAt[1];
              changed();
            }
          } else {
            view.tx += p[0] - prev[0];
            view.ty += p[1] - prev[1];
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
      if (!moved && pointers.size === 0 && view.onClick) {
        var at = downAt || p; // clicks land where the press aimed
        var w = view.screenToWorld(at[0], at[1]);
        view.onClick(w[0], w[1], e.clientX, e.clientY);
      }
    }
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointercancel", function (e) { pointers.delete(e.pointerId); });
    canvas.addEventListener("pointerleave", function () {
      if (view.onHover) view.onHover(null);
    });

    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      var p = pos(e);
      zoomAt(p[0], p[1], Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });
  };

  App.view = view;
})();
