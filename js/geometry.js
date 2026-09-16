/* Geometry helpers: distances, bboxes, label anchors. */
(function () {
  "use strict";
  window.App = window.App || {};

  function distToSegment2(px, py, ax, ay, bx, by) {
    var dx = bx - ax, dy = by - ay;
    var l2 = dx * dx + dy * dy;
    var t = 0;
    if (l2 > 0) {
      t = ((px - ax) * dx + (py - ay) * dy) / l2;
      t = Math.max(0, Math.min(1, t));
    }
    var cx = ax + t * dx, cy = ay + t * dy;
    return (px - cx) * (px - cx) + (py - cy) * (py - cy);
  }

  function distToStreet(street, x, y) {
    var best = Infinity;
    for (var s = 0; s < street.segs.length; s++) {
      var seg = street.segs[s];
      for (var i = 0; i < seg.length - 1; i++) {
        var d2 = distToSegment2(x, y, seg[i][0], seg[i][1], seg[i + 1][0], seg[i + 1][1]);
        if (d2 < best) best = d2;
      }
    }
    return Math.sqrt(best);
  }

  function bboxIntersects(a, b, margin) {
    margin = margin || 0;
    return a[0] - margin <= b[2] && b[0] - margin <= a[2] &&
           a[1] - margin <= b[3] && b[1] - margin <= a[3];
  }

  /* Point halfway along the street's longest segment — where labels go. */
  function labelAnchor(street) {
    var bestSeg = street.segs[0], bestLen = -1;
    for (var s = 0; s < street.segs.length; s++) {
      var seg = street.segs[s], len = 0;
      for (var i = 0; i < seg.length - 1; i++) {
        len += Math.hypot(seg[i + 1][0] - seg[i][0], seg[i + 1][1] - seg[i][1]);
      }
      if (len > bestLen) { bestLen = len; bestSeg = seg; }
    }
    var half = bestLen / 2, acc = 0;
    for (var j = 0; j < bestSeg.length - 1; j++) {
      var d = Math.hypot(bestSeg[j + 1][0] - bestSeg[j][0], bestSeg[j + 1][1] - bestSeg[j][1]);
      if (acc + d >= half && d > 0) {
        var t = (half - acc) / d;
        return [bestSeg[j][0] + t * (bestSeg[j + 1][0] - bestSeg[j][0]),
                bestSeg[j][1] + t * (bestSeg[j + 1][1] - bestSeg[j][1])];
      }
      acc += d;
    }
    return bestSeg[Math.floor(bestSeg.length / 2)].slice();
  }

  /* Even-odd point-in-polygon over a set of rings — matches fill("evenodd"),
     so multi-ring hoods hit-test exactly as they are drawn. */
  function pointInRings(rings, x, y) {
    var inside = false;
    for (var r = 0; r < rings.length; r++) {
      var ring = rings[r];
      var j = ring.length - 1;
      for (var i = 0; i < ring.length; i++) {
        var yi = ring[i][1], yj = ring[j][1];
        if ((yi > y) !== (yj > y)) {
          var xcross = ring[i][0] + (y - yi) * (ring[j][0] - ring[i][0]) / (yj - yi);
          if (x < xcross) inside = !inside;
        }
        j = i;
      }
    }
    return inside;
  }

  /* Shoelace centroid of the hood's largest ring — where its label goes. */
  function hoodAnchor(hood) {
    if (hood._anchor) return hood._anchor;
    var best = null, bestArea = -1;
    for (var r = 0; r < hood.rings.length; r++) {
      var ring = hood.rings[r], a = 0;
      for (var i = 0; i < ring.length - 1; i++) {
        a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
      }
      a = Math.abs(a) / 2;
      if (a > bestArea) { bestArea = a; best = ring; }
    }
    var cx = 0, cy = 0, area = 0;
    for (var k = 0; k < best.length - 1; k++) {
      var cross = best[k][0] * best[k + 1][1] - best[k + 1][0] * best[k][1];
      area += cross;
      cx += (best[k][0] + best[k + 1][0]) * cross;
      cy += (best[k][1] + best[k + 1][1]) * cross;
    }
    area *= 0.5;
    hood._anchor = area === 0
      ? [(hood.bbox[0] + hood.bbox[2]) / 2, (hood.bbox[1] + hood.bbox[3]) / 2]
      : [cx / (6 * area), cy / (6 * area)];
    return hood._anchor;
  }

  function distToRings(rings, x, y) {
    var best = Infinity;
    for (var r = 0; r < rings.length; r++) {
      var ring = rings[r];
      for (var i = 0; i < ring.length - 1; i++) {
        var d2 = distToSegment2(x, y, ring[i][0], ring[i][1],
                                ring[i + 1][0], ring[i + 1][1]);
        if (d2 < best) best = d2;
      }
    }
    return Math.sqrt(best);
  }

  function bridgeAnchor(b) {
    return b.rings.length ? hoodAnchor(b) : labelAnchor(b);
  }

  App.geom = {
    distToStreet: distToStreet,
    bboxIntersects: bboxIntersects,
    labelAnchor: labelAnchor,
    pointInRings: pointInRings,
    hoodAnchor: hoodAnchor,
    distToRings: distToRings,
    bridgeAnchor: bridgeAnchor
  };
})();
