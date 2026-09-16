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
import sys
from pathlib import Path

from geo import simplify, round_pts, bbox_of, bbox_union, ring_area, stitch_rings

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
OUT_PATH = BASE / "data" / "latvia_data.js"

LAT0 = 56.88  # mid-Latvia; Riga's 56.9715 would skew E-W scale at the edges
K_X = 111320.0 * math.cos(math.radians(LAT0))  # meters per degree lon
K_Y = 111132.0                                 # meters per degree lat

UNIT_TOL = 80.0       # Douglas-Peucker tolerance for admin borders, meters
WATER_TOL = 60.0
STITCH_TOL = 1.0      # ring endpoint matching, meters
MIN_WATER_AREA = 5e5  # m^2 — at country zoom only big lakes/rivers read

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


def load(name):
    return json.loads((RAW_DIR / f"{name}.json").read_text())


def main():
    admin_raw = load("latvia_admin")
    carved_raw = load("latvia_cities")
    water_raw = load("latvia_water")

    # Projection origin over ALL geometry so every coordinate is positive.
    lon_min, lat_max = 999.0, -999.0
    for data in (admin_raw, carved_raw, water_raw):
        for el in data["elements"]:
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

    # --- water: curated big lakes + river polygons (mirrors the Riga rules) ---
    water = []
    dropped_small = dropped_open = 0
    for el in water_raw["elements"]:
        rings = []
        if el["type"] == "way":
            geom = el.get("geometry")
            if not geom:
                continue
            pts = project(geom)
            if (abs(pts[0][0] - pts[-1][0]) > STITCH_TOL
                    or abs(pts[0][1] - pts[-1][1]) > STITCH_TOL):
                dropped_open += 1
                continue
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
            rings = oring + iring
            if not oring:
                continue
        if not rings:
            continue
        outer_area = max((ring_area(r) for r in rings), default=0.0)
        if outer_area < MIN_WATER_AREA:
            dropped_small += 1
            continue
        rings = [round_pts(simplify(r, WATER_TOL)) for r in rings]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            water.append({"rings": rings})
    print(f"water: {len(water)} bodies ({dropped_small} tiny dropped, "
          f"{dropped_open} open ways skipped)")

    # --- city dot-marker quiz targets, two population tiers ---
    # place=city|town nodes with a population tag; the thresholds are the
    # only curation — the lists themselves always come from OSM.
    TIER_10K = 10000
    TIER_5K = 5000
    DOT_R = 250.0  # tiny 12-gon ring: sub-pixel at country zoom, so the
    #                renderer and hit tests treat it as a dot marker
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
        if not name or pop < TIER_5K:
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
            ring = [[round(x + DOT_R * math.cos(2 * math.pi * k / 12)),
                     round(y + DOT_R * math.sin(2 * math.pi * k / 12))]
                    for k in range(12)]
            ring.append(list(ring[0]))
            items.append({"id": len(items), "name": name,
                          "segs": [], "rings": [ring], "bbox": bbox_of(ring)})
        return items

    cities = city_items(TIER_10K)
    cities5k = city_items(TIER_5K)
    print(f"cities: {len(cities)} over {TIER_10K}, {len(cities5k)} over "
          f"{TIER_5K} ({bad_pop} unparsable population tags)")
    for name, (pop, _, _) in ranked:
        print(f"  {name}: {pop}")
    if not 12 <= len(cities) <= 30:
        print(f"WARNING: expected roughly 15-20 cities over 10k, got {len(cities)}")
    if not 25 <= len(cities5k) <= 60:
        print(f"WARNING: expected roughly 30-45 cities over 5k, got {len(cities5k)}")
    if len(cities) < 8 or len(cities5k) <= len(cities):
        sys.exit("FATAL: city tiers look wrong — check latvia_places data")

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
        "land": 1,
    }
    js = "window.LATVIA_DATA=" + json.dumps(out, ensure_ascii=False,
                                            separators=(",", ":")) + ";\n"
    OUT_PATH.write_text(js, encoding="utf-8")
    size = OUT_PATH.stat().st_size
    print(f"wrote {OUT_PATH} ({size / 1e6:.2f} MB)")
    if size > 600_000:
        print(f"WARNING: over the ~600 KB budget — raise UNIT_TOL ({UNIT_TOL} m)?")


if __name__ == "__main__":
    main()
