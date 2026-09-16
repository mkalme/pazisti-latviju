/* Boot and mode switching. */
(function () {
  "use strict";
  window.App = window.App || {};

  App.showMenu = function () {
    App.game.stop();
    App.hoodgame.stop();
    App.bridgegame.stop();
    App.parkgame.stop();
    App.study.exit();
    var cfg = App.renderer.config;
    cfg.inLevel = null;
    cfg.activeHood = -1;
    cfg.colorOf = null;
    cfg.hoodQuiz = false;
    cfg.hoodColorOf = null;
    cfg.shadeHoods = false;
    cfg.majorsOnly = false;
    App.view.onClick = null;
    App.view.onHover = null;
    App.view.fitBbox(App.data.meta.bounds);
    App.renderer.invalidate();
    App.ui.buildMenu(App.levelList);
    App.ui.showScreen("menu");
  };

  function engines() {
    return [App.game, App.hoodgame, App.bridgegame, App.parkgame]
      .concat(Object.keys(App.transitGames).map(function (k) {
        return App.transitGames[k];
      }));
  }

  App.startGame = function (level) {
    App.study.exit();
    engines().forEach(function (e) { e.stop(); });
    App.ui.showScreen("game");
    var engine = level.kind === "hoods" ? App.hoodgame
      : level.kind === "bridges" ? App.bridgegame
      : level.kind === "parks" ? App.parkgame
      : App.transitGames[level.kind] || App.game;
    engine.start(level);
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
    document.getElementById("btn-sound").textContent = muted ? "🔇" : "🔊";
  };

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.RIGA_DATA) {
      document.getElementById("nodata").classList.remove("hidden");
      return;
    }
    App.data = window.RIGA_DATA;
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
    document.getElementById("btn-sound").addEventListener("click", function () {
      App.setMuted(!App.sound.muted);
    });

    App.showMenu();

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (!document.getElementById("menu").classList.contains("hidden")) return;
        App.showMenu();
      } else if (e.key === "s" || e.key === "S") {
        if (e.target && e.target.tagName === "INPUT") return;
        App.skipActive();
      }
    });
  });
})();
