/* Boot and mode switching. */
(function () {
  "use strict";
  window.App = window.App || {};

  var rigaData = null; // boot dataset; the menu always shows the Riga map

  /* Swap the active dataset (Riga <-> Latvia). Engines, renderer, UI and
     study all read App.data, so one reassignment reroutes everything; the
     renderer/view/spatial module captures are updated alongside. */
  App.useDataset = function (d) {
    if (App.data === d) return;
    App.data = d;
    App.renderer.data = d;
    App.spatial.build(d.streets || []);
    App.view.setBounds(d.meta.bounds);
    App.renderer.invalidate();
  };

  App.showMenu = function () {
    engines().forEach(function (e) { e.stop(); });
    App.study.exit();
    var cfg = App.renderer.config;
    cfg.inLevel = null;
    cfg.activeHood = -1;
    cfg.colorOf = null;
    cfg.hoodQuiz = false;
    cfg.hoodColorOf = null;
    cfg.shadeHoods = false;
    cfg.majorsOnly = false;
    App.useDataset(rigaData);
    App.view.onClick = null;
    App.view.onHover = null;
    App.view.topInset = 0; // menu covers the map; fit it bar-free
    App.view.fitBbox(App.data.meta.bounds);
    App.renderer.invalidate();
    App.ui.buildMenu(App.levelList);
    App.ui.showScreen("menu");
  };

  function engines() {
    return [App.game, App.hoodgame, App.bridgegame, App.parkgame,
            App.lvCityGame, App.lvCityGame5k]
      .concat(Object.keys(App.transitGames).map(function (k) {
        return App.transitGames[k];
      }));
  }

  var ENGINE_BY_KIND = {
    hoods: App.hoodgame,
    latvia: App.hoodgame,
    lvcities: App.lvCityGame,
    lvcities5k: App.lvCityGame5k,
    bridges: App.bridgegame,
    parks: App.parkgame
  };
  Object.keys(App.transitGames).forEach(function (k) {
    ENGINE_BY_KIND[k] = App.transitGames[k];
  });

  App.startGame = function (level) {
    App.study.exit();
    engines().forEach(function (e) { e.stop(); });
    App.useDataset(level.ds || rigaData);
    App.ui.showScreen("game");
    (ENGINE_BY_KIND[level.kind] || App.game).start(level);
  };

  App.startStudy = function (opts) {
    engines().forEach(function (e) { e.stop(); });
    App.ui.showScreen("study");
    App.study.enter(opts);
  };

  App.skipActive = function () {
    var active = engines().find(function (e) { return e.active; });
    if (active) active.skip();
  };

  App.restartLevel = function () {
    var active = engines().find(function (e) { return e.active; });
    if (active && active.level) App.startGame(active.level);
  };

  App.setTheme = function (theme) {
    document.documentElement.dataset.theme = theme;
    App.renderer.setTheme(theme);
    App.storage.prefSet("theme", theme);
    document.getElementById("btn-theme").textContent = theme === "dark" ? "☀️" : "🌙";
    if (!document.getElementById("menu").classList.contains("hidden")) {
      App.ui.buildMenu(App.levelList); // thumbnails are theme-colored
    }
  };

  App.setMuted = function (muted) {
    App.sound.setMuted(muted);
    App.storage.prefSet("muted", muted);
    document.getElementById("set-sound").checked = !muted;
  };

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.RIGA_DATA) {
      document.getElementById("nodata").classList.remove("hidden");
      return;
    }
    rigaData = App.data = window.RIGA_DATA;
    App.levelList = App.levels.build(App.data);
    App.spatial.build(App.data.streets);

    var canvas = document.getElementById("map");
    App.view.init(canvas, App.data.meta.bounds);
    App.view.onChange = function () { App.renderer.viewChanged(); };
    App.renderer.init(canvas, App.data);
    App.ui.init();

    var systemDark = window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    App.setTheme(App.storage.prefGet("theme", systemDark ? "dark" : "light"));
    App.setMuted(App.storage.prefGet("muted", false));
    document.getElementById("btn-theme").addEventListener("click", function () {
      App.setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });

    // Settings popover: pan/zoom locks (persisted), sound, restart, reset view
    App.view.panLock = !App.storage.prefGet("pan", true);
    App.view.zoomLock = !App.storage.prefGet("zoom", true);
    var pop = document.getElementById("settings-pop");
    var setPan = document.getElementById("set-pan");
    var setZoom = document.getElementById("set-zoom");
    setPan.checked = !App.view.panLock;
    setZoom.checked = !App.view.zoomLock;
    document.getElementById("btn-settings").addEventListener("click", function () {
      pop.classList.toggle("hidden");
    });
    document.addEventListener("click", function (e) {
      if (pop.classList.contains("hidden")) return;
      if (e.target.closest && e.target.closest("#settings-pop, #btn-settings")) return;
      pop.classList.add("hidden");
    });
    setPan.addEventListener("change", function () {
      App.view.panLock = !setPan.checked;
      App.storage.prefSet("pan", setPan.checked);
    });
    setZoom.addEventListener("change", function () {
      App.view.zoomLock = !setZoom.checked;
      App.storage.prefSet("zoom", setZoom.checked);
    });
    document.getElementById("set-sound").addEventListener("change", function (e) {
      App.setMuted(!e.target.checked);
    });
    document.getElementById("set-reset").addEventListener("click", function () {
      App.view.fitBbox(App.view.lastFit || App.view.bounds);
    });

    App.showMenu();

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (!pop.classList.contains("hidden")) { pop.classList.add("hidden"); return; }
        if (!document.getElementById("menu").classList.contains("hidden")) return;
        App.showMenu();
      } else if (e.key === "s" || e.key === "S") {
        if (e.target && e.target.tagName === "INPUT") return;
        App.skipActive();
      }
    });
  });
})();
