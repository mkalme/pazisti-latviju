/* Derive playable levels from RIGA_DATA: special citywide levels + one per neighborhood. */
(function () {
  "use strict";
  window.App = window.App || {};

  function countNames(data, ids) {
    var names = new Set();
    for (var i = 0; i < ids.length; i++) names.add(data.streets[ids[i]].name);
    return names.size;
  }

  function bboxOfIds(data, ids) {
    var bb = data.streets[ids[0]].bbox.slice();
    for (var i = 1; i < ids.length; i++) {
      var b = data.streets[ids[i]].bbox;
      bb[0] = Math.min(bb[0], b[0]); bb[1] = Math.min(bb[1], b[1]);
      bb[2] = Math.max(bb[2], b[2]); bb[3] = Math.max(bb[3], b[3]);
    }
    return bb;
  }

  function build(data) {
    var majors = [], all = [];
    var perHood = new Map();
    data.streets.forEach(function (s) {
      all.push(s.id);
      if (s.cls === 0) majors.push(s.id);
      s.hoods.forEach(function (h) {
        if (!perHood.has(h)) perHood.set(h, []);
        perHood.get(h).push(s.id);
      });
    });

    // The street modes live in their own collapsible category; the
    // citywide row keeps the polygon/feature quizzes.
    var streetTop = [
      { id: "majors", name: "Major arteries", note: "citywide", hoodId: -1, ids: majors },
      { id: "all", name: "Whole city", note: "marathon · a long game", hoodId: -1, ids: all }
    ];
    var specials = [
      { id: "hoods", kind: "hoods", name: "Neighborhoods",
        note: "citywide · click the district", hoodId: -1,
        ids: data.hoods.map(function (h) { return h.id; }),
        itemCount: data.hoods.length, bbox: data.meta.bounds }
    ];
    var transport = [];
    function featLevel(target, items, id, name, note) {
      if (!items || !items.length) return;
      var bb = items[0].bbox.slice();
      items.forEach(function (b) {
        bb[0] = Math.min(bb[0], b.bbox[0]); bb[1] = Math.min(bb[1], b.bbox[1]);
        bb[2] = Math.max(bb[2], b.bbox[2]); bb[3] = Math.max(bb[3], b.bbox[3]);
      });
      target.push({ id: id, kind: id, name: name, note: note, hoodId: -1,
        ids: items.map(function (b) { return b.id; }),
        itemCount: items.length, bbox: bb });
    }
    featLevel(specials, data.bridges, "bridges", "Bridges", "citywide · click the bridge");
    featLevel(specials, data.parks, "parks", "Parks", "citywide · click the park");
    var tr = data.transit || {};
    featLevel(transport, tr.tram, "tram", "Trams", "click the route");
    featLevel(transport, tr.trolleybus, "trolleybus", "Trolleybuses", "click the route");
    featLevel(transport, tr.busDay, "busday", "Buses (day)", "click the route");
    featLevel(transport, tr.busNight, "busnight", "Buses (night)", "click the route");
    featLevel(transport, tr.rail, "rail", "Train lines", "click the corridor");
    // Countrywide Latvia level — its own dataset, swapped in by startGame.
    // Guarded: the page still works if data/latvia_data.js is absent.
    var latvia = [];
    var pagLevels = [];
    if (window.LATVIA_DATA) {
      var lv = window.LATVIA_DATA;
      latvia.push({ id: "lv:all", kind: "latvia",
        name: "State cities & municipalities",
        note: "all of Latvia · click the territory", hoodId: -1,
        ids: lv.hoods.map(function (h) { return h.id; }),
        itemCount: lv.hoods.length, bbox: lv.meta.bounds, ds: lv });
      if (lv.cities && lv.cities.length) {
        latvia.push({ id: "lv:cities10k", kind: "lvcities",
          name: "Cities over 10 000",
          note: "all of Latvia · click the dot", hoodId: -1,
          ids: lv.cities.map(function (c) { return c.id; }),
          itemCount: lv.cities.length, bbox: lv.meta.bounds, ds: lv });
      }
      if (lv.cities5k && lv.cities5k.length) {
        latvia.push({ id: "lv:cities5k", kind: "lvcities5k",
          name: "Cities over 5 000",
          note: "all of Latvia · click the dot", hoodId: -1,
          ids: lv.cities5k.map(function (c) { return c.id; }),
          itemCount: lv.cities5k.length, bbox: lv.meta.bounds, ds: lv });
      }
      if (lv.citiesAll && lv.citiesAll.length) {
        latvia.push({ id: "lv:citiesall", kind: "lvcitiesall",
          name: "All towns",
          note: "marathon · click the dot", hoodId: -1,
          ids: lv.citiesAll.map(function (c) { return c.id; }),
          itemCount: lv.citiesAll.length, bbox: lv.meta.bounds, ds: lv });
      }
      // Second-level mosaic: one marathon card + a per-novads section,
      // all sharing a virtual dataset whose hoods ARE the pagasti
      if (lv.pagasti && lv.pagasti.length) {
        var lvp = Object.assign({}, lv, { hoods: lv.pagasti });
        // the marathon heads its own category, above the per-novads levels
        pagLevels.push({ id: "lv:pagasti", kind: "lvpagasti",
          name: "All pagasti & cities",
          note: "marathon · click the territory", hoodId: -1,
          ids: lv.pagasti.map(function (h) { return h.id; }),
          itemCount: lv.pagasti.length, bbox: lv.meta.bounds, ds: lvp });
        var perNov = new Map();
        lv.pagasti.forEach(function (p) {
          if (p.nov < 0) return;
          if (!perNov.has(p.nov)) perNov.set(p.nov, []);
          perNov.get(p.nov).push(p.id);
        });
        lv.hoods.forEach(function (u) {
          var ids = perNov.get(u.id);
          if (!ids || ids.length < 2) return;
          pagLevels.push({ id: "lv:pag:" + u.id, kind: "lvpagasti",
            name: u.name, note: "click the territory", hoodId: -1,
            ids: ids, itemCount: ids.length, bbox: u.bbox, ds: lvp,
            sub: 1, focusRings: u.rings });
        });
      }
      // Countrywide feature quizzes, one card per dataset key
      [["regions", "lvregions", "Historical regions", "click the region"],
       ["rivers", "lvrivers", "Rivers", "click the river"],
       ["lakes", "lvlakes", "Lakes", "click the lake"],
       ["roads", "lvroads", "Main highways", "A1–A15 · click the road"],
       ["castles", "lvcastles", "Castles & palaces", "click the castle"],
       ["nature", "lvnature", "National parks & reserves", "click the area"]
      ].forEach(function (spec) {
        var items = lv[spec[0]];
        if (!items || !items.length) return;
        latvia.push({ id: "lv:" + spec[0], kind: spec[1], name: spec[2],
          note: "all of Latvia · " + spec[3], hoodId: -1,
          ids: items.map(function (b) { return b.id; }),
          itemCount: items.length, bbox: lv.meta.bounds, ds: lv });
      });
    }

    var collator = new Intl.Collator("lv");
    var hoods = data.hoods.slice().sort(function (a, b) {
      return collator.compare(a.name, b.name);
    }).map(function (h) {
      var ids = perHood.get(h.id) || [];
      return { id: "hood:" + h.id, name: h.name, note: "", hoodId: h.id, ids: ids };
    }).filter(function (l) { return l.ids.length > 0; });

    streetTop.concat(specials, hoods).forEach(function (l) {
      if (l.kind) return; // hoods/bridges levels are already decorated
      l.itemCount = countNames(data, l.ids);
      l.bbox = l.hoodId >= 0 ? data.hoods[l.hoodId].bbox : bboxOfIds(data, l.ids);
    });
    return { specials: specials, transport: transport, latvia: latvia,
             pagasti: pagLevels, streets: streetTop.concat(hoods) };
  }

  App.levels = { build: build };
})();
