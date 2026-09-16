# Riga Streets — agent guide

A Seterra-style geography game for learning Riga: the game names a street /
neighborhood / bridge / park / transit line, the player clicks it on a canvas
map. Vanilla HTML/CSS/JS, **no build step, no dependencies, no Node** — the
page works from `file://`. Data comes from OpenStreetMap via a Python-stdlib
pipeline. UI text is English; all object names are Latvian (UTF-8 diacritics).

## Run / rebuild

- Serve: `python3 tools/serve.py` → http://localhost:8747 (disables HTTP
  caching — always use this, not `http.server`; stale-cache bugs look exactly
  like "my fix didn't work"). Script/CSS URLs also carry `?v=N` — **bump N in
  index.html after changing any JS/CSS/data file** so open tabs can't serve
  stale copies.
- Rebuild data: `python3 tools/fetch_osm.py` (downloads to `data/raw/`,
  skips existing files — delete one to refetch; retries + mirror fallback)
  then `python3 tools/build_data.py` → `data/riga_data.js`
  (`window.RIGA_DATA = {...}`; loaded as a script, never fetched — file:// CORS).
- `data/raw/` is not in git (large, refetchable); `data/riga_data.js` is
  committed so the game runs without touching Overpass.
- Latvia dataset: `python3 tools/fetch_osm.py latvia_` (name-prefix filter —
  fetches only the latvia_* queries and skips the Riga sanity checks) then
  `python3 tools/build_latvia.py` → `data/latvia_data.js`
  (`window.LATVIA_DATA`, committed, same loading rules). Shared geometry
  helpers live in `tools/geo.py`, imported by both builders.

## Architecture (script-tag globals under `window.App`, load order matters — see index.html)

- `js/geometry.js` — point/segment/ring math, label anchors (`labelAnchor`
  for polylines, `hoodAnchor` centroid for rings, `bridgeAnchor` picks).
- `js/spatial.js` — 250 m grid over streets; `query/queryAll/pick` (pick =
  nearest, ties to the SMALLEST feature: a bridge beats the avenue over it).
- `js/view.js` — pan/zoom/pinch; 5 px drag slop so fast clicks never pan;
  clicks resolve at the press point. `panLock`/`zoomLock` disable USER
  input only (programmatic fits/reveals still move); `topInset` (set by
  `ui.showScreen` from the floating bar's height) keeps fits below the
  HUD; `lastFit` remembers the latest `fitBbox` target for "Reset view".
  The ⚙️ settings popover (wired in main.js, prefs-persisted) holds the
  pan/zoom locks, the ONLY sound toggle and Reset view — corner buttons
  are just theme + gear. ↻ Restart lives in the game HUD bar next to
  Skip (`App.restartLevel`).
- `js/renderer.js` — canvas: cached overscanned base layer + light overlay.
  Pan/zoom blit the stale base (low-res mid-gesture) and re-render sharp on
  settle; the frame loop force-redraws before blank ever composites. Dynamic
  colors are **palette keys** ("correct1", "missed", …) resolved per theme at
  draw time — never store resolved colors. Layers: streets (`colorOf`),
  hood quiz (`hoodColorOf`/`hoodQuiz`), generic feature quiz
  (`config.featQuiz = {items, colorOf}` — bridges/parks/transit). Small
  features render as dot markers (`bridgeSmall`). `guide` = the pulsing
  answer during guided reveal.
- Engines (identical Seterra state machine: await → guided → done; 3/2/1/0
  scoring; wrong click flashes + names what was hit; 3 misses or Skip →
  guided pulse the player must click; correct answers advance instantly):
  - `js/game.js` — streets (spatial index, target-first clicks AND hover).
  - `js/hoodgame.js` — neighborhoods AND the Latvia territories quiz (kind
    `latvia`): items come from `level.ids`, hit-testing via
    `App.geom.hoodsAt` (every containing unit, smallest bbox first).
    State cities are magnetic within `App.geom.cityPickTol()` (10 px,
    capped at 4 km world — narrow viewports would otherwise blow the band
    past 10 km): the pick resolves to the near city in hover, clicks,
    wrong-click naming and study alike, unless the point sits exactly
    inside another city. The band applies while a city's NARROW bbox
    dimension is under ~3.5 bands (long-thin Jūrmala needs it as much as
    little Ogre); anything wider — Rīga always, others once zoomed in —
    picks exactly, so bands never swallow the novadi wedged against Rīga.
  - `js/featgame.js` — `App.createFeatureQuiz(getItems)` factory →
    `bridgegame`, `parkgame`, `App.transitGames.{tram,trolleybus,busday,busnight,rail}`,
    and `lvCityGame` (kind `lvcities`: LATVIA_DATA.cities dot markers).
- `js/levels.js` — derives levels from data: specials (majors/all/hoods/
  bridges/parks), transport, 58 per-neighborhood street levels.
- `js/ui.js` — menu card grid w/ canvas thumbnails (cached per theme),
  diacritic-insensitive hood search, HUD, tooltip, summary. Kind-specific
  wording/datasets live in the `KIND_*` lookups — extend those, not ternaries.
- `js/study.js` — study mode; Districts toggle switches hover/pins from
  streets to neighborhoods (forced + locked on streetless datasets).
  `js/storage.js` — localStorage bests (`rigaStreets.v1`) + prefs; Latvia
  level ids are `lv:`-prefixed to keep the flat namespace collision-free.
  `js/sound.js` — WebAudio synth. `js/main.js` — boot, theme, engine
  dispatch via the `ENGINE_BY_KIND` map, and `App.useDataset(d)` — the
  dataset seam (sets App.data + renderer.data + spatial + view bounds/
  minScale): levels carry `ds` (Latvia), `startGame` swaps before
  `engine.start`, `showMenu` always swaps back to Riga. Renderer: a
  `data.land` dataset fills every unit as opaque land over plain bg (no
  sea, no streets) with thinner quiz borders.

## Data model & pipeline policies (tools/build_data.py)

`RIGA_DATA`: `meta` (bounds, equirectangular meters, y-down, integer coords),
`streets` (1.8k entities), `hoods` (58 rings), `bridges`/`parks`
(segs+rings features), `transit` ({tram, trolleybus, busDay, busNight, rail}),
`ctx` (unnamed roads, draw-only), `water`.

Hard-won policies — do not casually undo:
- **A street is reconstructed, not just grouped by name**: compound border
  names split ("A / B"), cross-boundary ways merged, named ways of minor
  classes only *complete* existing streets, disconnected components healed by
  near-direct chains of other ways. **Exclusive ownership**: every stretch
  belongs to ONE street (smaller street wins transfers) so two streets can
  never highlight together. Remaining gaps are real (construction etc.).
- Bridges = the healed street geometry where names coincide + outlines/
  railway decks from a dedicated harvest. Parks = `leisure=park|garden` with
  a `PARK_EXCLUDE` curation set. Transit = OSM route relations filtered to
  Rīgas satiksme, grouped per line with both directions merged and **variant
  runs (depot trips, short workings) trimmed to the dominant termini pair**;
  member-way geometry is fetched deduplicated.

`LATVIA_DATA` (tools/build_latvia.py): same shape, so the runtime swaps it
in wholesale — the 45 quiz units live in `hoods` (35 novadi + 10 state
cities, `city:1` flags), `land:1` is the render flag, streets/ctx/bridges/
parks/transit are empty, `water` is a curated named-lakes/rivers set
(rivers stay patchy — most riverbank polygons are unnamed; lakes anchor
the map), and `cities`/`cities5k` hold the dot-quiz targets in two population
tiers (place=city|town nodes filtered by `TIER_10K`/`TIER_5K`; each is a
tiny 12-gon ring so featgame/renderer treat it as a dot marker — the
lists themselves are never hardcoded). City-dot quizzes use widened pick
radii (`CITY_PICK` in featgame.js) and larger dots on `land` maps. Hard-won policies:
- Units are OSM **admin_level=5** relations (7 valstspilsētas + 35 novadi;
  counts asserted — Varakļānu novads is gone since 2025, never hardcode the
  list). The 3 titular state cities (Jēkabpils, Ogre, Valmiera) are
  admin_level=7 relations appended as ids 42–44 AND cut into their parent
  novadi as even-odd **holes**, making them behave exactly like the enclave
  cities OSM maps with inner roles (Rēzekne, Daugavpils, Jelgava) — an
  answered novads must never tint its unanswered city.
- **Simplify per member way (cached by way id) BEFORE stitching rings** —
  unlike the Riga per-ring pipeline. Shared borders stay bit-identical on
  both sides, so adjacent opaque land fills tile without slivers at the
  80 m tolerance (per-ring simplification is invisible at Riga's 12 m,
  ugly here).

## Verifying changes

Use the browser pane on the dev server with a fresh query URL
(`/index.html?fresh=N`). Standard sweep: play one correct/wrong/skip/guided
cycle in the affected mode, then one click in each other mode (engines reset
each other via `App.showMenu`), check `read_console_messages` is clean, and
screenshot anything visual. Pointer coordinates from `getBoundingClientRect`
must be scaled by `screenshotWidth / window.innerWidth` before `computer`
clicks.
