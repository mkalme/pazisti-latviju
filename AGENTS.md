# Pazīsti Latviju — agent guide

A Seterra-style geography game for learning Latvia and Riga: the game names
a street / neighborhood / bridge / park / transit line / municipality /
city / river / lake / …, the player clicks it on a canvas map. Vanilla
HTML/CSS/JS, **no build step, no dependencies, no Node** — the page works
from `file://`. Data comes from OpenStreetMap via a Python-stdlib pipeline.
UI text is LATVIAN by default with English behind the ⚙️ language switcher
(`js/i18n.js`); all object names are Latvian (UTF-8 diacritics) and are
never translated.

## Run / rebuild

- Serve: `python3 tools/serve.py` → http://localhost:8747 (disables HTTP
  caching — always use this, not `http.server`; stale-cache bugs look exactly
  like "my fix didn't work"). Script/CSS URLs also carry `?v=N` — **bump N in
  index.html after changing any JS/CSS/data file** so open tabs can't serve
  stale copies.
- Rebuild data: `python3 tools/fetch_osm.py` (downloads to `data/raw/`,
  skips existing files — delete one to refetch; retries + mirror fallback)
  then `python3 tools/build_data.py` → `data/riga_data.js`
  (`window.RIGA_DATA = {...}`; loaded as a script, never fetched — file:// CORS),
  then `python3 tools/check_data.py` — the street-quality regression gate
  (see Data policies below); a rebuild is not done until it passes.
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
  clicks resolve at the press point. Moving past the slop makes the
  gesture a drag and CANCELS the click even under `panLock` (the lock
  only keeps the map still). `fastClick` ("Ātrais režīms", pref `fast`)
  fires the click on mouse-down — mouse-only and only while `panLock` is
  on (its checkbox is disabled otherwise: with panning on, a press may be
  a drag start). `panLock`/`zoomLock` disable USER input only
  (programmatic fits/reveals still move); `topInset` (set by
  `ui.showScreen` from the floating bar's height) keeps fits below the
  HUD; `lastFit` remembers the latest `fitBbox` target for "Reset view".
  The ⚙️ settings popover (wired in main.js, prefs-persisted) holds the
  pan/zoom locks, the ONLY sound toggle, fast mode, the language select
  and Reset view — corner buttons are just theme + gear. ↻ Restart lives
  in the game HUD bar next to Skip (`App.restartLevel`).
- `js/i18n.js` — `App.i18n`: the `STRINGS` table (lv + en), `t(key,
  params)` with `{x}` substitution and lv→en→key fallback, `n(count,
  unitKey)` count agreement (LV singular at n%10==1 && n%100!=11),
  `levelName/levelNote` (fixed levels carry `nameKey`/`noteKey`;
  per-apkaime and per-novads levels keep data names), and `apply()` for
  the `data-i18n`/`data-i18n-title`/`data-i18n-placeholder` markup
  (authored in Latvian). `App.setLang` (main.js) re-applies chrome,
  rebuilds the menu and refreshes the HUD level name in place. The brand
  "Pazīsti Latviju" is invariant and lives directly in the markup.
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
    past 10 km): the pick resolves to the near city in hover, clicks and
    wrong-click naming alike, unless the point sits exactly
    inside another city. The band applies while a city's NARROW bbox
    dimension is under ~3.5 bands (long-thin Jūrmala needs it as much as
    little Ogre); anything wider — Rīga always, others once zoomed in —
    picks exactly, so bands never swallow the novadi wedged against Rīga.
  - `js/featgame.js` — `App.createFeatureQuiz(getItems, opts)` factory →
    `bridgegame`, `parkgame`, `App.transitGames.{tram,trolleybus,busday,busnight,rail}`,
    and the Latvia instances `lvCityGame`/`lvCityGame5k` (dot markers,
    `CITY_PICK` radius), `lvRiverGame`/`lvRoadGame` (lines),
    `lvLakeGame`/`lvNatureGame` (areas), `lvCastleGame` (dots).
- `js/levels.js` — derives levels from data: specials (majors/all/hoods/
  bridges/parks), transport, 58 per-neighborhood street levels.
- `js/ui.js` — menu card grid w/ canvas thumbnails (cached per theme),
  diacritic-insensitive hood search, HUD, tooltip, summary. Kind-specific
  wording/datasets live in the `KIND_*` lookups (wording tables hold i18n
  KEYS, resolved at render time) — extend those, not ternaries.
  `js/storage.js` — localStorage bests (`rigaStreets.v1`) + prefs
  (`rigaStreets.prefs`: theme/muted/pan/zoom/fast/lang — the
  `rigaStreets.*` key names are KEPT after the rebrand on purpose, or
  saved bests/prefs would be orphaned); Latvia
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
  classes only *complete* existing streets. Disconnected components are
  healed by **gap-anchored** chains of other ways: a chain may only launch
  within `HEAL_ANCHOR_R` of the two components' closest vertices, must
  arrive within `HEAL_ARRIVE_R` of the far side, and is pruned to the
  ellipse the `1.4 x direct + 60` budget allows around the REAL gap — so it
  can follow a corridor for kilometers (`HEAL_GAP_MAX == CLUSTER_JOIN`) but
  never wander around blocks (free-launch healing once let Baznīcas iela
  absorb 1.2 km of Skolas/Ģertrūdes/Lāčplēša). ONE best chain per pair,
  smallest-gap-first, already-connected pairs skipped, and healing runs in
  `HEAL_ROUNDS` rounds so donors split by transfers re-heal. Unnamed
  foot/cycle paths (`streets_pool_paths`) bridge only gaps <= `PATH_GAP_MAX`.
  Pairs still disconnected with gaps <= `BRIDGE_MAX` (interchanges with no
  direct way) get a straight SYNTHETIC connector that takes nobody's
  pavement. **Exclusive ownership**: every stretch belongs to ONE street. A
  street may take a NAMED stretch only from a street with more total length
  (bridge wins deck), except junction slivers <= `SLIVER_MAX`, and never
  more than `NAMED_CHAIN_MAX` per chain — a long continuation under another
  street's name stays that street's; takes are index intervals and the
  donor keeps the remainder pieces. Same-name components join into ONE
  entity only when a CORRIDOR TEST passes: an ownership-blind route over
  road geometry (paths excluded) between their closest vertices within
  `MERGE_K x gap + MERGE_C` and `CLUSTER_JOIN` — K. Valdemāra continues
  over the Vanšu deck, while Kleistu iela's Babīte branch (no direct road
  link) is a SEPARATE entity so hovering one never lights up the other.
  Leftover non-main clusters under `FRAG_MAX_LEN` and stray patch-only
  clusters are demoted to ctx decor. **Streets end where Riga ends**: the
  58 apkaimes union is the city polygon; an outside stretch survives only
  as a weave reconnecting two in-city parts (Berģu iela), and a finished
  entity with under `CITY_MIN_SHARE` of its length strictly inside is the
  neighbouring municipality's road (Kleistu iela's border-line branch into
  Mārupes novads) — drawn as ctx, never a quiz street. Remaining bare gaps
  are real — rail yards, field crossings, corridors that detour under
  another name.
- **Dual carriageways are collapsed to one centerline**: oneway ways (minus
  roundabouts) chain into strands across junctions (straightest continuation
  wins), anti-parallel strand pairs `DC_MIN..DC_MAX` apart merge into their
  midline, then leftover parallel frontage lanes are absorbed into the new
  centerline and loose endpoints snap onto it. Two-way streets never
  collapse — a loop street's anti-parallel legs are not carriageways.
  AFTER simplification every entity is WELDED (`weld_entity`): an endpoint
  within `WELD_R` of the street's other geometry moves onto it (OSM
  junctions with unshared nodes, collapse cut offsets), and dangling ends
  reach a centerline from up to `DC_MAX`. The weld must stay after
  simplify — simplification moves lines by up to `STREET_TOL` and would
  reopen freshly closed cracks. Then `entity_bridge` enforces the EMIT
  INVARIANT: no entity ships with an internal gap <= `BRIDGE_MAX` — a late
  split from any pass (the crumb drop once severed Aleksandra Čaka iela's
  spine) gets a straight connector at build time, and check_data gates on
  the invariant itself.
  `python3 tools/check_data.py` prints the quality report (absorption
  deltas, residual doubled km, split components) and enforces regression
  gates — run it after every rebuild; it must stay green.
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
lists themselves are never hardcoded). City-dot quizzes use a widened pick
radius (`CITY_PICK` in featgame.js) and larger dots on `land` maps.
Countrywide feature-quiz keys, all OSM-derived with curation constants
beside their code: `rivers` (named waterway ways grouped per river —
Daugava/Gauja have NO whole-river relations, so ways are the uniform
source; `RIVER_TOL`), `lakes` (top `TOP_LAKES` by ring area from the
broad water fetch, reservoirs excluded, bilingual border names trimmed
at " / "), `roads` (A1–A15 by strict ref regex; the hint is the most
frequent dashed itinerary name, falling back to apvedceļš/šoseja names —
plain "most common name" would pick town streets like "Rīgas iela"),
`castles` (historic=castle dots minus `CASTLE_DROP` pattern —
muiža/skansts/cietoksnis parts/tornis/pilskalns/… — and
`CASTLE_EXCLUDE`), `nature` (4 national parks + 4 dabas rezervāti by
NAME pattern with relations preferred over same-named ways: Slītere NP
is a boundary way, and legacy core-zone rezervāts polygons inside the
NPs sit in `NATURE_EXCLUDE`), `regions` (the 5 kultūrvēsturiskās zemes:
`boundary=traditional` relations — NOT the same-named statistical
regions or villages), and `pagasti` — the full 593-piece second-level
mosaic (admin_level=8 parishes + level-7 towns/titular cities, with the
7 state cities REBUILT at `PAGASTS_TOL` so every border shares one
cache and tiles crack-free; each entry carries its parent novads id
`nov` for the per-municipality levels, duplicate pagasts names get the
parent appended, towns are `city`-flagged for magnetic picks). It plays
through hoodgame on a virtual dataset clone (`Object.assign({}, lv,
{hoods: lv.pagasti})` in levels.js) — one marathon card plus a
searchable per-novads section. Menu: the per-novads levels ("Pagasti", folder card in the Latvia row)
and ALL Riga street modes ("Rīgas ielas" folder card in the Rīga
row: majors + whole city + 58 per-hood levels) open as SUBMENU pages
(`setCategory` in ui.js: #menu-root swaps for #menu-sub with a Back
button, title and the search box; Escape goes back; the open category
survives a game round-trip). Pinned top cards stay outside the filters.
Sub-levels (`level.sub` + `focusRings`) run in FOCUS mode: hoodgame
restricts picks to `level.ids` (outside clicks are inert), the renderer
grays non-members (`cfg.focusIds`, alpha 0.35 + faint borders) and
strokes the municipality outline (`cfg.focusRings`). Decor `water` = every named body over
`MIN_WATER_AREA`. Feature
quizzes share ONE hover+click radius and one `pickAt` resolution (hover
shows exactly what a click selects; the asked target wins ties/overlaps but
never beats a strictly nearer feature). Hard-won policies:
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
