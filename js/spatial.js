/* Uniform grid spatial index over street edges, for tolerant nearest-street lookup. */
(function () {
  "use strict";
  window.App = window.App || {};

  var CELL = 250; // meters

  var grid = null; // Map "cx,cy" -> array of street ids
  var streets = null;

  function key(cx, cy) { return cx + "," + cy; }

  function build(streetList) {
    streets = streetList;
    grid = new Map();
    for (var i = 0; i < streets.length; i++) {
      var st = streets[i];
      for (var s = 0; s < st.segs.length; s++) {
        var seg = st.segs[s];
        for (var e = 0; e < seg.length - 1; e++) {
          var x0 = Math.min(seg[e][0], seg[e + 1][0]), x1 = Math.max(seg[e][0], seg[e + 1][0]);
          var y0 = Math.min(seg[e][1], seg[e + 1][1]), y1 = Math.max(seg[e][1], seg[e + 1][1]);
          var ca = Math.floor(x0 / CELL), cb = Math.floor(x1 / CELL);
          var cc = Math.floor(y0 / CELL), cd = Math.floor(y1 / CELL);
          for (var cx = ca; cx <= cb; cx++) {
            for (var cy = cc; cy <= cd; cy++) {
              var k = key(cx, cy);
              var arr = grid.get(k);
              if (!arr) { arr = []; grid.set(k, arr); }
              if (arr[arr.length - 1] !== st.id) arr.push(st.id);
            }
          }
        }
      }
    }
  }

  /* All streets within radius r (world meters) of (x, y), nearest first;
     filter(id) optional. */
  function queryAll(x, y, r, filter) {
    var ca = Math.floor((x - r) / CELL), cb = Math.floor((x + r) / CELL);
    var cc = Math.floor((y - r) / CELL), cd = Math.floor((y + r) / CELL);
    var seen = new Set();
    var hits = [];
    for (var cx = ca; cx <= cb; cx++) {
      for (var cy = cc; cy <= cd; cy++) {
        var arr = grid.get(key(cx, cy));
        if (!arr) continue;
        for (var i = 0; i < arr.length; i++) {
          var id = arr[i];
          if (seen.has(id)) continue;
          seen.add(id);
          if (filter && !filter(id)) continue;
          var d = App.geom.distToStreet(streets[id], x, y);
          if (d <= r) hits.push({ id: id, dist: d });
        }
      }
    }
    hits.sort(function (a, b) { return a.dist - b.dist; });
    return hits;
  }

  function query(x, y, r, filter) {
    var hits = queryAll(x, y, r, filter);
    return hits.length ? hits[0] : null;
  }

  /* The most SPECIFIC street at a point: among streets lying together under
     the cursor (within 20 m of the nearest), the smallest one wins — so a
     bridge beats the avenue that runs over it. `overlapping` lists them all,
     winner first. */
  function pick(x, y, r, filter) {
    var hits = queryAll(x, y, r, filter);
    if (!hits.length) return null;
    var near = hits[0].dist;
    var co = hits.filter(function (h) { return h.dist <= near + 8; });
    co.sort(function (a, b) {
      var qa = Math.round(a.dist / 10), qb = Math.round(b.dist / 10);
      if (qa !== qb) return qa - qb;
      var ba = streets[a.id].bbox, bb = streets[b.id].bbox;
      return (ba[2] - ba[0] + ba[3] - ba[1]) - (bb[2] - bb[0] + bb[3] - bb[1]);
    });
    return { id: co[0].id, dist: co[0].dist, overlapping: co };
  }

  App.spatial = { build: build, query: query, queryAll: queryAll, pick: pick };
})();
