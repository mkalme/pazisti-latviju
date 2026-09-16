/* Best-result persistence in localStorage, with in-memory fallback. */
(function () {
  "use strict";
  window.App = window.App || {};

  var KEY = "rigaStreets.v1";
  var memory = null; // fallback when localStorage is unavailable

  function load() {
    if (memory) return memory;
    try {
      var raw = localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : { levels: {} };
    } catch (e) {
      memory = { levels: {} };
      return memory;
    }
  }

  function save(state) {
    if (memory) { memory = state; return; }
    try {
      localStorage.setItem(KEY, JSON.stringify(state));
    } catch (e) {
      memory = state;
    }
  }

  function bestFor(levelId) {
    return load().levels[levelId] || null;
  }

  /* Returns true if this run set a new best. */
  function record(levelId, pct, timeMs) {
    var state = load();
    var cur = state.levels[levelId];
    var isBest = !cur || pct > cur.bestPct ||
      (pct === cur.bestPct && timeMs < cur.bestTimeMs);
    state.levels[levelId] = {
      bestPct: isBest ? pct : cur.bestPct,
      bestTimeMs: isBest ? timeMs : cur.bestTimeMs,
      plays: (cur ? cur.plays : 0) + 1
    };
    save(state);
    return isBest;
  }

  var PREF_KEY = "rigaStreets.prefs";
  var prefMemory = null;

  function prefs() {
    if (prefMemory) return prefMemory;
    try {
      return JSON.parse(localStorage.getItem(PREF_KEY)) || {};
    } catch (e) {
      prefMemory = prefMemory || {};
      return prefMemory;
    }
  }

  function prefGet(name, fallback) {
    var p = prefs();
    return name in p ? p[name] : fallback;
  }

  function prefSet(name, value) {
    var p = prefs();
    p[name] = value;
    if (prefMemory) { prefMemory = p; return; }
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(p));
    } catch (e) {
      prefMemory = p;
    }
  }

  App.storage = { bestFor: bestFor, record: record, prefGet: prefGet, prefSet: prefSet };
})();
