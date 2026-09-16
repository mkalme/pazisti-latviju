#!/usr/bin/env python3
"""Build data/latvia_data.js (window.LATVIA_DATA = {...}) from data/raw/latvia_*.json.

A countrywide dataset mirroring RIGA_DATA's shape so the game can swap it
in wholesale: the quiz targets (35 novadi + 10 state cities) live in
`hoods`, curated lakes/rivers in `water`, and a top-level `land: 1` flag
tells the renderer to fill every unit as opaque land (sea and foreign
territory stay plain background). streets/ctx/bridges/parks/transit are
present but empty.

Hard-won policies — do not casually undo:
- Geometry is simplified PER MEMBER WAY (cached by way id) BEFORE rings
  are stitched. Douglas-Peucker keeps way endpoints, so a border way
  shared by two units comes out bit-identical on both sides: adjacent
  land fills tile without slivers or background peeking through. (The
  Riga pipeline simplifies per assembled ring instead; harmless at 12 m
  tolerance, ugly at 80 m.)
- Novadi relations carry enclave state cities (Rēzekne, Daugavpils, ...)
  as `inner` members; those rings are KEPT so even-odd fill and hit
  testing exclude the city automatically.
- The first-level units (admin_level=5: 7 valstspilsētas + 35 novadi)
  are sorted by the Latvian alphabet; the 3 titular state cities that are
  NOT separate territories (Jēkabpils, Ogre, Valmiera; admin_level=7) are
  APPENDED after them so they paint on top of their parent novadi. Hit
  testing resolves the overlap smallest-first in js/geometry.js.
"""
import json
import math
import re
import sys
from pathlib import Path

from geo import (simplify, round_pts, bbox_of, bbox_union, bbox_overlap,
                 ring_area, point_in_ring, stitch_rings)

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
OUT_PATH = BASE / "data" / "latvia_data.js"

LAT0 = 56.88  # mid-Latvia; Riga's 56.9715 would skew E-W scale at the edges
K_X = 111320.0 * math.cos(math.radians(LAT0))  # meters per degree lon
K_Y = 111132.0                                 # meters per degree lat

UNIT_TOL = 80.0       # Douglas-Peucker tolerance for admin borders, meters
WATER_TOL = 60.0
RIVER_TOL = 250.0     # river meanders are sub-pixel at country zoom
ROAD_TOL = 120.0
QUIZ_RING_TOL = 200.0  # lake/nature QUIZ outlines: dense 60 m shorelines
#                        stroke as fuzz at country zoom
STITCH_TOL = 1.0      # ring endpoint matching, meters
MIN_WATER_AREA = 2e6  # m^2 decor gate — the broad fetch has 2k+ bodies
RIVER_POLY_MIN = 5e4  # …but river/canal polygons come as CHAINS of small
#                       pieces: gate them gently or the blue river chops up
TOP_LAKES = 20        # lake quiz size (largest by area, reservoirs excluded)

# State cities that exist only as titular cities inside a novads. All three
# must be present in latvia_cities.json or the build aborts.
TITULAR_CITIES = {"Jēkabpils", "Ogre", "Valmiera"}

# Build-time collation (stdlib has no locale collator; a plain sort would
# put "Ādažu novads" last). Non-letters sort before letters, as usual.
LV_ALPHABET = "aābcčdeēfgģhiījkķlļmnņoprsštuūvzž"
LV_ORDER = {ch: i for i, ch in enumerate(LV_ALPHABET)}


def lv_key(name):
    return [(1, LV_ORDER[ch]) if ch in LV_ORDER else (0, ord(ch))
            for ch in name.lower()]


def _d(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _len(seg):
    return sum(_d(seg[k], seg[k + 1]) for k in range(len(seg) - 1))


def merge_exact(segs):
    """Splice polylines that share an exact endpoint used by exactly two
    of them (consecutive OSM ways share nodes, so most joints are exact).
    Junction endpoints (3+ ways) are left alone."""
    segs = [list(s) for s in segs]
    changed = True
    while changed:
        changed = False
        ends = {}
        for i, s in enumerate(segs):
            for p in (tuple(s[0]), tuple(s[-1])):
                ends.setdefault(p, []).append(i)
        for p, ids in ends.items():
            ids = list(dict.fromkeys(ids))
            if len(ids) != 2:
                continue
            i, j = ids
            a, b = segs[i], segs[j]
            if tuple(a[0]) == p:
                a = a[::-1]
            if tuple(b[-1]) == p:
                b = b[::-1]
            if tuple(a[-1]) == p and tuple(b[0]) == p:
                segs[i] = a + b[1:]
                del segs[j]
                changed = True
                break
    return segs


def dominant_chain(segs, join, chain_gap, keep_frac=0.4):
    """Heal a named line feature: drop far-away same-named strays (a second
    river called Brasla, a mistagged A3 stub near Madona), then merge the
    remaining pieces end-to-end — nearest endpoints first, with straight
    connectors bridging real mapping gaps (the Daugava has no centerline
    for 3.3 km near Jēkabpils; the upper Gauja for 15.5 km)."""
    segs = merge_exact(segs)
    if len(segs) <= 1:
        return segs
    # components by endpoint proximity, keep those near the longest's size
    par = list(range(len(segs)))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if min(_d(a, b) for a in (segs[i][0], segs[i][-1])
                   for b in (segs[j][0], segs[j][-1])) <= join:
                par[find(i)] = find(j)
    comps = {}
    for i in range(len(segs)):
        comps.setdefault(find(i), []).append(i)
    lengths = {k: sum(_len(segs[i]) for i in ids) for k, ids in comps.items()}
    best = max(lengths.values())
    pool = [list(segs[i]) for k, ids in comps.items()
            if lengths[k] >= keep_frac * best for i in ids]
    # greedy end-to-end merging across gaps up to chain_gap. A connector
    # may not exceed the shorter piece it joins: bridging the Daugava's
    # 3.3 km hole between two 167 km halves is right, dragging the line
    # from the Gauja's mouth 15 km back inland to a 2 km oxbow is not —
    # such fragments stay as standalone dashes.
    lens = [_len(s) for s in pool]
    while len(pool) > 1:
        pick = None
        bd = chain_gap
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                limit = min(chain_gap, max(2000.0, min(lens[i], lens[j])))
                for ai in (0, -1):
                    for bi in (0, -1):
                        dm = _d(pool[i][ai], pool[j][bi])
                        if dm <= limit and dm < bd:
                            bd = dm
                            pick = (i, j, ai, bi)
        if pick is None:
            break
        i, j, ai, bi = pick
        a = pool[i] if ai == -1 else pool[i][::-1]
        b = pool[j] if bi == 0 else pool[j][::-1]
        merged = a + b  # the joint doubles as a straight gap connector
        mlen = lens[i] + lens[j] + bd
        pool = [pool[k] for k in range(len(pool)) if k not in (i, j)]
        lens = [lens[k] for k in range(len(lens)) if k not in (i, j)]
        pool.append(merged)
        lens.append(mlen)
    # leftover dashes that never chained are closed oxbow arms and tiny
    # orphans — real geometry, but they read as glitches at country zoom
    keep = max(lens)
    return [pool[k] for k in range(len(pool))
            if lens[k] >= min(3000.0, keep)]


def load(name):
    return json.loads((RAW_DIR / f"{name}.json").read_text())


def main():
    admin_raw = load("latvia_admin")
    carved_raw = load("latvia_cities")
    water_raw = load("latvia_water")
    rivers_raw = load("latvia_rivers")
    roads_raw = load("latvia_roads")
    castles_raw = load("latvia_castles")
    nature_raw = load("latvia_nature")
    regions_raw = load("latvia_regions")
    pagasti_raw = load("latvia_pagasti")

    # Projection origin over ALL geometry so every coordinate is positive
    # (ways crossing the border carry their full course, past Latvia).
    lon_min, lat_max = 999.0, -999.0
    for data in (admin_raw, carved_raw, water_raw, rivers_raw, roads_raw,
                 castles_raw, nature_raw, regions_raw):
        for el in data["elements"]:
            if "lon" in el:  # plain nodes (castles)
                lon_min = min(lon_min, el["lon"])
                lat_max = max(lat_max, el["lat"])
            for geom in ([el.get("geometry") or []]
                         + [m.get("geometry") or [] for m in el.get("members", [])]):
                for pt in geom:
                    lon_min = min(lon_min, pt["lon"])
                    lat_max = max(lat_max, pt["lat"])

    def project(geom):
        return [((pt["lon"] - lon_min) * K_X, (lat_max - pt["lat"]) * K_Y)
                for pt in geom]

    # Shared per-way simplify cache: units and country consume the same
    # simplified points for the same OSM way — the crack-free-borders policy.
    way_cache = {}

    def way_pts(member):
        ref = member["ref"]
        if ref not in way_cache:
            way_cache[ref] = round_pts(simplify(project(member["geometry"]), UNIT_TOL))
        return way_cache[ref]

    def admin_rings(el, name):
        members = [m for m in el.get("members", [])
                   if m.get("type") == "way" and m.get("geometry")]
        outers = [way_pts(m) for m in members if m.get("role") in ("outer", "")]
        inners = [way_pts(m) for m in members if m.get("role") == "inner"]
        orings, lo = stitch_rings(outers, STITCH_TOL)
        irings, li = stitch_rings(inners, STITCH_TOL)
        if not orings or lo or li:
            sys.exit(f"FATAL: could not close rings for {name}: "
                     f"{len(orings)} outer ring(s), {lo}+{li} leftover piece(s)")
        return [r for r in orings + irings if len(r) >= 4]

    # --- the 42 first-level units, then the 3 carved titular cities ---
    def build_units(raw):
        units = []
        for el in raw["elements"]:
            if el["type"] != "relation":
                continue
            tags = el.get("tags", {})
            name = tags.get("name", f"rel {el['id']}")
            rings = admin_rings(el, name)
            unit = {"name": name, "rings": rings,
                    "bbox": bbox_of([p for r in rings for p in r])}
            if tags.get("border_type") == "city":
                unit["city"] = 1
            units.append(unit)
        units.sort(key=lambda u: lv_key(u["name"]))
        return units

    units = build_units(admin_raw)
    n_cities = sum(1 for u in units if u.get("city"))
    if len(units) != 42 or n_cities != 7:
        sys.exit(f"FATAL: expected 42 first-level units with 7 state cities, "
                 f"got {len(units)} with {n_cities}")
    names = {u["name"] for u in units}
    if "Rīga" not in names or "Varakļānu novads" in names:
        sys.exit("FATAL: unit names look wrong (no Rīga, or pre-2025 Varakļānu novads present)")

    carved = build_units(carved_raw)
    missing = TITULAR_CITIES - {u["name"] for u in carved}
    if missing:
        sys.exit(f"FATAL: titular state cities missing from latvia_cities: {missing}")
    for u in carved:
        u["city"] = 1
    # Carve the titular cities out of their parents: the city's rings are
    # appended to the parent novads as even-odd holes, so fills, answer
    # tints, pulses and hit tests treat them exactly like the enclave
    # state cities OSM already maps with inner roles (Rēzekne, ...).
    # Without this, an answered novads would tint the unanswered city.
    parent_of = {"Jēkabpils": "Jēkabpils novads", "Ogre": "Ogres novads",
                 "Valmiera": "Valmieras novads"}
    by_name = {u["name"]: u for u in units}
    for c in carved:
        if c["name"] in parent_of:
            by_name[parent_of[c["name"]]]["rings"] += c["rings"]
    units += carved  # appended after the 42 first-level units — stable ids

    if len({u["name"] for u in units}) != len(units):
        sys.exit("FATAL: duplicate unit names")
    for i, u in enumerate(units):
        u["id"] = i
        for r in u["rings"]:
            assert r[0] == r[-1], f"unclosed ring in {u['name']}"
    print(f"units: {len(units)} ({n_cities + len(carved)} state cities, "
          f"{len(units) - n_cities - len(carved)} novadi)")

    # --- water: decor layer (area-gated) + the lakes quiz (top N) ---
    def water_rings(el):
        """Simplified rings for a natural=water way/relation, or []."""
        if el["type"] == "way":
            geom = el.get("geometry")
            if not geom:
                return []
            pts = project(geom)
            if (abs(pts[0][0] - pts[-1][0]) > STITCH_TOL
                    or abs(pts[0][1] - pts[-1][1]) > STITCH_TOL):
                return []
            rings = [pts]
        elif el["type"] == "relation":
            outers = [project(m["geometry"]) for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") in ("outer", "")
                      and m.get("geometry")]
            inners = [project(m["geometry"]) for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") == "inner"
                      and m.get("geometry")]
            oring, _ = stitch_rings(outers, STITCH_TOL)
            iring, _ = stitch_rings(inners, STITCH_TOL)
            if not oring:
                return []
            rings = oring + iring
        else:
            return []
        rings = [round_pts(simplify(r, WATER_TOL)) for r in rings]
        return [r for r in rings if len(r) >= 4]

    water = []
    lake_cand = {}  # name -> (outer_area, rings); biggest body per name
    dropped_small = 0
    for el in water_raw["elements"]:
        rings = water_rings(el)
        if not rings:
            continue
        outer_area = max(ring_area(r) for r in rings)
        tags = el.get("tags", {})
        # rivers/canals arrive as CHAINS of small polygon pieces (mostly
        # old-scheme waterway=riverbank) — a flat area gate would chop the
        # blue Daugava into fragments
        is_river = (tags.get("waterway") == "riverbank"
                    or tags.get("water") in ("river", "canal"))
        if outer_area >= (RIVER_POLY_MIN if is_river else MIN_WATER_AREA):
            water.append({"rings": rings})
        else:
            dropped_small += 1
        name = tags.get("name")
        if name and not is_river and tags.get("water") != "reservoir":
            if name not in lake_cand or outer_area > lake_cand[name][0]:
                lake_cand[name] = (outer_area, rings)
    top = sorted(lake_cand.items(), key=lambda kv: -kv[1][0])[:TOP_LAKES]
    # border lakes carry bilingual names ("Riču ezers / возера Рычы");
    # quiz outlines get a coarser pass — 60 m shoreline detail strokes as
    # fuzz at country zoom
    lakes = [{"name": n.split(" / ")[0], "segs": [],
              "rings": [q for q in (round_pts(simplify(r, QUIZ_RING_TOL))
                                    for r in rings) if len(q) >= 4]}
             for n, (_, rings) in top]
    for l in lakes:
        l["bbox"] = bbox_of([p for ring in l["rings"] for p in ring])
    lakes.sort(key=lambda l: lv_key(l["name"]))
    for i, l in enumerate(lakes):
        l["id"] = i
    print(f"water: {len(water)} decor bodies ({dropped_small} small dropped); "
          f"lakes quiz: {len(lakes)}")
    for n, (a, _) in top:
        print(f"  {n}: {a / 1e6:.1f} km²")
    if not any(l["name"].startswith("Lubān") for l in lakes):
        print("WARNING: Lubāns missing from the lake quiz — check the fetch")

    # --- city dot-marker quiz targets, two population tiers ---
    # place=city|town nodes with a population tag; the thresholds are the
    # only curation — the lists themselves always come from OSM.
    TIER_10K = 10000
    TIER_5K = 5000
    DOT_R = 250.0  # tiny 12-gon ring: sub-pixel at country zoom, so the
    #                renderer and hit tests treat it as a dot marker

    def dot_ring(x, y):
        ring = [[round(x + DOT_R * math.cos(2 * math.pi * k / 12)),
                 round(y + DOT_R * math.sin(2 * math.pi * k / 12))]
                for k in range(12)]
        ring.append(list(ring[0]))
        return ring
    places_raw = load("latvia_places")
    best = {}
    bad_pop = 0
    for el in places_raw["elements"]:
        if el["type"] != "node":
            continue
        tags = el.get("tags", {})
        name = tags.get("name")
        try:
            pop = int(tags.get("population", "").replace(" ", "").replace(",", ""))
        except ValueError:
            bad_pop += 1
            continue
        if not name or pop <= 0:
            continue
        if name not in best or pop > best[name][0]:
            best[name] = (pop, el["lon"], el["lat"])
    if "Rīga" not in best:
        sys.exit("FATAL: Rīga missing from latvia_places — population tags look wrong")
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])

    def city_items(min_pop):
        items = []
        for name, (pop, lon, lat) in ranked:
            if pop < min_pop:
                continue
            x, y = project([{"lon": lon, "lat": lat}])[0]
            ring = dot_ring(x, y)
            items.append({"id": len(items), "name": name,
                          "segs": [], "rings": [ring], "bbox": bbox_of(ring)})
        return items

    cities = city_items(TIER_10K)
    cities5k = city_items(TIER_5K)
    cities_all = city_items(1)  # every official town with a population tag
    print(f"cities: {len(cities)} over {TIER_10K}, {len(cities5k)} over "
          f"{TIER_5K}, {len(cities_all)} in total "
          f"({bad_pop} unparsable population tags)")
    for name, (pop, _, _) in ranked:
        print(f"  {name}: {pop}")
    if not 12 <= len(cities) <= 30:
        print(f"WARNING: expected roughly 15-20 cities over 10k, got {len(cities)}")
    if not 25 <= len(cities5k) <= 60:
        print(f"WARNING: expected roughly 30-45 cities over 5k, got {len(cities5k)}")
    if not 60 <= len(cities_all) <= 120:
        print(f"WARNING: expected ~81 towns in total, got {len(cities_all)}")
    if len(cities) < 8 or len(cities5k) <= len(cities) \
            or len(cities_all) <= len(cities5k):
        sys.exit("FATAL: city tiers look wrong — check latvia_places data")

    # --- rivers: named ways grouped per river. Whole-river relations are
    # NOT used — Daugava and Gauja have none; ways are the uniform source,
    # and cross-border gaps simply become separate segs. ---
    # the channelized lower Pededze, down to the Aiviekste confluence
    # (the mapped Aiviekste passes 0.5 km from Jaunpededze's end — the
    # two visually merge near Lubāns, which is the real hydrology)
    RIVER_ALIAS = {"Jaunpededze": "Pededze"}
    river_groups = {}
    for el in rivers_raw["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        name = RIVER_ALIAS.get(name, name)
        seg = round_pts(simplify(project(el["geometry"]), RIVER_TOL))
        if len(seg) >= 2:
            river_groups.setdefault(name, []).append(seg)
    # same-named different rivers of COMPARABLE length defeat the largest-
    # component rule — pin those to a locator point (Kurzeme's Saka at
    # Pāvilosta, not the Daugava side arm at Jēkabpils)
    RIVER_LOCK = {"Saka": (56.87, 21.19)}
    # Approximate mean discharge (m³/s) — not in OSM; only the RELATIVE
    # magnitude matters, it scales the drawn line width (log-mapped)
    RIVER_FLOW = {
        "Daugava": 678, "Lielupe": 106, "Venta": 95, "Gauja": 72,
        "Aiviekste": 59, "Salaca": 33, "Dubna": 28, "Abava": 27,
        "Ogre": 19, "Irbe": 17, "Bārta": 15, "Mēmele": 15, "Mūsa": 11,
        "Saka": 11, "Pededze": 10, "Iecava": 7, "Rēzekne": 6, "Svēte": 5,
        "Amata": 4.5, "Brasla": 4, "Tebra": 4, "Durbe": 3,
    }
    rivers = []
    for name in sorted(river_groups, key=lv_key):
        # component join = chain gap: a short legitimate continuation near
        # the main course (Zvidzienas kanāls) must survive the stray
        # filter, which only judges genuinely DISTANT same-named pieces
        segs = dominant_chain(river_groups[name], 20000, 20000)
        if name in RIVER_LOCK:
            lat, lon = RIVER_LOCK[name]
            lx, ly = project([{"lon": lon, "lat": lat}])[0]
            segs = [s for s in segs
                    if min(_d((lx, ly), p) for p in s) <= 30000]
        lw = round(1.55 + 0.75 * math.log10(RIVER_FLOW.get(name, 5.0)), 1)
        rivers.append({"id": len(rivers), "name": name, "segs": segs,
                       "rings": [], "lw": lw,
                       "bbox": bbox_of([p for s in segs for p in s])})

    # Orient each river's MAIN chain source->mouth so the renderer can
    # taper the drawn width along the flow. Mouths are detectable — they
    # touch the recipient river or lake; sea outlets (and rivers where
    # BOTH ends touch water: Aiviekste runs Lubāns->Daugava, Rēzekne
    # Rāznas->Lubāns) carry explicit coordinates instead.
    RIVER_MOUTH = {
        "Daugava": (57.06, 24.02), "Gauja": (57.16, 24.27),
        "Lielupe": (57.01, 23.93), "Venta": (57.40, 21.53),
        "Salaca": (57.75, 24.36), "Irbe": (57.60, 21.72),
        "Saka": (56.89, 21.17),
        "Aiviekste": (56.61, 25.75),
        "Rēzekne": (56.72, 26.95),
        "Dubna": (56.36, 26.17),  # rises from quiz-lake Sīvers, so its
        #                           SOURCE touches lake points too
    }
    lake_pts = [p for l in lakes for ring in l["rings"] for p in ring]
    for r in rivers:
        seg = max(r["segs"], key=_len)
        if r["name"] in RIVER_MOUTH:
            lat, lon = RIVER_MOUTH[r["name"]]
            mpt = project([{"lon": lon, "lat": lat}])[0]
            def endscore(p, mpt=mpt):
                return _d(p, mpt)
        else:
            other = [p for o in rivers if o is not r
                     for s in o["segs"] for p in s] + lake_pts
            def endscore(p, other=other):
                return min(_d(p, q) for q in other)
        if endscore(seg[0]) < endscore(seg[-1]):
            seg.reverse()
    print(f"rivers: {len(rivers)} "
          f"({sum(len(s) for r in rivers for s in r['segs'])} pts)")
    if len(rivers) < 15:
        print(f"WARNING: expected ~20 rivers, got {len(rivers)}: "
              f"{sorted(river_groups)}")

    # --- main highways A1..A15 (strict — OSM carries junk A-refs too).
    # The hint is the official route name: the most frequent way name
    # containing a dash, so town street names ("Rīgas iela") never win. ---
    road_groups = {}
    road_names = {}
    for el in roads_raw["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        tags = el.get("tags", {})
        ref = tags.get("ref", "")
        if not re.match(r"^A([1-9]|1[0-5])$", ref):
            continue
        seg = round_pts(simplify(project(el["geometry"]), ROAD_TOL))
        if len(seg) >= 2:
            road_groups.setdefault(ref, []).append(seg)
        nm = tags.get("name")
        # Route names, not town streets: dashed itineraries first, bypass/
        # highway names ("Rēzeknes apvedceļš") as the fallback tier
        if nm and ("—" in nm or " - " in nm or "apvedceļš" in nm
                   or "šoseja" in nm):
            road_names.setdefault(ref, {})
            road_names[ref][nm] = road_names[ref].get(nm, 0) + 1
    roads = []
    for ref in sorted(road_groups, key=lambda r: int(r[1:])):
        names = road_names.get(ref, {})
        segs = dominant_chain(road_groups[ref], 3000, 3000)
        roads.append({"id": len(roads), "name": ref,
                      "hint": max(names, key=names.get) if names else "",
                      "segs": segs, "rings": [],
                      "bbox": bbox_of([p for s in segs for p in s])})
    print(f"roads: {len(roads)} "
          f"({sum(len(s) for r in roads for s in r['segs'])} pts)")
    for r in roads:
        print(f"  {r['name']}: {r['hint']}")
    if len(roads) != 15:
        print(f"WARNING: expected exactly A1..A15, got {len(roads)}")
    if len(roads) < 10:
        sys.exit("FATAL: main road set looks wrong")

    # --- castles & palaces: historic=castle points (palaces are
    # castle_type=palace/stately under the same tag). The name pattern
    # drops what slipped in under that tag but is no castle: fortress
    # sub-elements (redouts/lunettes), earthworks, lone towers, hillfort
    # mounds, manors, museums and "castle site" markers. CASTLE_EXCLUDE
    # holds judgment calls the pattern cannot catch. ---
    CASTLE_DROP = re.compile(
        "muiž|skansts|reduts|roduts|lunete|vieta|tornis|namiņ|muzejs|"
        "cietok|pilskaln")
    CASTLE_EXCLUDE = {
        "Cēsu pilsdrupas",     # same site as "Cēsu pils komplekss"
        "Mazā Mežotnes pils",  # the manor annex opposite Mežotnes pils
        "Bruņinieku pils",     # generic name, ambiguous target
    }
    castle_pts = {}
    for el in castles_raw["elements"]:
        name = el.get("tags", {}).get("name")
        if (not name or name in CASTLE_EXCLUDE or name in castle_pts
                or CASTLE_DROP.search(name)):
            continue
        if el["type"] == "node":
            x, y = project([{"lon": el["lon"], "lat": el["lat"]}])[0]
        else:
            flat = [p for g in ([el.get("geometry") or []]
                                + [m.get("geometry") or []
                                   for m in el.get("members", [])])
                    for p in project(g)]
            if not flat:
                continue
            bb = bbox_of(flat)
            x, y = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        castle_pts[name] = (x, y)
    castles = []
    for name in sorted(castle_pts, key=lv_key):
        ring = dot_ring(*castle_pts[name])
        castles.append({"id": len(castles), "name": name, "segs": [],
                        "rings": [ring], "bbox": bbox_of(ring)})
    print(f"castles: {len(castles)}")
    for c in castles:
        print(f"  {c['name']}")
    if len(castles) < 15:
        sys.exit("FATAL: too few castles — check latvia_castles data")

    # --- national parks & strict reserves, by NAME pattern (tags cannot
    # be trusted: Slītere NP is a boundary WAY, the rezervāti are ways,
    # one of them tagged leisure=nature_reserve only). Relations first so
    # a same-named legacy way never shadows the proper multipolygon. ---
    NATURE_RE = re.compile("nacionālais parks|dabas rezervāts")
    NATURE_EXCLUDE = {
        # historic core zones inside today's national parks, not part of
        # the official 4 NP + 4 rezervāti set
        "Slīteres dabas rezervāts",
        "Lielā Ķemeru tīreļa dabas rezervāts",
        "Krievu salas dabas rezervāts",
    }
    nature = []
    seen_nature = set()
    ordered = ([e for e in nature_raw["elements"] if e["type"] == "relation"]
               + [e for e in nature_raw["elements"] if e["type"] == "way"])
    for el in ordered:
        name = el.get("tags", {}).get("name", "")
        if (not NATURE_RE.search(name) or name in seen_nature
                or name in NATURE_EXCLUDE):
            continue
        if el["type"] == "way":
            geom = el.get("geometry")
            if not geom:
                continue
            pts = project(geom)
            if (abs(pts[0][0] - pts[-1][0]) > STITCH_TOL
                    or abs(pts[0][1] - pts[-1][1]) > STITCH_TOL):
                print(f"  note: skipped {name} (open way)")
                continue
            oring = [pts]
            iring = []
        else:
            outers = [project(m["geometry"]) for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") in ("outer", "")
                      and m.get("geometry")]
            inners = [project(m["geometry"]) for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") == "inner"
                      and m.get("geometry")]
            oring, _ = stitch_rings(outers, STITCH_TOL)
            iring, _ = stitch_rings(inners, STITCH_TOL)
        rings = [round_pts(simplify(r, QUIZ_RING_TOL)) for r in oring + iring]
        rings = [r for r in rings if len(r) >= 4]
        if not oring or not rings:
            print(f"  note: skipped {name} (unclosed rings)")
            continue
        seen_nature.add(name)
        nature.append({"name": name, "segs": [], "rings": rings,
                       "bbox": bbox_of([p for r in rings for p in r])})
    nature.sort(key=lambda n: lv_key(n["name"]))
    for i, n in enumerate(nature):
        n["id"] = i
    print(f"nature: {len(nature)}")
    for n in nature:
        print(f"  {n['name']}")
    if len(nature) != 8:
        print(f"WARNING: expected 4 national parks + 4 rezervāti = 8, got {len(nature)}")
    if len(nature) < 4:
        sys.exit("FATAL: protected-area set looks wrong")

    # --- the five historical lands (kultūrvēsturiskās zemes): OSM maps
    # them as boundary=traditional relations following the 2021 law ---
    REGION_NAMES = {"Kurzeme", "Vidzeme", "Zemgale", "Latgale", "Sēlija"}
    regions = []
    for el in regions_raw["elements"]:
        if el["type"] != "relation":
            continue
        name = el.get("tags", {}).get("name", "")
        if name not in REGION_NAMES:
            continue
        outers = [project(m["geometry"]) for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role") in ("outer", "")
                  and m.get("geometry")]
        inners = [project(m["geometry"]) for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role") == "inner"
                  and m.get("geometry")]
        oring, lo = stitch_rings(outers, STITCH_TOL)
        iring, li = stitch_rings(inners, STITCH_TOL)
        rings = [round_pts(simplify(r, QUIZ_RING_TOL)) for r in oring + iring]
        rings = [r for r in rings if len(r) >= 4]
        if not oring or lo or li or not rings:
            sys.exit(f"FATAL: could not close rings for region {name}")
        regions.append({"name": name, "segs": [], "rings": rings,
                        "bbox": bbox_of([p for r in rings for p in r])})
    regions.sort(key=lambda r: lv_key(r["name"]))
    for i, r in enumerate(regions):
        r["id"] = i
    print(f"regions: {len(regions)}: "
          + ", ".join(r["name"] for r in regions))
    if len(regions) != 5:
        sys.exit("FATAL: expected the 5 historical lands")

    # --- the full second-level mosaic: pagasti + towns + state cities ---
    # Its own per-way cache at a coarser tolerance (593 territories would
    # weigh megabytes at UNIT_TOL); the 7 state cities are REBUILT from
    # this cache so their borders tile crack-free with adjacent pagasti.
    PAGASTS_TOL = 100.0     # cap: even the big Latgale pagasti stay decent
    PAGASTS_MIN_TOL = 25.0  # floor: small Pierīga pagasti keep real shape
    pag_sources = ([e for e in pagasti_raw["elements"] if e["type"] == "relation"]
                   + [e for e in admin_raw["elements"]
                      if e["type"] == "relation"
                      and e.get("tags", {}).get("border_type") == "city"])
    # ADAPTIVE tolerance: each way simplifies for the SMALLEST territory
    # it borders — per-novads levels zoom deep into small units, where a
    # flat 150 m looks chunky. One cache still keeps shared borders
    # bit-identical, so the mosaic stays crack-free.
    way_min_size = {}
    for el in pag_sources:
        lons = [pt["lon"] for m in el.get("members", [])
                for pt in m.get("geometry") or []]
        lats = [pt["lat"] for m in el.get("members", [])
                for pt in m.get("geometry") or []]
        if not lons:
            continue
        size = ((max(lons) - min(lons)) * K_X + (max(lats) - min(lats)) * K_Y)
        for m in el.get("members", []):
            if m.get("type") == "way" and m.get("geometry"):
                ref = m["ref"]
                way_min_size[ref] = min(way_min_size.get(ref, 1e18), size)
    pag_cache = {}

    def pag_way_pts(member):
        ref = member["ref"]
        if ref not in pag_cache:
            tol = min(PAGASTS_TOL,
                      max(PAGASTS_MIN_TOL, way_min_size.get(ref, 1e18) / 300))
            pag_cache[ref] = round_pts(simplify(project(member["geometry"]), tol))
        return pag_cache[ref]

    def pag_rings(el):
        members = [m for m in el.get("members", [])
                   if m.get("type") == "way" and m.get("geometry")]
        outers = [pag_way_pts(m) for m in members
                  if m.get("role") in ("outer", "")]
        inners = [pag_way_pts(m) for m in members if m.get("role") == "inner"]
        orings, lo = stitch_rings(outers, STITCH_TOL)
        irings, li = stitch_rings(inners, STITCH_TOL)
        if not orings or lo or li:
            return None
        return [r for r in orings + irings if len(r) >= 4]

    def inside_evenodd(x, y, rings):
        return sum(point_in_ring(x, y, r) for r in rings) % 2 == 1

    def grid_inside(item, n=13):
        b = item["bbox"]
        pts = []
        for i in range(n):
            for j in range(n):
                x = b[0] + (b[2] - b[0]) * (i + 0.5) / n
                y = b[1] + (b[3] - b[1]) * (j + 0.5) / n
                if inside_evenodd(x, y, item["rings"]):
                    pts.append((x, y))
        return pts

    def assign_parent(item):
        """The novads whose OUTER ring holds most of the item's area.
        Interior samples are area-true — a vertex-mean 'centroid' is
        density-biased and once dropped Olaines pagasts into Ķekavas
        novads. Outer-ring test so enclave holes don't repel members."""
        samples = grid_inside(item)
        best_id, best_k = -1, 0
        for u in units:
            if u.get("city") or not bbox_overlap(item["bbox"], u["bbox"]):
                continue
            ring = max(u["rings"], key=lambda r: abs(ring_area(r)))
            k = sum(1 for (x, y) in samples if point_in_ring(x, y, ring))
            if k > best_k:
                best_k, best_id = k, u["id"]
        return best_id

    pagasti = []
    pag_failed = []
    for el in pag_sources:
        tags = el.get("tags", {})
        name = tags.get("name", f"rel {el['id']}")
        rings = pag_rings(el)
        if rings is None:
            pag_failed.append(name)
            continue
        item = {"name": name, "rings": rings,
                "bbox": bbox_of([p for r in rings for p in r])}
        if tags.get("admin_level") != "8":
            item["city"] = 1  # towns and cities: dot-small, magnetic picks
        if tags.get("admin_level") == "5":
            item["nov"] = -2  # state city: assigned by shared border below
            item["_refs"] = [m["ref"] for m in el.get("members", [])
                             if m.get("type") == "way" and m.get("geometry")]
        else:
            item["nov"] = assign_parent(item)
        pagasti.append(item)
    if pag_failed:
        print(f"  note: {len(pag_failed)} second-level units skipped "
              f"(unclosed rings): {pag_failed[:6]}")
    if len(pag_failed) > 10:
        sys.exit("FATAL: too many broken pagasti relations")
    # State cities join the per-novads level of the municipality they
    # share the LONGEST border with — namesakes and enclaves fall out
    # naturally (Rēzekne -> Rēzeknes novads), and Rīga/Jūrmala get a
    # principled Pierīga home. Border length is summed over shared ways.
    way_len = {}
    nov_refs = {}
    unit_id_by_name = {u["name"]: u["id"] for u in units}
    for el in admin_raw["elements"]:
        if el["type"] != "relation":
            continue
        name = el.get("tags", {}).get("name", "")
        is_city = el.get("tags", {}).get("border_type") == "city"
        refs = []
        for m in el.get("members", []):
            if m.get("type") != "way" or not m.get("geometry"):
                continue
            refs.append(m["ref"])
            if m["ref"] not in way_len:
                g = project(m["geometry"])
                way_len[m["ref"]] = _len(g)
        if not is_city and name in unit_id_by_name:
            nov_refs[unit_id_by_name[name]] = set(refs)
    for it in pagasti:
        if it["nov"] != -2:
            continue
        best_id, best_len = -1, 0.0
        crefs = set(it.pop("_refs", []))
        for nid, refs in nov_refs.items():
            shared = sum(way_len.get(r, 0.0) for r in crefs & refs)
            if shared > best_len:
                best_len, best_id = shared, nid
        it["nov"] = best_id
        print(f"  state city {it['name']} -> "
              f"{[u['name'] for u in units if u['id'] == best_id][0] if best_id >= 0 else 'none'}")
    # OSM does not always exclude a town from its pagasts polygon
    # (Carnikava sits wholly inside Carnikavas pagasts; Ādaži overlaps
    # Ādažu pagasts only partially, so bbox containment is NOT a reliable
    # test). Measure the actual overlap with a deterministic interior
    # sample grid and punch any majority-overlapped town into its
    # dominant host as an even-odd hole — then AUDIT what remains, so
    # future data drift fails loudly at build time.
    def overlap_frac(samples, host):
        if not samples:
            return 0.0
        return (sum(1 for (x, y) in samples
                    if inside_evenodd(x, y, host["rings"])) / len(samples))

    carved_towns = []
    town_samples = {}
    for t in pagasti:
        if not t.get("city"):
            continue
        samples = grid_inside(t)
        town_samples[t["name"]] = samples
        best_host, best_frac = None, 0.0
        for host in pagasti:
            if host.get("city") or host is t \
                    or not bbox_overlap(t["bbox"], host["bbox"]):
                continue
            frac = overlap_frac(samples, host)
            if frac > best_frac:
                best_frac, best_host = frac, host
        if best_host is not None and best_frac >= 0.5:
            best_host["rings"] = best_host["rings"] + t["rings"]
            carved_towns.append(f"{t['name']} ({best_frac:.0%} in {best_host['name']})")
    if carved_towns:
        print(f"  carved {len(carved_towns)} overlapping town(s): "
              + "; ".join(carved_towns))
    leftovers = []
    for t in pagasti:
        if not t.get("city"):
            continue
        for host in pagasti:
            if host.get("city") or host is t \
                    or not bbox_overlap(t["bbox"], host["bbox"]):
                continue
            frac = overlap_frac(town_samples[t["name"]], host)
            if frac >= 0.1:
                leftovers.append(f"{t['name']} ~{frac:.0%} inside {host['name']}")
    if leftovers:
        print(f"WARNING: unresolved town/pagasts overlaps: {leftovers}")
    # duplicate pagasts names exist across novadi — disambiguate with the
    # parent so marathon prompts stay unique
    name_count = {}
    for it in pagasti:
        name_count[it["name"]] = name_count.get(it["name"], 0) + 1
    unit_name = {u["id"]: u["name"] for u in units}
    for it in pagasti:
        if name_count[it["name"]] > 1 and it["nov"] >= 0:
            it["name"] += " (" + unit_name[it["nov"]] + ")"
    pagasti.sort(key=lambda p: lv_key(p["name"]))
    for i, it in enumerate(pagasti):
        it["id"] = i
    n_lone = sum(1 for it in pagasti if it["nov"] < 0)
    n_dupes = sum(1 for n, c in name_count.items() if c > 1)
    print(f"pagasti mosaic: {len(pagasti)} territories "
          f"({n_lone} standalone incl. the 7 state cities, "
          f"{n_dupes} duplicated names disambiguated)")
    per_nov = {}
    for it in pagasti:
        per_nov[it["nov"]] = per_nov.get(it["nov"], 0) + 1
    thin = [u["name"] for u in units
            if not u.get("city") and per_nov.get(u["id"], 0) < 2]
    if thin:
        print(f"WARNING: municipalities with under 2 assigned territories "
              f"(no per-novads level): {thin}")
    if not 500 <= len(pagasti) <= 650:
        print("WARNING: expected ~593 second-level territories")
    if n_lone > 12:
        print(f"WARNING: {n_lone} territories got no parent novads")

    # --- emit ---
    all_bb = units[0]["bbox"]
    for u in units[1:]:
        all_bb = bbox_union(all_bb, u["bbox"])
    out = {
        "meta": {"version": 1, "bounds": all_bb,
                 "proj": {"lon_min": lon_min, "lat_max": lat_max, "lat0": LAT0}},
        "hoods": [{"id": u["id"], "name": u["name"], "rings": u["rings"],
                   "bbox": u["bbox"], **({"city": 1} if u.get("city") else {})}
                  for u in units],
        "streets": [], "ctx": [], "bridges": [], "parks": [], "transit": {},
        "water": water,
        "cities": cities,
        "cities5k": cities5k,
        "citiesAll": cities_all,
        "rivers": rivers,
        "lakes": lakes,
        "roads": roads,
        "castles": castles,
        "nature": nature,
        "regions": regions,
        "pagasti": pagasti,
        "land": 1,
    }
    js = "window.LATVIA_DATA=" + json.dumps(out, ensure_ascii=False,
                                            separators=(",", ":")) + ";\n"
    OUT_PATH.write_text(js, encoding="utf-8")
    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH} ({size / 1e6:.2f} MB)")
    if size > 2_200_000:
        print("WARNING: over the ~2.2 MB budget — raise PAGASTS_TOL/"
              "RIVER_TOL or lower MIN_WATER_AREA generosity")


if __name__ == "__main__":
    main()
