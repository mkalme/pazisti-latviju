/* Study mode: free browsing with hover names and pinnable labels — streets
   by default, districts (apkaimes) when the Districts toggle is on. */
(function () {
  "use strict";
  window.App = window.App || {};

  var study = { active: false };
  var wasDistricts = false;

  function districtsOn() {
    return document.getElementById("study-districts").checked;
  }

  function filter(id) {
    return !App.renderer.config.majorsOnly || App.data.streets[id].cls === 0;
  }

  function hoodAt(wx, wy) {
    var hoods = App.data.hoods;
    for (var i = 0; i < hoods.length; i++) {
      var b = hoods[i].bbox;
      if (wx < b[0] || wx > b[2] || wy < b[1] || wy > b[3]) continue;
      if (App.geom.pointInRings(hoods[i].rings, wx, wy)) return hoods[i].id;
    }
    return -1;
  }

  function onHover(wx, wy, cx, cy) {
    if (wx === null) {
      App.renderer.setHover(-1);
      App.renderer.setHoverHood(-1);
      App.ui.hideTooltip();
      return;
    }
    if (districtsOn()) {
      App.renderer.setHover(-1);
      var hid = hoodAt(wx, wy);
      App.renderer.setHoverHood(hid);
      if (hid >= 0) App.ui.showTooltip(App.data.hoods[hid].name, cx, cy);
      else App.ui.hideTooltip();
      return;
    }
    App.renderer.setHoverHood(-1);
    // Highlight the most specific street under the cursor (a bridge beats
    // the avenue running over it); overlapping streets are disclosed in a
    // secondary tooltip line rather than lighting up together.
    var p = App.spatial.pick(wx, wy, 8 / App.view.scale, filter);
    if (!p) {
      App.renderer.setHover(-1);
      App.ui.hideTooltip();
      return;
    }
    App.renderer.setHover(p.id);
    var s = App.data.streets[p.id];
    var also = [];
    p.overlapping.forEach(function (h) {
      var nm = App.data.streets[h.id].name;
      if (nm !== s.name && also.indexOf(nm) < 0) also.push(nm);
    });
    App.ui.showTooltip(s.name + " — " + App.data.hoods[s.dom].name, cx, cy, also);
  }

  function onClick(wx, wy) {
    if (districtsOn()) {
      var hid = hoodAt(wx, wy);
      if (hid < 0) return;
      var hp = App.renderer.hoodPins;
      var hat = hp.indexOf(hid);
      if (hat >= 0) hp.splice(hat, 1); else hp.push(hid);
      App.sound.play("tick");
      App.renderer.invalidateOverlay();
      return;
    }
    var hit = App.spatial.pick(wx, wy, 12 / App.view.scale, filter);
    if (!hit) return;
    var pins = App.renderer.pins;
    var at = pins.indexOf(hit.id);
    if (at >= 0) pins.splice(at, 1); else pins.push(hit.id);
    App.sound.play("tick");
    App.renderer.invalidateOverlay();
  }

  function applyMode() {
    var cfg = App.renderer.config;
    cfg.shadeHoods = document.getElementById("study-shade").checked;
    cfg.majorsOnly = document.getElementById("study-majors").checked;
    cfg.hoodQuiz = districtsOn(); // strong borders + faded street backdrop
    App.renderer.setHover(-1);
    App.renderer.setHoverHood(-1);
    App.renderer.invalidate();
  }

  study.enter = function (opts) {
    study.active = true;
    if (opts && opts.districts) {
      document.getElementById("study-districts").checked = true;
      document.getElementById("study-shade").checked = true;
      wasDistricts = true;
    } else if (opts && opts.shade) {
      document.getElementById("study-shade").checked = true;
    }
    var cfg = App.renderer.config;
    cfg.inLevel = null;
    cfg.activeHood = -1;
    cfg.colorOf = null;
    cfg.hoodColorOf = null;
    App.view.onClick = onClick;
    App.view.onHover = onHover;
    App.view.fitBbox(opts && opts.bbox ? opts.bbox : App.data.meta.bounds);
    applyMode();
  };

  study.exit = function () {
    if (!study.active) return;
    study.active = false;
    App.renderer.pins = [];
    App.renderer.hoodPins = [];
    App.renderer.setHover(-1);
    App.renderer.setHoverHood(-1);
    App.ui.hideTooltip();
  };

  study.onToggle = function () {
    if (!study.active) return;
    if (districtsOn() && !wasDistricts) {
      // Turning Districts on brings shading with it as a sensible default
      document.getElementById("study-shade").checked = true;
    }
    wasDistricts = districtsOn();
    applyMode();
  };

  App.study = study;
})();
