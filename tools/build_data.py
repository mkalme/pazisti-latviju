#!/usr/bin/env python3
"""Build data/riga_data.js (window.RIGA_DATA = {...}) from data/raw/*.json.

Pipeline: project lon/lat to planar meters, stitch neighborhood/water rings,
simplify geometry, merge street ways into named entities, assign each entity
to the neighborhoods it passes through.
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
OUT_PATH = BASE / "data" / "riga_data.js"

BASE_CLASSES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "pedestrian",
]
STREET_CLS = {
    "motorway": 0, "trunk": 0, "primary": 0, "secondary": 0,
    "tertiary": 1, "unclassified": 1,
    "residential": 2, "living_street": 2, "pedestrian": 2,
    "motorway_link": 1, "trunk_link": 1, "primary_link": 1,
    "secondary_link": 1, "tertiary_link": 1,
}
RAW_STREET_FILES = [f"streets_{c}" for c in BASE_CLASSES] + [
    "streets_unnamed", "streets_links",
]
PATCH_FILE = "streets_named_extra"  # named service/track/footway/... ways

LAT0 = 56.9715
K_X = 111320.0 * math.cos(math.radians(LAT0))  # meters per degree lon
K_Y = 111132.0                                 # meters per degree lat

STREET_TOL = 3.0      # Douglas-Peucker tolerance, meters
POLY_TOL = 12.0
STITCH_TOL = 1.0      # ring endpoint matching, meters
CLUSTER_MARGIN = 300.0  # same-name ways within this bbox margin = one street
SAMPLE_STEP = 50.0    # hood-assignment sampling interval along streets
MIN_WATER_AREA = 10000.0  # m^2


def load(name):
    return json.loads((RAW_DIR / f"{name}.json").read_text())


# ---------- main pipeline ----------

def main():
    street_raw = {f: load(f) for f in RAW_STREET_FILES}
    hoods_raw = load("apkaimes")
    water_raw = load("water")

    # Projection origin from the full data extent
    lon_min, lat_max = 999.0, -999.0
    def scan(geom):
        nonlocal lon_min, lat_max
        for pt in geom:
            lon_min = min(lon_min, pt["lon"])
            lat_max = max(lat_max, pt["lat"])
    for data in street_raw.values():
        for el in data["elements"]:
            scan(el.get("geometry") or [])
    for data in (hoods_raw, water_raw):
        for el in data["elements"]:
            scan(el.get("geometry") or [])
            for m in el.get("members", []):
                scan(m.get("geometry") or [])

    def project(geom):
        return [((pt["lon"] - lon_min) * K_X, (lat_max - pt["lat"]) * K_Y) for pt in geom]

    # --- neighborhoods ---
    hoods = []
    failed = []
    for el in hoods_raw["elements"]:
        if el["type"] != "relation":
            continue
        name = el["tags"].get("name", f"rel {el['id']}")
        pieces = [project(m["geometry"]) for m in el.get("members", [])
                  if m.get("type") == "way" and m.get("role") in ("outer", "")
                  and m.get("geometry")]
        rings, leftover = stitch_rings(pieces, STITCH_TOL)
        if not rings:
            failed.append(name)
            continue
        if leftover:
            print(f"  note: {name}: {leftover} unstitchable outer piece(s), kept {len(rings)} ring(s)")
        rings = [round_pts(simplify(r, POLY_TOL)) for r in rings]
        rings = [r for r in rings if len(r) >= 4]
        allpts = [p for r in rings for p in r]
        hoods.append({"name": name, "rings": rings, "bbox": bbox_of(allpts)})
    if failed:
        sys.exit(f"FATAL: could not close rings for apkaimes: {failed}")
    hoods.sort(key=lambda h: h["name"])
    for i, h in enumerate(hoods):
        h["id"] = i
    print(f"neighborhoods: {len(hoods)}")

    # --- water ---
    water = []
    dropped_small = dropped_open = 0
    for el in water_raw["elements"]:
        rings = []
        if el["type"] == "way":
            geom = el.get("geometry")
            if not geom:
                continue
            pts = project(geom)
            if abs(pts[0][0] - pts[-1][0]) > STITCH_TOL or abs(pts[0][1] - pts[-1][1]) > STITCH_TOL:
                dropped_open += 1
                continue
            rings = [pts]
        elif el["type"] == "relation":
            outers = [project(m["geometry"]) for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry")]
            inners = [project(m["geometry"]) for m in el.get("members", [])
                      if m.get("type") == "way" and m.get("role") == "inner" and m.get("geometry")]
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
        rings = [round_pts(simplify(r, POLY_TOL)) for r in rings]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            water.append({"rings": rings})
    print(f"water: {len(water)} bodies ({dropped_small} tiny dropped, {dropped_open} open ways skipped)")

    # --- street entities (named) and context roads (unnamed / ramps) ---
    ways = []
    unnamed = []
    for data in street_raw.values():
        for el in data["elements"]:
            if el["type"] != "way" or not el.get("geometry"):
                continue
            cls = STREET_CLS.get(el.get("tags", {}).get("highway"))
            if cls is None:
                continue
            pts = project(el["geometry"])
            if len(pts) < 2:
                continue
            name = el.get("tags", {}).get("name", "").strip()
            if name:
                ways.append({"name": name, "cls": cls, "pts": pts,
                             "bbox": bbox_of(pts)})
            else:
                length = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                             for i in range(len(pts) - 1))
                unnamed.append({"cls": cls, "pts": pts, "bbox": bbox_of(pts),
                                "len": length, "used": False})

    # Unnamed service/track ways join the absorption pool only: they may
    # bridge two same-named street pieces, but are never drawn on their own
    # (unnamed driveways and yard lanes would swamp the map).
    for el in load("streets_pool_minor")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        pts = project(el["geometry"])
        if len(pts) < 2:
            continue
        length = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                     for i in range(len(pts) - 1))
        unnamed.append({"cls": 2, "pts": pts, "bbox": bbox_of(pts),
                        "len": length, "used": False, "pool_only": True})

    # All NAMED ways also join the connector pool — a bridge deck often
    # carries the through-street's name while its ends are named after the
    # bridge (Vanšu tilts vs Krišjāņa Valdemāra iela). When a chain through
    # a named way heals a SMALLER street, that stretch is TRANSFERRED to it
    # (removed from its own street): every piece of road belongs to exactly
    # one street, so two streets can never light up together.
    def pool_add(name, cls, pts, bbox, src):
        length = sum(math.hypot(pts[i + 1][0] - pts[i][0],
                                pts[i + 1][1] - pts[i][1])
                     for i in range(len(pts) - 1))
        unnamed.append({"cls": cls, "pts": pts, "bbox": bbox, "len": length,
                        "used": False, "pool_only": True, "own_name": name,
                        "src": src})

    for w in ways:
        pool_add(w["name"], w["cls"], w["pts"], w["bbox"], w)

    by_name = {}
    for w in ways:
        by_name.setdefault(w["name"], []).append(w)

    # Patch streets with named ways of excluded classes (an unpaved stretch
    # tagged highway=track, a named service alley, ...). Only names that
    # already exist as proper streets are patched — no new quiz entities.
    patched = 0
    for el in load(PATCH_FILE)["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        name = el.get("tags", {}).get("name", "").strip()
        if name not in by_name:
            continue
        pts = project(el["geometry"])
        if len(pts) < 2:
            continue
        w = {"name": name, "cls": 2, "pts": pts, "bbox": bbox_of(pts),
             "patch": True}
        by_name[name].append(w)
        pool_add(name, 2, pts, w["bbox"], w)
        patched += 1
    print(f"patched {patched} named ways of excluded classes into existing streets")

    # Streets can weave across the city boundary (Berģu iela): merge named
    # ways from just outside it into their in-city street — only when they
    # hug that street's own extent, so same-named streets in neighboring
    # towns stay out.
    group_bbox = {}
    for name, group in by_name.items():
        bb = list(group[0]["bbox"])
        for w in group[1:]:
            bb = bbox_union(bb, w["bbox"])
        group_bbox[name] = bb
    PATCH_CLS = {"service": 2, "track": 2, "footway": 2, "cycleway": 2,
                 "path": 2, "steps": 2}
    boundary_added = 0
    for el in load("streets_boundary")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        tags = el.get("tags", {})
        hw = tags.get("highway")
        cls = STREET_CLS.get(hw, PATCH_CLS.get(hw))
        name = tags.get("name", "").strip()
        if cls is None or name not in by_name:
            continue
        pts = project(el["geometry"])
        if len(pts) < 2:
            continue
        bb = group_bbox[name]
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        if not (bb[0] - 350 <= mx <= bb[2] + 350 and bb[1] - 350 <= my <= bb[3] + 350):
            continue
        w = {"name": name, "cls": cls, "pts": pts, "bbox": bbox_of(pts)}
        by_name[name].append(w)
        pool_add(name, cls, pts, w["bbox"], w)
        boundary_added += 1
    print(f"merged {boundary_added} cross-boundary ways into existing streets")

    # Boundary stretches shared with a neighboring municipality can carry a
    # compound name ("Berģu iela / Ezera prospekts"). Fold such ways into each
    # component street that exists on its own; drop the compound pseudo-street.
    for comp_name in [n for n in by_name if " / " in n]:
        parts = [p.strip() for p in comp_name.split(" / ")]
        matched = [p for p in parts if p in by_name]
        if not matched:
            continue
        for w in by_name.pop(comp_name):
            for p in matched:
                by_name[p].append({"name": p, "cls": w["cls"], "pts": w["pts"],
                                   "bbox": w["bbox"]})
        print(f'folded "{comp_name}" into: {", ".join(matched)}')

    # Heal disconnected streets with ONE rule: when two separate CONNECTED
    # COMPONENTS of a street are joined by a nearly direct chain of other
    # road segments — any class, named or unnamed — that chain belongs to the
    # street's drawn geometry (duplicated, never removed from its own street).
    #  - "nearly direct": chain length <= 1.4 x straight-line + 60 m, at most
    #    5 ways and 1 km — so crossing streets cannot be swallowed;
    #  - chains attach and terminate on ANY vertex of the street's own ways;
    #  - the arriving way is CLIPPED at the first street vertex it reaches,
    #    so tails never leak past the street;
    #  - vertices of the SAME component never absorb anything — junction
    #    cross-pieces and dual-carriageway rungs stay out.
    def endkey(pt):
        return (round(pt[0]), round(pt[1]))

    by_end = {}
    vkeys = []
    for idx, u in enumerate(unnamed):
        vkeys.append([endkey(p) for p in u["pts"]])
        for pt in (u["pts"][0], u["pts"][-1]):
            by_end.setdefault(endkey(pt), []).append(idx)

    MAX_CHAIN_WAYS = 5
    MAX_CHAIN_LEN = 1000.0  # meters
    # Street size, for the ownership rule: a street may only take geometry
    # from a LARGER street (the bridge wins the deck; the avenue never takes
    # the bridge and simply ends at it).
    group_diag = {}
    for gname, grp in by_name.items():
        gb = list(grp[0]["bbox"])
        for w in grp[1:]:
            gb = bbox_union(gb, w["bbox"])
        group_diag[gname] = (gb[2] - gb[0]) + (gb[3] - gb[1])

    absorbed_total = 0
    transferred_total = 0
    healed_names = 0
    for name, group in by_name.items():
        # Connected components of the street's own ways (shared vertices)
        parent = list(range(len(group)))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        owner = {}
        for wi, w in enumerate(group):
            for p in w["pts"]:
                k = endkey(p)
                if k in owner:
                    ra, rb = find(owner[k]), find(wi)
                    if ra != rb:
                        parent[ra] = rb
                else:
                    owner[k] = wi
        if len({find(i) for i in range(len(group))}) < 2:
            continue  # street already in one piece — nothing to heal

        ends = {}  # vertex key -> (point, component id)
        for wi, w in enumerate(group):
            c = find(wi)
            for p in w["pts"]:
                ends.setdefault(endkey(p), (p, c))
        absorbed = {}  # ui -> None (whole way) | clipped point list

        def record(ui, clip):
            cur = absorbed.get(ui, False)
            if cur is None:
                return  # already absorbed whole
            if clip is None or cur is False or len(clip) > len(cur):
                absorbed[ui] = clip

        for skey, (spt, scomp) in ends.items():
            stack = [(skey, (), 0.0)]
            seen = {skey}
            while stack:
                pk, chain, tot = stack.pop()
                if len(chain) >= MAX_CHAIN_WAYS:
                    continue
                for ui in by_end.get(pk, []):
                    if ui in chain:
                        continue
                    u = unnamed[ui]
                    own = u.get("own_name")
                    if own == name:
                        continue
                    if own is not None:
                        if own not in by_name:
                            continue  # orphaned (folded compound name)
                        if group_diag.get(name, 0) >= group_diag.get(own, 0):
                            continue  # only smaller streets take from larger
                    vk = vkeys[ui]
                    if vk[0] == pk:
                        forward, other = True, vk[-1]
                    elif vk[-1] == pk:
                        forward, other = False, vk[0]
                    else:
                        continue
                    if tot + u["len"] > MAX_CHAIN_LEN:
                        continue
                    # Walk the conduit from its entry end; the FIRST vertex
                    # belonging to the street is the arrival — the way is
                    # CLIPPED there so its tail cannot leak past the street.
                    pts = u["pts"]
                    order = range(len(vk)) if forward else range(len(vk) - 1, -1, -1)
                    part, prev, arrival = 0.0, None, None
                    for i in order:
                        if prev is not None:
                            part += math.hypot(pts[i][0] - pts[prev][0],
                                               pts[i][1] - pts[prev][1])
                        prev = i
                        if vk[i] != skey and vk[i] in ends:
                            arrival = i
                            break
                    if arrival is not None:
                        apt, acomp = ends[vk[arrival]]
                        if acomp != scomp:
                            direct = math.hypot(apt[0] - spt[0], apt[1] - spt[1])
                            if tot + part <= 1.4 * direct + 60.0:
                                for c in chain:
                                    record(c, None)
                                clip = pts[:arrival + 1] if forward else pts[arrival:]
                                record(ui, None if len(clip) == len(pts) else clip)
                    if other not in seen:
                        seen.add(other)
                        stack.append((other, chain + (ui,), tot + u["len"]))
        for ui, clip in absorbed.items():
            u = unnamed[ui]
            use = u["pts"] if clip is None else clip
            if len(use) < 2:
                continue
            own = u.get("own_name")
            if own is None:
                if clip is None:
                    u["used"] = True  # partial absorptions stay in context
            else:
                # TRANSFER from the larger street: exactly one owner per
                # stretch. First street to claim a way wins; the source way
                # keeps only the remainder beyond the arrival vertex.
                if u.get("transferred"):
                    continue
                u["transferred"] = True
                src = u["src"]
                if clip is None:
                    if src in by_name.get(own, []):
                        by_name[own].remove(src)
                else:
                    n = len(clip)
                    if clip[0] == src["pts"][0]:
                        rem = src["pts"][n - 1:]
                    else:
                        rem = src["pts"][:len(src["pts"]) - n + 1]
                    if len(rem) >= 2:
                        src["pts"] = rem
                        src["bbox"] = bbox_of(rem)
                    elif src in by_name.get(own, []):
                        by_name[own].remove(src)
                transferred_total += 1
            group.append({"name": name, "cls": u["cls"], "pts": use,
                          "bbox": bbox_of(use)})
        absorbed_total += len(absorbed)
        if absorbed:
            healed_names += 1
    print(f"absorbed {absorbed_total} connector ways into {healed_names} split streets "
          f"({transferred_total} transferred from larger streets)")

    context = []
    for u in unnamed:
        if u["used"] or u.get("pool_only"):
            continue
        seg = round_pts(simplify(u["pts"], STREET_TOL))
        if len(seg) >= 2:
            context.append({"c": u["cls"], "s": seg, "b": bbox_of(seg)})
    print(f"context roads (unnamed/ramps): {len(context)} ways")

    streets = []
    multi_entity = []
    for name in sorted(by_name):
        clusters = []
        for w in by_name[name]:
            hits = [c for c in clusters if bbox_overlap(c["bbox"], w["bbox"], CLUSTER_MARGIN)]
            if not hits:
                clusters.append({"bbox": list(w["bbox"]), "ways": [w]})
            else:
                target = hits[0]
                target["ways"].append(w)
                target["bbox"] = bbox_union(target["bbox"], w["bbox"])
                for extra in hits[1:]:
                    target["ways"].extend(extra["ways"])
                    target["bbox"] = bbox_union(target["bbox"], extra["bbox"])
                    clusters.remove(extra)
        # A cluster made only of patch ways is a stray same-named drive or
        # path far from the real street — not a quiz target of its own.
        clusters = [c for c in clusters if any(not w.get("patch") for w in c["ways"])]
        if len(clusters) > 1:
            multi_entity.append((name, len(clusters)))
        for c in clusters:
            segs = [round_pts(simplify(w["pts"], STREET_TOL)) for w in c["ways"]]
            segs = [s for s in segs if len(s) >= 2]
            if not segs:
                continue
            allpts = [p for s in segs for p in s]
            streets.append({
                "name": name,
                "cls": min(w["cls"] for w in c["ways"]),
                "segs": segs,
                "bbox": bbox_of(allpts),
            })
    streets.sort(key=lambda s: (s["name"], s["bbox"][0], s["bbox"][1]))
    for i, s in enumerate(streets):
        s["id"] = i
    print(f"streets: {len(streets)} entities from {len(by_name)} names, {len(ways)} ways")
    if multi_entity:
        print(f"  multi-entity names ({len(multi_entity)}):")
        for name, n in multi_entity:
            print(f"    {name}: {n}")

    # --- neighborhood assignment ---
    def samples_of(street):
        pts_out = []
        for seg in street["segs"]:
            pts_out.append(seg[0])
            carry = 0.0
            for i in range(len(seg) - 1):
                x0, y0 = seg[i]
                x1, y1 = seg[i + 1]
                seg_len = math.hypot(x1 - x0, y1 - y0)
                d = SAMPLE_STEP - carry
                while d < seg_len:
                    t = d / seg_len
                    pts_out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
                    d += SAMPLE_STEP
                carry = (carry + seg_len) % SAMPLE_STEP
        return pts_out

    def hood_contains(h, x, y):
        bb = h["bbox"]
        if not (bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]):
            return False
        return any(point_in_ring(x, y, r) for r in h["rings"])

    unassigned_fallback = 0
    for s in streets:
        pts = samples_of(s)
        counts = {}
        for x, y in pts:
            for h in hoods:
                if hood_contains(h, x, y):
                    counts[h["id"]] = counts.get(h["id"], 0) + 1
                    break
        total = len(pts)
        assigned = [hid for hid, c in counts.items()
                    if c / total >= 0.2 or c * SAMPLE_STEP >= 250.0]
        if counts:
            dom = max(counts, key=counts.get)
        else:
            mx = (s["bbox"][0] + s["bbox"][2]) / 2
            my = (s["bbox"][1] + s["bbox"][3]) / 2
            dom = min(hoods, key=lambda h: (
                ((h["bbox"][0] + h["bbox"][2]) / 2 - mx) ** 2
                + ((h["bbox"][1] + h["bbox"][3]) / 2 - my) ** 2))["id"]
            unassigned_fallback += 1
        if dom not in assigned:
            assigned.append(dom)
        s["hoods"] = sorted(assigned)
        s["dom"] = dom
    if unassigned_fallback:
        print(f"  {unassigned_fallback} streets fell outside all hoods, assigned to nearest")

    per_hood = {h["id"]: 0 for h in hoods}
    for s in streets:
        for hid in s["hoods"]:
            per_hood[hid] += 1
    print("streets per neighborhood:")
    for h in hoods:
        print(f"  {h['name']}: {per_hood[h['id']]}")

    # --- bridges: their own quiz layer (deck lines and/or outline rings) ---
    bcand = {}
    for el in load("bridges")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        t = el.get("tags", {})
        nm = (t.get("bridge:name") or t.get("name", "")).strip()
        if not nm:
            continue
        pts = project(el["geometry"])
        if len(pts) < 2:
            continue
        entry = bcand.setdefault(nm, {"segs": [], "rings": []})
        closed = (abs(pts[0][0] - pts[-1][0]) <= STITCH_TOL and
                  abs(pts[0][1] - pts[-1][1]) <= STITCH_TOL)
        if t.get("man_made") == "bridge" and closed:
            ring = round_pts(simplify(pts, STREET_TOL))
            if len(ring) >= 4:
                entry["rings"].append(ring)
        else:
            seg = round_pts(simplify(pts, STREET_TOL))
            if len(seg) >= 2:
                entry["segs"].append(seg)
    # A bridge that is also a named street uses the street's FINAL healed
    # geometry as its deck (transfers included) — raw harvest pieces are
    # fragmentary; outlines and railway decks stay from the harvest.
    street_by_name = {}
    for st in streets:
        street_by_name.setdefault(st["name"], []).append(st)
    bridges = []
    for nm in sorted(bcand):
        e = bcand[nm]
        segs = e["segs"]
        if nm in street_by_name:
            segs = [seg for ent in street_by_name[nm] for seg in ent["segs"]]
        allpts = ([p for s in segs for p in s]
                  + [p for r in e["rings"] for p in r])
        if not allpts:
            continue
        bridges.append({"id": len(bridges), "name": nm, "segs": segs,
                        "rings": e["rings"], "bbox": bbox_of(allpts)})
    print(f"bridges: {len(bridges)}")

    # --- parks: named leisure=park/garden areas (rings, holes included) ---
    # Names here are dropped from the quiz — edit to taste (OSM tags a few
    # debatable things as parks: the Zoo, sports grounds, memorial cemeteries).
    PARK_EXCLUDE = set()
    pcand = {}
    for el in load("parks")["elements"]:
        t = el.get("tags", {})
        nm = t.get("name", "").strip()
        if not nm or nm in PARK_EXCLUDE:
            continue
        rings = []
        if el["type"] == "way" and el.get("geometry"):
            pts = project(el["geometry"])
            if (len(pts) >= 4
                    and abs(pts[0][0] - pts[-1][0]) <= STITCH_TOL
                    and abs(pts[0][1] - pts[-1][1]) <= STITCH_TOL):
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
            if oring:
                rings = oring + iring
        if not rings:
            continue
        area = max(ring_area(r) for r in rings)
        if area < 1500:  # flower beds, not parks
            continue
        # Parks are studied zoomed-in: keep street-grade edge fidelity
        rings = [round_pts(simplify(r, 2.0)) for r in rings]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            pcand.setdefault(nm, []).extend(rings)
    parks = []
    for nm in sorted(pcand):
        allpts = [p for r in pcand[nm] for p in r]
        parks.append({"id": len(parks), "name": nm, "segs": [],
                      "rings": pcand[nm], "bbox": bbox_of(allpts)})
    print(f"parks: {len(parks)}")

    # --- public transport: one feature per line, grouped from OSM route
    # relations (both directions merged; member ways fetched deduped) ---
    way_geo = {}
    for el in load("transit_ways")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        pts = project(el["geometry"])
        if len(pts) < 2:
            continue
        seg = round_pts(simplify(pts, STREET_TOL))
        if len(seg) >= 2:
            way_geo[el["id"]] = seg

    RS_REF = re.compile(r"^(\d{1,2}[aA]?|N\d{1,2})$")
    MODE_LABEL = {"tram": "Tramvajs", "trolleybus": "Trolejbuss", "bus": "Autobuss"}
    RAIL_BUCKETS = [
        ("Jūrmalas līnija", ("Tukums", "Ķemeri", "Sloka", "Dubulti")),
        ("Jelgavas līnija", ("Jelgava",)),
        ("Daugavpils līnija", ("Aizkraukle", "Lielvārde", "Ogre", "Krustpils",
                               "Daugavpils", "Rēzekne", "Zilupe")),
        ("Skultes līnija", ("Skulte", "Saulkrasti")),
        ("Siguldas līnija", ("Valga", "Sigulda", "Cēsis", "Valmiera")),
    ]

    def rs_operated(t):
        blob = (t.get("operator", "") + " " + t.get("network", "")).lower()
        return "satiksme" in blob

    tgroups = {}
    for el in load("transit_routes")["elements"]:
        if el["type"] != "relation":
            continue
        t = el.get("tags", {})
        mode = t.get("route")
        ref = t.get("ref", "").strip()
        wids = [m["ref"] for m in el.get("members", [])
                if m.get("type") == "way" and m["ref"] in way_geo]
        if not wids:
            continue
        if mode == "train":
            blob = " ".join(filter(None, (t.get("from"), t.get("to"), t.get("name"))))
            hit = next(((nm, kws) for nm, kws in RAIL_BUCKETS
                        if any(k in blob for k in kws)), None)
            if not hit:
                continue
            g = tgroups.setdefault(("rail", hit[0]), {
                "name": hit[0], "hint": ", ".join(hit[1]), "ref": "", "ways": set()})
            g["ways"].update(wids)
        else:
            # City lines only: Rīgas satiksme operator or a city-style ref —
            # regional carriers (4-digit refs) pass through the bbox too.
            if not (rs_operated(t) or RS_REF.match(ref)) or not ref:
                continue
            # A line can have several route VARIANTS (depot runs, short
            # workings like "Jugla => Stacijas laukums"). Collect per
            # termini-pair; the dominant pair is chosen later.
            frm = (t.get("from") or "").strip()
            to = (t.get("to") or "").strip()
            if (not frm or not to) and ":" in t.get("name", ""):
                parts = [p.strip() for p in t["name"].split(":", 1)[1].split("=>")]
                if len(parts) == 2:
                    frm = frm or parts[0]
                    to = to or parts[1]
            pair = (frozenset([frm.lower(), to.lower()])
                    if (frm or to) else frozenset([str(el["id"])]))
            g = tgroups.setdefault((mode, ref), {
                "name": f"{MODE_LABEL[mode]} {ref}", "ref": ref, "variants": {}})
            v = g["variants"].setdefault(pair, {"ways": set(), "frm": frm, "to": to})
            v["ways"].update(wids)

    def ref_sortkey(ref):
        num = "".join(ch for ch in ref if ch.isdigit())
        return (ref[:1] == "N", int(num) if num else 999, ref)

    way_len = {}
    for wid, s in way_geo.items():
        way_len[wid] = sum(math.hypot(s[i + 1][0] - s[i][0], s[i + 1][1] - s[i][1])
                           for i in range(len(s) - 1))

    transit = {"tram": [], "trolleybus": [], "busDay": [], "busNight": [], "rail": []}
    trimmed = 0
    for (kind, _), g in tgroups.items():
        if "variants" in g:
            # Dominant termini pair wins: longest route, depot runs last
            def vscore(v):
                blob = (v["frm"] + " " + v["to"]).lower()
                return ("depo" not in blob,
                        sum(way_len[w] for w in v["ways"]))
            best = max(g["variants"].values(), key=vscore)
            if len(g["variants"]) > 1:
                trimmed += 1
            ways = best["ways"]
            hint = (best["frm"] + " – " + best["to"]).strip(" –")
        else:
            ways = g["ways"]
            hint = g["hint"]
        segs = [way_geo[w] for w in sorted(ways)]
        allpts = [p for s in segs for p in s]
        feat = {"name": g["name"], "segs": segs, "rings": [],
                "bbox": bbox_of(allpts), "hint": hint}
        if kind == "rail":
            transit["rail"].append(feat)
        elif kind == "tram":
            transit["tram"].append(feat)
        elif kind == "trolleybus":
            transit["trolleybus"].append(feat)
        else:
            key = "busNight" if g["ref"].startswith("N") else "busDay"
            transit[key].append(feat)
    for tname, arr in transit.items():
        if tname == "rail":
            arr.sort(key=lambda f: f["name"])
        else:
            arr.sort(key=lambda f: ref_sortkey(f["name"].split()[-1]))
        for i, f in enumerate(arr):
            f["id"] = i
        print(f"transit {tname}: {len(arr)}")
    print(f"transit: kept only the regular route on {trimmed} lines with variants")

    # --- emit ---
    all_bb = bbox_of([p for s in streets for seg in s["segs"] for p in seg])
    out = {
        "meta": {
            "version": 2,
            "bounds": [0, 0, all_bb[2], all_bb[3]],
            "proj": {"lon_min": lon_min, "lat_max": lat_max, "lat0": LAT0},
        },
        "hoods": [{"id": h["id"], "name": h["name"], "rings": h["rings"], "bbox": h["bbox"]}
                  for h in hoods],
        "streets": [{"id": s["id"], "name": s["name"], "cls": s["cls"], "hoods": s["hoods"],
                     "dom": s["dom"], "segs": s["segs"], "bbox": s["bbox"]}
                    for s in streets],
        "ctx": context,
        "bridges": bridges,
        "parks": parks,
        "transit": transit,
        "water": water,
    }
    js = "window.RIGA_DATA=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUT_PATH.write_text(js, encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(js.encode()) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
