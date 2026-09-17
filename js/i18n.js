/* UI localization: Latvian by default, English via the ⚙️ switcher.
   Only chrome is translated — data names (streets, apkaimes, novadi,
   features, transit lines) always stay Latvian. The brand "Pazīsti
   Latviju" is invariant and lives directly in the markup. */
(function () {
  "use strict";
  window.App = window.App || {};

  var STRINGS = {
    lv: {
      "menu.subtitle": "Spēle nosauc vietu — noklikšķini uz tās kartē. " +
        "Zaļš: pirmajā mēģinājumā. Dzeltens: otrajā. Oranžs: trešajā. " +
        "Sarkans: atbilde parādīta.",
      "menu.h2.riga": "Rīga",
      "menu.h2.transport": "Sabiedriskais transports",
      "menu.h2.latvia": "Latvija",
      "menu.back": "‹ Atpakaļ",
      "cat.streets": "Rīgas ielas",
      "cat.pagasti": "Pagasti",
      "search.pagasti": "Meklēt novadus…",
      "search.pagasti.none": "Neviens novads neatbilst",
      "search.hoods": "Meklēt apkaimes…",
      "search.hoods.none": "Neviena apkaime neatbilst",
      "card.best": "Labākais: {pct}% — {time}",
      "n.levels": ["līmenis", "līmeņi"],

      "hud.menu": "‹ Izvēlne",
      "hud.find": "Atrodi:",
      "hud.restart": "↻ No jauna",
      "hud.restart.title": "Sākt šo līmeni no jauna",
      "hud.skip": "Izlaist",
      "hud.skip.title": "Padoties un parādīt atbildi (S)",

      "sum.best": "Jauns rekords!",
      "sum.detail": "{time} · {n} no {total} pirmajā mēģinājumā",
      "sum.hint": "(uzvirzi kursoru — iemirgosies kartē)",
      "sum.again": "Spēlēt vēlreiz",
      "sum.menu": "Izvēlne",

      "set.pan": "Kartes pārvietošana",
      "set.zoom": "Kartes tālummaiņa",
      "set.sound": "Skaņa",
      "set.fast": "Ātrais režīms",
      "set.fast.title": "Klikšķis tiek ieskaitīts jau nospiežot peles pogu",
      "set.fast.locked": "Izslēdz kartes pārvietošanu, lai lietotu ātro režīmu",
      "set.reset": "⟲ Atiestatīt skatu",
      "set.lang": "Valoda",
      "btn.theme.title": "Gaišais/tumšais režīms",
      "btn.settings.title": "Kartes un skaņas iestatījumi",
      "attr.pre": "Kartes dati © ",
      "attr.post": " dalībnieki",
      "nodata.p1": "Spēles dati nav atrasti. Vispirms tie jāizveido:",
      "nodata.p2": "pēc tam pārlādē šo lapu.",

      "level.majors": "Galvenās ielas",
      "level.all": "Visa pilsēta",
      "level.hoods": "Apkaimes",
      "level.bridges": "Tilti",
      "level.parks": "Parki",
      "level.tram": "Tramvaji",
      "level.trolleybus": "Trolejbusi",
      "level.busday": "Autobusi (diena)",
      "level.busnight": "Autobusi (nakts)",
      "level.rail": "Vilcienu līnijas",
      "level.lvall": "Valstspilsētas un novadi",
      "level.lvcities10k": "Pilsētas virs 10 000",
      "level.lvcities5k": "Pilsētas virs 5 000",
      "level.lvcitiesall": "Visas pilsētas",
      "level.lvpagasti": "Visi pagasti un pilsētas",
      "level.lvregions": "Kultūrvēsturiskās zemes",
      "level.lvrivers": "Upes",
      "level.lvlakes": "Ezeri",
      "level.lvroads": "Galvenie autoceļi",
      "level.lvcastles": "Pilis",
      "level.lvnature": "Nacionālie parki un rezervāti",

      "note.citywide": "visa Rīga",
      "note.marathonLong": "maratons · gara spēle",
      "note.hoods": "visa Rīga · klikšķini uz apkaimes",
      "note.bridges": "visa Rīga · klikšķini uz tilta",
      "note.parks": "visa Rīga · klikšķini uz parka",
      "note.route": "klikšķini uz maršruta",
      "note.corridor": "klikšķini uz līnijas",
      "note.lvTerritory": "visa Latvija · klikšķini uz teritorijas",
      "note.lvDot": "visa Latvija · klikšķini uz punkta",
      "note.marathonDot": "maratons · klikšķini uz punkta",
      "note.marathonTerritory": "maratons · klikšķini uz teritorijas",
      "note.territory": "klikšķini uz teritorijas",
      "note.lvRegion": "visa Latvija · klikšķini uz zemes",
      "note.lvRiver": "visa Latvija · klikšķini uz upes",
      "note.lvLake": "visa Latvija · klikšķini uz ezera",
      "note.lvRoad": "A1–A15 · klikšķini uz ceļa",
      "note.lvCastle": "visa Latvija · klikšķini uz pils",
      "note.lvNature": "visa Latvija · klikšķini uz teritorijas",

      "unit.streets": ["iela", "ielas"],
      "unit.hoods": ["apkaime", "apkaimes"],
      "unit.territories": ["teritorija", "teritorijas"],
      "unit.cities": ["pilsēta", "pilsētas"],
      "unit.towns": ["pilsēta", "pilsētas"],
      "unit.rivers": ["upe", "upes"],
      "unit.lakes": ["ezers", "ezeri"],
      "unit.roads": ["autoceļš", "autoceļi"],
      "unit.castles": ["pils", "pilis"],
      "unit.areas": ["teritorija", "teritorijas"],
      "unit.regions": ["zeme", "zemes"],
      "unit.bridges": ["tilts", "tilti"],
      "unit.parks": ["parks", "parki"],
      "unit.lines": ["līnija", "līnijas"],

      "missed.streets": "Neuzminētās ielas",
      "missed.hoods": "Neuzminētās apkaimes",
      "missed.territories": "Neuzminētās teritorijas",
      "missed.cities": "Neuzminētās pilsētas",
      "missed.towns": "Neuzminētās pilsētas",
      "missed.rivers": "Neuzminētās upes",
      "missed.lakes": "Neuzminētie ezeri",
      "missed.roads": "Neuzminētie autoceļi",
      "missed.castles": "Neuzminētās pilis",
      "missed.areas": "Neuzminētās teritorijas",
      "missed.regions": "Neuzminētās zemes",
      "missed.bridges": "Neuzminētie tilti",
      "missed.parks": "Neuzminētie parki",
      "missed.lines": "Neuzminētās līnijas"
    },
    en: {
      "menu.subtitle": "The game names a place — click it on the map. " +
        "Green: first try. Yellow: second. Orange: third. Red: revealed.",
      "menu.h2.riga": "Riga",
      "menu.h2.transport": "Public transport",
      "menu.h2.latvia": "Latvia",
      "menu.back": "‹ Back",
      "cat.streets": "Riga streets",
      "cat.pagasti": "Pagasti",
      "search.pagasti": "Search municipalities…",
      "search.pagasti.none": "No municipality matches",
      "search.hoods": "Search neighborhoods…",
      "search.hoods.none": "No neighborhood matches",
      "card.best": "Best: {pct}% in {time}",
      "n.levels": ["level", "levels"],

      "hud.menu": "‹ Menu",
      "hud.find": "Click:",
      "hud.restart": "↻ Restart",
      "hud.restart.title": "Restart this level",
      "hud.skip": "Skip",
      "hud.skip.title": "Give up — reveal the answer (S)",

      "sum.best": "New best!",
      "sum.detail": "{time} · {n} of {total} on the first try",
      "sum.hint": "(hover to flash on the map)",
      "sum.again": "Play again",
      "sum.menu": "Menu",

      "set.pan": "Map panning",
      "set.zoom": "Map zooming",
      "set.sound": "Sound",
      "set.fast": "Fast mode",
      "set.fast.title": "The click registers on mouse-down",
      "set.fast.locked": "Turn off map panning to use fast mode",
      "set.reset": "⟲ Reset view",
      "set.lang": "Language",
      "btn.theme.title": "Light/dark theme",
      "btn.settings.title": "Map & sound settings",
      "attr.pre": "Map data © ",
      "attr.post": " contributors",
      "nodata.p1": "Game data not found. Generate it first:",
      "nodata.p2": "then reload this page.",

      "level.majors": "Major arteries",
      "level.all": "Whole city",
      "level.hoods": "Neighborhoods",
      "level.bridges": "Bridges",
      "level.parks": "Parks",
      "level.tram": "Trams",
      "level.trolleybus": "Trolleybuses",
      "level.busday": "Buses (day)",
      "level.busnight": "Buses (night)",
      "level.rail": "Train lines",
      "level.lvall": "State cities & municipalities",
      "level.lvcities10k": "Cities over 10 000",
      "level.lvcities5k": "Cities over 5 000",
      "level.lvcitiesall": "All towns",
      "level.lvpagasti": "All pagasti & cities",
      "level.lvregions": "Historical regions",
      "level.lvrivers": "Rivers",
      "level.lvlakes": "Lakes",
      "level.lvroads": "Main highways",
      "level.lvcastles": "Castles & palaces",
      "level.lvnature": "National parks & reserves",

      "note.citywide": "all of Riga",
      "note.marathonLong": "marathon · a long game",
      "note.hoods": "all of Riga · click the district",
      "note.bridges": "all of Riga · click the bridge",
      "note.parks": "all of Riga · click the park",
      "note.route": "click the route",
      "note.corridor": "click the corridor",
      "note.lvTerritory": "all of Latvia · click the territory",
      "note.lvDot": "all of Latvia · click the dot",
      "note.marathonDot": "marathon · click the dot",
      "note.marathonTerritory": "marathon · click the territory",
      "note.territory": "click the territory",
      "note.lvRegion": "all of Latvia · click the region",
      "note.lvRiver": "all of Latvia · click the river",
      "note.lvLake": "all of Latvia · click the lake",
      "note.lvRoad": "A1–A15 · click the road",
      "note.lvCastle": "all of Latvia · click the castle",
      "note.lvNature": "all of Latvia · click the area",

      "unit.streets": ["street", "streets"],
      "unit.hoods": ["area", "areas"],
      "unit.territories": ["territory", "territories"],
      "unit.cities": ["city", "cities"],
      "unit.towns": ["town", "towns"],
      "unit.rivers": ["river", "rivers"],
      "unit.lakes": ["lake", "lakes"],
      "unit.roads": ["road", "roads"],
      "unit.castles": ["castle", "castles"],
      "unit.areas": ["area", "areas"],
      "unit.regions": ["region", "regions"],
      "unit.bridges": ["bridge", "bridges"],
      "unit.parks": ["park", "parks"],
      "unit.lines": ["line", "lines"],

      "missed.streets": "Missed streets",
      "missed.hoods": "Missed neighborhoods",
      "missed.territories": "Missed territories",
      "missed.cities": "Missed cities",
      "missed.towns": "Missed towns",
      "missed.rivers": "Missed rivers",
      "missed.lakes": "Missed lakes",
      "missed.roads": "Missed roads",
      "missed.castles": "Missed castles",
      "missed.areas": "Missed areas",
      "missed.regions": "Missed regions",
      "missed.bridges": "Missed bridges",
      "missed.parks": "Missed parks",
      "missed.lines": "Missed lines"
    }
  };

  /* Which of [singular, plural] a count takes. Counts here are never 0
     (cards only exist for non-empty levels); zero would need a third,
     genitive-plural form in Latvian. */
  var PLURAL = {
    lv: function (n) { return n % 10 === 1 && n % 100 !== 11 ? 0 : 1; },
    en: function (n) { return n === 1 ? 0 : 1; }
  };

  var i18n = {
    lang: "lv",

    init: function () {
      i18n.setLang(App.storage.prefGet("lang", "lv"));
    },

    setLang: function (lang) {
      i18n.lang = STRINGS[lang] ? lang : "lv"; // guard a corrupt pref
    },

    /* t("sum.detail", {time: "2:41", n: 12, total: 58}) — falls back
       lv -> en -> the key itself, so a typo shows up instead of throwing. */
    t: function (key, params) {
      var s = STRINGS[i18n.lang][key];
      if (s === undefined) s = STRINGS.en[key];
      if (s === undefined) return key;
      if (params) {
        s = s.replace(/\{(\w+)\}/g, function (m, name) {
          return name in params ? params[name] : m;
        });
      }
      return s;
    },

    /* Count + agreeing noun: n(58, "unit.streets") -> "58 ielas",
       n(41, "unit.streets") -> "41 iela". */
    n: function (count, key) {
      var forms = STRINGS[i18n.lang][key] || STRINGS.en[key];
      if (!forms) return count + " " + key;
      return count + " " + forms[PLURAL[i18n.lang](count)];
    },

    /* Fixed levels carry i18n keys; per-apkaime and per-novads levels
       keep their data names untranslated. */
    levelName: function (level) {
      return level.nameKey ? i18n.t(level.nameKey) : level.name;
    },
    levelNote: function (level) {
      return level.noteKey ? i18n.t(level.noteKey) : level.note;
    },

    /* Re-translate the static chrome. Elements whose text is always set
       dynamically before display (sum-title, sub-title, …) carry no
       data-i18n attribute on purpose. */
    apply: function () {
      document.documentElement.lang = i18n.lang;
      var els = document.querySelectorAll("[data-i18n]");
      for (var i = 0; i < els.length; i++) {
        els[i].textContent = i18n.t(els[i].getAttribute("data-i18n"));
      }
      els = document.querySelectorAll("[data-i18n-title]");
      for (i = 0; i < els.length; i++) {
        els[i].title = i18n.t(els[i].getAttribute("data-i18n-title"));
      }
      els = document.querySelectorAll("[data-i18n-placeholder]");
      for (i = 0; i < els.length; i++) {
        els[i].placeholder = i18n.t(els[i].getAttribute("data-i18n-placeholder"));
      }
    }
  };

  App.i18n = i18n;
})();
