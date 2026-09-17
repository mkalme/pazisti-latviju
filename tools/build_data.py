#!/usr/bin/env python3
"""Build data/riga_data.js (window.RIGA_DATA = {...}) from data/raw/*.json.

Pipeline: project lon/lat to planar meters, stitch neighborhood/water rings,
simplify geometry, merge street ways into named entities, assign each entity
to the neighborhoods it passes through.
"""
import bisect
import heapq
import json
import math
import re
import sys
from pathlib import Path

from geo import (simplify, round_pts, bbox_of, bbox_union,
                 ring_area, point_in_ring, stitch_rings)

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
OUT_PATH = BASE / "data" / "riga_data.js"

BASE_CLASSES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "pedestrian",
]
# Five-tier road hierarchy. Tier 0 is the A-ROAD network (state main roads
# A1..A15 by their `ref`, the itineraries that thread through the city:
# A2/A6/A7/A8/A10) — a handful of true highways. Unrefed motorway/trunk
# stretches land with the primaries. Renderers key width, color and zoom
# fading off this, so the map reads like a real one — a few strong
# arteries over a fading carpet.
STREET_CLS = {
    "motorway": 1, "trunk": 1,
    "primary": 1,
    "secondary": 2,
    "tertiary": 3, "unclassified": 3,
    "residential": 4, "living_street": 4, "pedestrian": 4,
    "motorway_link": 3, "trunk_link": 3, "primary_link": 3,
    "secondary_link": 3, "tertiary_link": 3,
}
A_REF = re.compile(r"^A([1-9]|1[0-5])$")
# Tier-0 gate: a street is an A ROAD only when it carries enough HIGH-SPEED
# A-refed way. OSM refs the signed itinerary straight through the center
# (Merķeļa iela is "A2" for 600 m at 50 km/h) — those stay ordinary
# streets; the real corridors all have km of 70-90 km/h refed length.
A_FAST_KMH = 65
A_FAST_MIN = 900.0   # meters of fast refed way that make a street an A road


def road_cls(tags):
    """Grade tier for a way (tier 0 is assigned to CORRIDOR GEOMETRY at
    emit time, by the peeled A-refed mask — never per way here, and never
    via connectors)."""
    return STREET_CLS.get(tags.get("highway"))
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
SAMPLE_STEP = 50.0    # hood-assignment sampling interval along streets
MIN_WATER_AREA = 10000.0  # m^2

# --- street healing (gap-anchored chains between a street's components) ---
HEAL_ANCHOR_R = 100.0   # chains may launch this close to the gap's near vertex
HEAL_ARRIVE_R = 150.0   # ...and must arrive this close to the far vertex
HEAL_GAP_MAX = 3000.0   # == CLUSTER_JOIN: every pair close enough to be one
                        # entity gets a healing attempt; farther same-named
                        # components are separate streets by definition
HEAL_DETOUR_K = 1.4     # chain length budget: K * direct + C
HEAL_DETOUR_C = 60.0
HEAL_BEARING_TOL = 60.0      # deg; the chain must roughly continue the street
HEAL_BEARING_MIN_GAP = 30.0  # ...unless the gap is too tiny to have a bearing
HEAL_ROUNDS = 3         # re-heal splits caused by earlier transfers
HEAL_STARTS = 8         # launch vertices tried per gap side
SLIVER_MAX = 90.0       # junction slivers this short may transfer from ANY street
BRIDGE_MAX = 90.0       # leftover gaps this short get a straight synthetic
                        # connector — junction holes with nothing to absorb
NAMED_CHAIN_MAX = 400.0  # meters of OTHER streets' pavement one chain may take —
                         # doglegs and short decks, never a parallel avenue
PATH_GAP_MAX = 120.0    # unnamed foot/cycle paths only bridge gaps this small
MAX_CHAIN_WAYS = 8      # base; scales with the gap (long gaps = many ways)

# --- entity formation ---
CLUSTER_JOIN = 3000.0   # max gap at which same-name components MAY merge...
MERGE_K = 1.15          # ...but only via a nearly straight road corridor:
MERGE_C = 150.0         # route <= MERGE_K * direct + MERGE_C, ownership-blind
                        # (the slack covers interchange crossings — Jūrmalas
                        # gatve detours over the K. Ulmaņa junction — while
                        # Kleistu-scale detours still fail)
FRAG_MAX_LEN = 150.0    # leftover non-main clusters shorter than this become
                        # context decor, not quiz targets
CITY_MIN_SHARE = 0.4    # entities with less of their length strictly inside
                        # the city are the neighbour's roads: context decor

# --- dual-carriageway collapse (anti-parallel oneway strands -> midline) ---
DC_SAMPLE = 10.0     # sampling step along strands, meters
DC_MIN = 4.0         # partner lateral distance window, meters (carriageways
DC_MAX = 55.0        # pinch together at tapers and spread at wide medians)
DC_HEAD_TOL = 20.0   # deg from exactly anti-parallel
DC_MIN_RUN = 120.0   # shorter strands neither collapse nor serve as partners
WELD_R = 15.0        # entity endpoints this close to their street's other
                     # geometry weld onto it (unshared OSM junction nodes,
                     # collapse cut offsets); dangling ends near a collapse
                     # centerline weld from as far as DC_MAX
DC_SELF_MIN = 150.0  # same-strand matches only beyond this param distance
DC_COAST = 2         # samples a run may coast with its partner obscured


def load(name):
    return json.loads((RAW_DIR / f"{name}.json").read_text())


def endkey(pt):
    return (round(pt[0]), round(pt[1]))


def plen(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def parse_way_flags(tags):
    """(oneway, roundabout) flags used by healing and carriageway collapse."""
    ow = tags.get("oneway") in ("yes", "1", "true", "-1")
    rab = tags.get("junction") in ("roundabout", "circular")
    return ow, rab


def subtract_intervals(pts, ivals):
    """Remove index intervals [(lo, hi), ...] from a polyline. Returns the
    kept pieces; each shares its boundary vertex with the removed stretch."""
    ivs = sorted(ivals)
    merged = [list(ivs[0])]
    for lo, hi in ivs[1:]:
        if lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    pieces = []
    pos = 0
    for lo, hi in merged:
        if lo > pos:
            pieces.append(pts[pos:lo + 1])
        pos = max(pos, hi)
    if pos < len(pts) - 1:
        pieces.append(pts[pos:])
    return [p for p in pieces if len(p) >= 2]


def vertex_components(group):
    """Connected components of a street's ways via exact shared vertices.
    Returns (comp id per way, {comp: [way idx]}, {vertex key: (pt, comp)})."""
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
    comp_of = [find(i) for i in range(len(group))]
    comps = {}
    for wi, c in enumerate(comp_of):
        comps.setdefault(c, []).append(wi)
    ends = {}
    for wi, w in enumerate(group):
        c = comp_of[wi]
        for p in w["pts"]:
            ends.setdefault(endkey(p), (p, c))
    return comp_of, comps, ends


def closest_vertices(group, wis_a, wis_b):
    """Closest vertex pair between two way sets: (pt_a, pt_b, distance)."""
    best = (None, None, float("inf"))
    for wa in wis_a:
        ba = group[wa]["bbox"]
        for wb in wis_b:
            bb = group[wb]["bbox"]
            dx = max(0.0, max(ba[0], bb[0]) - min(ba[2], bb[2]))
            dy = max(0.0, max(ba[1], bb[1]) - min(ba[3], bb[3]))
            if math.hypot(dx, dy) >= best[2]:
                continue
            for p in group[wa]["pts"]:
                for q in group[wb]["pts"]:
                    d = math.hypot(p[0] - q[0], p[1] - q[1])
                    if d < best[2]:
                        best = (p, q, d)
    return best


# ---------- dual-carriageway collapse ----------

def polyline_cum(pts):
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0],
                                        pts[i + 1][1] - pts[i][1]))
    return cum


def point_at(pts, cum, t):
    if t <= 0.0:
        return [pts[0][0], pts[0][1]]
    if t >= cum[-1]:
        return [pts[-1][0], pts[-1][1]]
    i = min(bisect.bisect_right(cum, t) - 1, len(pts) - 2)
    span = cum[i + 1] - cum[i]
    f = 0.0 if span == 0.0 else (t - cum[i]) / span
    return [pts[i][0] + f * (pts[i + 1][0] - pts[i][0]),
            pts[i][1] + f * (pts[i + 1][1] - pts[i][1])]


def extract_piece(pts, cum, a, b):
    piece = [point_at(pts, cum, a)]
    for i in range(len(pts)):
        if a < cum[i] < b:
            piece.append([pts[i][0], pts[i][1]])
    end = point_at(pts, cum, b)
    if math.hypot(end[0] - piece[-1][0], end[1] - piece[-1][1]) > 1e-9:
        piece.append(end)
    return piece


def build_strands(ways):
    """Chain oneway ways end-to-start (travel direction preserved) into
    maximal polyline strands. Junction nodes where same-street ramps merge
    or diverge do NOT break the chain — the straightest continuation wins,
    so a carriageway stays one strand across its slip roads."""
    starts, stops = {}, {}
    for i, w in enumerate(ways):
        starts.setdefault(endkey(w["pts"][0]), []).append(i)
        stops.setdefault(endkey(w["pts"][-1]), []).append(i)

    def head_dir(i):
        p = ways[i]["pts"]
        for k in range(1, len(p)):
            dx, dy = p[k][0] - p[0][0], p[k][1] - p[0][1]
            ln = math.hypot(dx, dy)
            if ln > 0.0:
                return dx / ln, dy / ln
        return 0.0, 0.0

    def tail_dir(i):
        p = ways[i]["pts"]
        for k in range(len(p) - 2, -1, -1):
            dx, dy = p[-1][0] - p[k][0], p[-1][1] - p[k][1]
            ln = math.hypot(dx, dy)
            if ln > 0.0:
                return dx / ln, dy / ln
        return 0.0, 0.0

    cos_cont = math.cos(math.radians(60.0))
    used = [False] * len(ways)

    def straightest(cands, dx, dy, dir_of):
        best = None
        for j in cands:
            if used[j]:
                continue
            jx, jy = dir_of(j)
            c = dx * jx + dy * jy
            if c < cos_cont:
                continue
            if best is None or c > best[0] + 1e-12 or \
                    (abs(c - best[0]) <= 1e-12 and j < best[1]):
                best = (c, j)
        return None if best is None else best[1]

    chains = []
    for i in range(len(ways)):
        if used[i]:
            continue
        used[i] = True
        chain = [i]
        while True:
            tx, ty = head_dir(chain[0])
            j = straightest(stops.get(endkey(ways[chain[0]]["pts"][0]), ()),
                           tx, ty, tail_dir)
            if j is None:
                break
            used[j] = True
            chain.insert(0, j)
        while True:
            tx, ty = tail_dir(chain[-1])
            j = straightest(starts.get(endkey(ways[chain[-1]]["pts"][-1]), ()),
                           tx, ty, head_dir)
            if j is None:
                break
            used[j] = True
            chain.append(j)
        chains.append(chain)
    out = []
    for chain in chains:
        pts = [list(p) for p in ways[chain[0]]["pts"]]
        for j in chain[1:]:
            nx = ways[j]["pts"]
            skip = 1 if endkey(pts[-1]) == endkey(nx[0]) else 0
            pts.extend([list(p) for p in nx[skip:]])
        out.append(pts)
    return out


def collapse_entity(ways):
    """Replace anti-parallel oneway carriageway pairs DC_MIN..DC_MAX apart
    with their midline. Returns (float polylines, meters of line removed)."""
    elig = [w for w in ways if w.get("ow") and not w.get("rab")]
    out = [[list(p) for p in w["pts"]] for w in ways
           if not (w.get("ow") and not w.get("rab"))]
    if not elig:
        return out, 0.0, 0
    strands = build_strands(elig)
    cums = [polyline_cum(s) for s in strands]
    CELL = 64.0
    grid = {}
    for si, pts in enumerate(strands):
        for j in range(len(pts) - 1):
            (x0, y0), (x1, y1) = pts[j], pts[j + 1]
            for cx in range(int(min(x0, x1) // CELL), int(max(x0, x1) // CELL) + 1):
                for cy in range(int(min(y0, y1) // CELL), int(max(y0, y1) // CELL) + 1):
                    grid.setdefault((cx, cy), []).append((si, j))
    consumed = [[] for _ in strands]
    centers = []
    removed = 0.0
    cos_head = math.cos(math.radians(DC_HEAD_TOL))

    def overlaps(ivs, lo, hi):
        return any(lo < b and a < hi for a, b in ivs)

    def inside(ivs, t):
        return any(a <= t <= b for a, b in ivs)

    elig_strand = [cums[i][-1] >= DC_MIN_RUN for i in range(len(strands))]

    def matches(si, t, px, py, hx, hy):
        """Best (distance, foot param) per partner strand around a sample."""
        found = {}
        for cx in range(int((px - DC_MAX) // CELL), int((px + DC_MAX) // CELL) + 1):
            for cy in range(int((py - DC_MAX) // CELL), int((py + DC_MAX) // CELL) + 1):
                for sj, j in grid.get((cx, cy), ()):
                    if not elig_strand[sj]:
                        continue  # median rungs and slip stubs never partner
                    a, b = strands[sj][j], strands[sj][j + 1]
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    ln = math.hypot(dx, dy)
                    if ln == 0.0 or (hx * dx + hy * dy) / ln > -cos_head:
                        continue  # not anti-parallel
                    tt = ((px - a[0]) * dx + (py - a[1]) * dy) / (ln * ln)
                    tt = max(0.0, min(1.0, tt))
                    d = math.hypot(px - (a[0] + tt * dx), py - (a[1] + tt * dy))
                    if not (DC_MIN <= d <= DC_MAX):
                        continue
                    fp = cums[sj][j] + tt * ln
                    if sj == si and abs(fp - t) < DC_SELF_MIN:
                        continue
                    if inside(consumed[sj], fp):
                        continue
                    cur = found.get(sj)
                    if cur is None or d < cur[0]:
                        found[sj] = (d, fp)
        return found

    def close(si, run):
        nonlocal removed
        if run is None or len(run["items"]) < 2:
            return
        op0, fp0 = run["items"][0]
        op1, fp1 = run["items"][-1]
        own = op1 - op0
        if own < DC_MIN_RUN or abs(fp1 - fp0) < 0.6 * own:
            return
        j = run["j"]
        flo, fhi = (fp0, fp1) if fp0 <= fp1 else (fp1, fp0)
        if overlaps(consumed[si], op0, op1) or overlaps(consumed[j], flo, fhi):
            return
        consumed[si].append((op0, op1))
        consumed[j].append((flo, fhi))
        mid = []
        for op, fp in run["items"]:
            pa = point_at(strands[si], cums[si], op)
            pb = point_at(strands[j], cums[j], fp)
            mid.append([(pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0])
        centers.append(mid)
        removed += own

    for si, pts in enumerate(strands):
        cum = cums[si]
        if not elig_strand[si]:
            continue
        run = None
        t = 0.0
        while t <= cum[-1]:
            got = {}
            if not inside(consumed[si], t):
                i = min(bisect.bisect_right(cum, t) - 1, len(pts) - 2)
                dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
                ln = math.hypot(dx, dy)
                if ln > 0.0:
                    span = cum[i + 1] - cum[i]
                    f = 0.0 if span == 0.0 else (t - cum[i]) / span
                    got = matches(si, t, pts[i][0] + f * dx, pts[i][1] + f * dy,
                                  dx / ln, dy / ln)
            # A live run sticks to its partner even when some other strand is
            # momentarily nearer, and coasts a couple of samples through
            # junction breaks, so one crossing does not split the collapse.
            ext = False
            if run is not None and run["j"] in got:
                fp = got[run["j"]][1]
                step = fp - run["items"][-1][1]
                allow = 3.0 * DC_SAMPLE * (run["miss"] + 1)
                if 0.0 < abs(step) <= allow and \
                        (run["sign"] == 0 or step * run["sign"] > 0.0):
                    run["sign"] = 1 if step > 0 else -1
                    run["items"].append((t, fp))
                    run["miss"] = 0
                    ext = True
            if not ext:
                if run is not None and run["miss"] < DC_COAST and \
                        run["j"] not in got:
                    run["miss"] += 1
                else:
                    close(si, run)
                    run = None
                    if got:
                        j = min(got, key=lambda k: (got[k][0], k))
                        run = {"j": j, "sign": 0, "items": [(t, got[j][1])],
                               "miss": 0}
            t += DC_SAMPLE
        close(si, run)

    # Second pass: absorb leftover parallel lanes into the centerline. A
    # frontage/local lane running along a collapsed corridor (either
    # direction — the centerline has no travel direction) is redundant next
    # to it; its matched stretch is removed, divergent tails survive.
    if centers:
        ccums = [polyline_cum(c) for c in centers]
        cgrid2 = {}
        for ci, c in enumerate(centers):
            for j in range(len(c) - 1):
                (x0, y0), (x1, y1) = c[j], c[j + 1]
                for cx in range(int(min(x0, x1) // CELL), int(max(x0, x1) // CELL) + 1):
                    for cy in range(int(min(y0, y1) // CELL), int(max(y0, y1) // CELL) + 1):
                        cgrid2.setdefault((cx, cy), []).append((ci, j))

        def near_center(px, py, hx, hy):
            for cx in range(int((px - DC_MAX) // CELL), int((px + DC_MAX) // CELL) + 1):
                for cy in range(int((py - DC_MAX) // CELL), int((py + DC_MAX) // CELL) + 1):
                    for ci, j in cgrid2.get((cx, cy), ()):
                        a, b = centers[ci][j], centers[ci][j + 1]
                        dx, dy = b[0] - a[0], b[1] - a[1]
                        ln = math.hypot(dx, dy)
                        if ln == 0.0 or abs(hx * dx + hy * dy) / ln < cos_head:
                            continue
                        tt = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / (ln * ln)))
                        d = math.hypot(px - (a[0] + tt * dx), py - (a[1] + tt * dy))
                        if DC_MIN <= d <= DC_MAX:
                            return True
            return False

        for si, pts in enumerate(strands):
            cum = cums[si]
            if not elig_strand[si]:
                continue
            start = None
            last = None
            miss = 0
            t = 0.0
            while t <= cum[-1] + DC_SAMPLE:
                hit = False
                if t <= cum[-1] and not inside(consumed[si], t):
                    i = min(bisect.bisect_right(cum, t) - 1, len(pts) - 2)
                    dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
                    ln = math.hypot(dx, dy)
                    if ln > 0.0:
                        span = cum[i + 1] - cum[i]
                        f = 0.0 if span == 0.0 else (t - cum[i]) / span
                        hit = near_center(pts[i][0] + f * dx, pts[i][1] + f * dy,
                                          dx / ln, dy / ln)
                if hit:
                    if start is None:
                        start = t
                    last = t
                    miss = 0
                elif start is not None:
                    if miss < DC_COAST:
                        miss += 1
                    else:
                        if last - start >= DC_MIN_RUN:
                            consumed[si].append((start, last))
                            removed += last - start
                        start = None
                        last = None
                        miss = 0
                t += DC_SAMPLE
            if start is not None and last is not None and last - start >= DC_MIN_RUN:
                consumed[si].append((start, last))
                removed += last - start

    for si, pts in enumerate(strands):
        if not consumed[si]:
            out.append(pts)
            continue
        ivs = sorted(consumed[si])
        merged = [list(ivs[0])]
        for lo, hi in ivs[1:]:
            if lo <= merged[-1][1] + 1e-9:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        pos = 0.0
        spans = []
        for lo, hi in merged:
            if lo - pos > 1.0:
                spans.append((pos, lo))
            pos = max(pos, hi)
        if cums[si][-1] - pos > 1.0:
            spans.append((pos, cums[si][-1]))
        for a, b in spans:
            piece = extract_piece(pts, cums[si], a, b)
            if len(piece) >= 2:
                out.append(piece)
    # Crumbs — short cut leftovers lying wholly inside a collapsed corridor —
    # are redundant next to the centerline and would dangle as separate
    # components; drop them before stitching.
    if centers:
        cgrid = {}
        for ci, c in enumerate(centers):
            for j in range(len(c) - 1):
                (x0, y0), (x1, y1) = c[j], c[j + 1]
                for cx in range(int((min(x0, x1) - DC_MAX) // CELL),
                               int((max(x0, x1) + DC_MAX) // CELL) + 1):
                    for cy in range(int((min(y0, y1) - DC_MAX) // CELL),
                                   int((max(y0, y1) + DC_MAX) // CELL) + 1):
                        cgrid.setdefault((cx, cy), []).append((c[j], c[j + 1]))

        def in_corridor(p):
            for a, b in cgrid.get((int(p[0] // CELL), int(p[1] // CELL)), ()):
                dx, dy = b[0] - a[0], b[1] - a[1]
                l2 = dx * dx + dy * dy
                if l2 == 0.0:
                    continue
                tt = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
                ex, ey = p[0] - (a[0] + tt * dx), p[1] - (a[1] + tt * dy)
                if ex * ex + ey * ey <= DC_MAX * DC_MAX:
                    return True
            return False

        out = [poly for poly in out
               if plen(poly) >= DC_MIN_RUN or not all(in_corridor(p) for p in poly)]
    out.extend(centers)
    return out, removed, len(centers)


def entity_bridge(segs):
    """Final invariant: an entity never ships with an internal gap of up to
    BRIDGE_MAX between its parts — whatever pass caused it (a crumb drop
    that severed the spine, an absorb orphan beyond weld reach). Touching
    is judged the way welds leave geometry: shared-ish vertices OR an
    endpoint on another piece's segment. Returns added connector count."""
    n = len(segs)
    if n < 2:
        return 0
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    boxes = [bbox_of(s) for s in segs]

    def touching(i, j):
        bi, bj = boxes[i], boxes[j]
        if not (bi[0] - 2 <= bj[2] and bj[0] - 2 <= bi[2]
                and bi[1] - 2 <= bj[3] and bj[1] - 2 <= bi[3]):
            return False
        for p in segs[i]:
            for q in segs[j]:
                if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= 4.0:
                    return True
        for a, b in ((i, j), (j, i)):
            for end in (0, -1):
                p = segs[a][end]
                pts = segs[b]
                for k in range(len(pts) - 1):
                    u, v = pts[k], pts[k + 1]
                    dx, dy = v[0] - u[0], v[1] - u[1]
                    l2 = dx * dx + dy * dy
                    if l2 == 0.0:
                        continue
                    t = max(0.0, min(1.0, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / l2))
                    ex = p[0] - (u[0] + t * dx)
                    ey = p[1] - (u[1] + t * dy)
                    if ex * ex + ey * ey <= 4.0:
                        return True
        return False

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) != find(j) and touching(i, j):
                parent[find(i)] = find(j)
    comps = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    if len(comps) < 2:
        return 0
    cids = sorted(comps)
    pairs = []
    for a in range(len(cids)):
        for b in range(a + 1, len(cids)):
            best = (1e18, None, None)
            for i in comps[cids[a]]:
                for j in comps[cids[b]]:
                    for p in segs[i]:
                        for q in segs[j]:
                            d2 = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
                            if d2 < best[0]:
                                best = (d2, p, q)
            gap = math.sqrt(best[0])
            if gap <= BRIDGE_MAX:
                pairs.append((gap, cids[a], cids[b], best[1], best[2]))
    if not pairs:
        return 0
    pairs.sort(key=lambda t: t[0])
    added = 0
    roots = {c: c for c in cids}

    def cfind(a):
        while roots[a] != a:
            roots[a] = roots[roots[a]]
            a = roots[a]
        return a

    for gap, ca, cb, pa, pb in pairs:
        if cfind(ca) == cfind(cb):
            continue
        roots[cfind(ca)] = cfind(cb)
        segs.append([[round(pa[0]), round(pa[1])], [round(pb[0]), round(pb[1])]])
        added += 1
    return added


def seg_touch_comps(segs):
    """Touch-model components over final entity polylines: shared-ish
    vertices or an endpoint on another piece's segment."""
    n = len(segs)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    boxes = [bbox_of(s) for s in segs]
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            bi, bj = boxes[i], boxes[j]
            if not (bi[0] - 2 <= bj[2] and bj[0] - 2 <= bi[2]
                    and bi[1] - 2 <= bj[3] and bj[1] - 2 <= bi[3]):
                continue
            hit = False
            for p in segs[i]:
                for q in segs[j]:
                    if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= 4.0:
                        hit = True
                        break
                if hit:
                    break
            if not hit:
                for a, b in ((i, j), (j, i)):
                    for end in (0, -1):
                        p = segs[a][end]
                        pts = segs[b]
                        for k in range(len(pts) - 1):
                            u, v = pts[k], pts[k + 1]
                            dx, dy = v[0] - u[0], v[1] - u[1]
                            l2 = dx * dx + dy * dy
                            if l2 == 0.0:
                                continue
                            t = max(0.0, min(1.0, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / l2))
                            ex = p[0] - (u[0] + t * dx)
                            ey = p[1] - (u[1] + t * dy)
                            if ex * ex + ey * ey <= 4.0:
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        break
            if hit:
                parent[find(i)] = find(j)
    comps = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return comps


def weld_entity(polys, ncenter):
    """Close hairline cracks inside one street: an endpoint lying near other
    geometry of the SAME entity is meant to touch it — OSM junctions where
    two ways don't share a node, collapse cuts offset from the centerline,
    orphaned lane tails. Endpoints move onto the nearest point of a
    neighboring polyline: within WELD_R generally, within DC_MAX when the
    neighbor is a collapse centerline (the last `ncenter` polylines) and
    nothing nearer exists — a dangling lane reaches its centerline."""
    if len(polys) < 2:
        return
    CELL = 64.0
    first_center = len(polys) - ncenter
    for _sweep in range(2):  # a move can shift a segment someone welded to
        grid = {}
        for pi, pts in enumerate(polys):
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                for cx in range(int(min(a[0], b[0]) // CELL), int(max(a[0], b[0]) // CELL) + 1):
                    for cy in range(int(min(a[1], b[1]) // CELL), int(max(a[1], b[1]) // CELL) + 1):
                        grid.setdefault((cx, cy), []).append((pi, i))
        moved = 0
        for pi, pts in enumerate(polys):
            for end in (0, -1):
                p = pts[end]
                best = None  # (dist, qx, qy, target is a centerline)
                for cx in range(int((p[0] - DC_MAX) // CELL), int((p[0] + DC_MAX) // CELL) + 1):
                    for cy in range(int((p[1] - DC_MAX) // CELL), int((p[1] + DC_MAX) // CELL) + 1):
                        for pj, j in grid.get((cx, cy), ()):
                            if pj == pi:
                                continue
                            a, b = polys[pj][j], polys[pj][j + 1]
                            dx, dy = b[0] - a[0], b[1] - a[1]
                            l2 = dx * dx + dy * dy
                            if l2 == 0.0:
                                continue
                            tt = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
                            qx, qy = a[0] + tt * dx, a[1] + tt * dy
                            d = math.hypot(p[0] - qx, p[1] - qy)
                            if best is None or d < best[0]:
                                best = (d, qx, qy, pj >= first_center)
                if best is None or best[0] <= 1.5:
                    continue  # nothing near, or already touching
                if best[0] <= WELD_R or (best[3] and best[0] <= DC_MAX):
                    pts[end] = [best[1], best[2]]
                    moved += 1
        if not moved:
            break


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
    a_fast_len = {}   # name -> meters of fast A-refed way (tier-0 gate)
    aref_ways = {}    # name -> [(pts, is_fast)] — the raw A-refed geometry
    for data in street_raw.values():
        for el in data["elements"]:
            if el["type"] != "way" or not el.get("geometry"):
                continue
            tags = el.get("tags", {})
            cls = road_cls(tags)
            if cls is None:
                continue
            pts = project(el["geometry"])
            if len(pts) < 2:
                continue
            ow, rab = parse_way_flags(tags)
            name = tags.get("name", "").strip()
            if name:
                ways.append({"name": name, "cls": cls, "pts": pts,
                             "bbox": bbox_of(pts), "ow": ow, "rab": rab})
                routes = [p.strip() for p in tags.get("ref", "").split(";")
                          if A_REF.match(p.strip())]
                if routes:
                    ms = re.sub(r"\D", "", tags.get("maxspeed", "") or "")
                    fast = bool(ms) and int(ms) >= A_FAST_KMH
                    aref_ways.setdefault(name, []).append((pts, fast, routes))
                    if fast:
                        a_fast_len[name] = a_fast_len.get(name, 0.0) + plen(pts)
            else:
                unnamed.append({"cls": cls, "pts": pts, "bbox": bbox_of(pts),
                                "ow": ow, "rab": rab})

    # Unnamed service/track ways join the absorption pool only: they may
    # bridge two same-named street pieces, but are never drawn on their own
    # (unnamed driveways and yard lanes would swamp the map). Unnamed foot,
    # cycle and path ways too (streets_pool_paths, optional; walk-flagged so
    # they only ever bridge small gaps): a plaza walkway is sometimes the
    # only thing joining two halves of a street.
    pool_files = ["streets_pool_minor"]
    if (RAW_DIR / "streets_pool_paths.json").exists():
        pool_files.append("streets_pool_paths")
    else:
        print("note: data/raw/streets_pool_paths.json missing — rerun "
              "tools/fetch_osm.py to enable path-based gap healing")
    for fname in pool_files:
        for el in load(fname)["elements"]:
            if el["type"] != "way" or not el.get("geometry"):
                continue
            pts = project(el["geometry"])
            if len(pts) < 2:
                continue
            ow, rab = parse_way_flags(el.get("tags", {}))
            unnamed.append({"cls": 4, "pts": pts, "bbox": bbox_of(pts),
                            "ow": ow, "rab": rab, "pool_only": True,
                            "walk": fname == "streets_pool_paths"})

    # All NAMED ways also join the connector pool — a bridge deck often
    # carries the through-street's name while its ends are named after the
    # bridge (Vanšu tilts vs Krišjāņa Valdemāra iela). When a chain through
    # a named way heals a SMALLER street, that stretch is TRANSFERRED to it
    # (removed from its own street): every piece of road belongs to exactly
    # one street, so two streets can never light up together.
    def pool_add(name, w):
        unnamed.append({"cls": w["cls"], "pts": w["pts"], "bbox": w["bbox"],
                        "ow": w.get("ow"), "rab": w.get("rab"),
                        "pool_only": True, "own_name": name, "src": w})

    for w in ways:
        pool_add(w["name"], w)

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
        w = {"name": name, "cls": 4, "pts": pts, "bbox": bbox_of(pts),
             "patch": True}
        w["ow"], w["rab"] = parse_way_flags(el.get("tags", {}))
        by_name[name].append(w)
        pool_add(name, w)
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
    PATCH_CLS = {"service": 4, "track": 4, "footway": 4, "cycleway": 4,
                 "path": 4, "steps": 4}
    boundary_added = 0
    for el in load("streets_boundary")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        tags = el.get("tags", {})
        hw = tags.get("highway")
        cls = road_cls(tags)
        if cls is None:
            cls = PATCH_CLS.get(hw)
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
        w["ow"], w["rab"] = parse_way_flags(tags)
        by_name[name].append(w)
        pool_add(name, w)
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
                                   "bbox": w["bbox"], "ow": w.get("ow"),
                                   "rab": w.get("rab")})
        print(f'folded "{comp_name}" into: {", ".join(matched)}')

    # Heal disconnected streets: when two CONNECTED COMPONENTS of a street
    # are joined by a nearly direct chain of other road segments — any class,
    # named or unnamed — that chain becomes street geometry. The search is
    # GAP-ANCHORED: chains may only launch near the two components' closest
    # vertices, must arrive near the far side, and are pruned to the ellipse
    # the detour budget allows around the gap — so they can follow a corridor
    # for kilometers (Kleistu iela continues along unnamed forest road) yet
    # never wander around blocks and swallow parallel streets. Every pair
    # close enough to cluster into one entity (HEAL_GAP_MAX == CLUSTER_JOIN)
    # gets an attempt; ONE best chain per pair, applied smallest-gap-first
    # with redundant pairs skipped. Healing runs in ROUNDS: a transfer can
    # split its donor at a junction, and the next round heals the donor
    # through whatever pool geometry is still unclaimed.
    #  - ownership: a street may only take a NAMED stretch from a street with
    #    more total length (the bridge wins the deck; the avenue never takes
    #    the bridge), junction slivers <= SLIVER_MAX excepted, and no chain
    #    may take more than NAMED_CHAIN_MAX of other streets' pavement — a
    #    long continuation under another street's name stays that street's;
    #  - pool ways are enterable at ANY vertex (a crossing street's junction
    #    node is usually mid-way); takes are clipped index intervals, and
    #    donors keep their remainder pieces;
    #  - unnamed foot/cycle paths only ever bridge gaps <= PATH_GAP_MAX;
    #  - a chain branch terminates at the FIRST street vertex it touches.
    mirror_of = {}   # named way dict -> its pool mirror index
    for ui, u in enumerate(unnamed):
        if u.get("own_name") is not None:
            mirror_of[id(u["src"])] = ui

    orig_by_name = by_name
    claimed = {}      # pool idx -> [(lo, hi)] claimed index intervals
    healed_add = {}   # name -> connector pieces healed into it

    def effective_group(name):
        """The name's ways with every claimed interval removed (a claimed
        stretch belongs to the street that healed through it), plus the
        connector pieces healed into this street so far."""
        out = []
        for w in orig_by_name[name]:
            ui = mirror_of.get(id(w))
            ivals = claimed.get(ui) if ui is not None else None
            if ivals:
                for pc in subtract_intervals(w["pts"], ivals):
                    nw = {"name": name, "cls": w["cls"], "pts": pc,
                          "bbox": bbox_of(pc), "ow": w.get("ow"),
                          "rab": w.get("rab")}
                    if w.get("patch"):
                        nw["patch"] = True
                    out.append(nw)
            else:
                out.append(w)
        out.extend(healed_add.get(name, ()))
        return out

    # Pool vertex index, built once — the pool never changes, only claims do.
    by_vertex = {}   # vertex key -> [(pool idx, vertex idx), ...]
    vkeys = []       # pool idx -> [vertex key, ...]
    for ui, u in enumerate(unnamed):
        vk = [endkey(p) for p in u["pts"]]
        vkeys.append(vk)
        for vi, k in enumerate(vk):
            by_vertex.setdefault(k, []).append((ui, vi))

    def walk_pool(u, vk, enter, step, skey, ends):
        """Walk pool way u from vertex `enter` in direction `step` (+-1).
        Stops at the first street vertex (arrival) or the way's end.
        Returns (arrival index or None, traversed length, exit index)."""
        pts = u["pts"]
        part = 0.0
        i = enter
        while True:
            j = i + step
            if j < 0 or j >= len(pts):
                return None, part, i
            part += math.hypot(pts[j][0] - pts[i][0], pts[j][1] - pts[i][1])
            i = j
            if vk[i] != skey and vk[i] in ends:
                return i, part, i

    cos_bearing = math.cos(math.radians(HEAL_BEARING_TOL))
    healed_pairs = conflict_skips = sliver_takes = 0
    absorbed_total = transferred_total = 0
    healed_names = set()

    def uf_find(m, a):
        while m.setdefault(a, a) != a:
            m[a] = m[m[a]]
            a = m[a]
        return a

    for rnd in range(HEAL_ROUNDS):
        groups = {}
        for n in orig_by_name:
            g = effective_group(n)
            if g:
                groups[n] = g
        name_len = {n: sum(plen(w["pts"]) for w in g) for n, g in groups.items()}
        heal_jobs = []   # (name, group, comps, ends, pairs)
        for name in sorted(groups):
            group = groups[name]
            comp_of, comps, ends = vertex_components(group)
            if len(comps) < 2:
                continue
            cids = sorted(comps)
            pairs = []
            for i in range(len(cids)):
                for j in range(i + 1, len(cids)):
                    ca, cb = cids[i], cids[j]
                    pa, pb, gap = closest_vertices(group, comps[ca], comps[cb])
                    if gap > HEAL_GAP_MAX:
                        continue
                    pairs.append((ca, cb, pa, pb, gap))
            if pairs:
                heal_jobs.append((name, group, comps, ends, pairs))
        if not heal_jobs:
            break

        cands = []  # (gap, name, ca, cb, tot, hops); hops = ((ui, lo, hi), ...)
        for name, group, comps, ends, pairs in heal_jobs:
            for ca, cb, pa, pb, gap in pairs:
                chain_cap = (HEAL_DETOUR_K * (gap + HEAL_ANCHOR_R + HEAL_ARRIVE_R)
                             + HEAL_DETOUR_C)
                ways_cap = min(90, max(MAX_CHAIN_WAYS, int(gap / 40.0) + 4))
                best = None
                for scomp, tcomp, sanchor, tanchor in ((ca, cb, pa, pb), (cb, ca, pb, pa)):
                    starts = []
                    seen_keys = set()
                    for wi in comps[scomp]:
                        for p in group[wi]["pts"]:
                            d = math.hypot(p[0] - sanchor[0], p[1] - sanchor[1])
                            if d > HEAL_ANCHOR_R:
                                continue
                            k = endkey(p)
                            if k in by_vertex and k not in seen_keys:
                                seen_keys.add(k)
                                starts.append((d, k, p))
                    starts.sort(key=lambda s: s[0])
                    for _, skey, spt in starts[:HEAL_STARTS]:
                        stack = [(skey, (), 0.0, 0.0, False)]
                        seen = {skey}
                        while stack:
                            pk, hops, tot, named, haswalk = stack.pop()
                            if len(hops) >= ways_cap:
                                continue
                            for ui, vi in by_vertex.get(pk, ()):
                                if any(h[0] == ui for h in hops):
                                    continue
                                u = unnamed[ui]
                                own = u.get("own_name")
                                if own == name:
                                    continue
                                sliver_only = False
                                if own is not None:
                                    if own not in groups:
                                        continue  # orphaned (folded compound name)
                                    if name_len.get(name, 0.0) >= name_len.get(own, 0.0):
                                        sliver_only = True
                                walk2 = haswalk or bool(u.get("walk"))
                                if walk2 and gap > PATH_GAP_MAX:
                                    continue
                                vk = vkeys[ui]
                                for step in (1, -1):
                                    arrival, part, exit_i = walk_pool(u, vk, vi, step,
                                                                      skey, ends)
                                    tot2 = tot + part
                                    named2 = named + (part if own is not None else 0.0)
                                    if tot2 > chain_cap or named2 > NAMED_CHAIN_MAX:
                                        continue
                                    if sliver_only and tot2 > SLIVER_MAX:
                                        continue
                                    if arrival is not None:
                                        apt, acomp = ends[vk[arrival]]
                                        if acomp != tcomp:
                                            continue  # wrong-side contact ends the branch
                                        if math.hypot(apt[0] - tanchor[0],
                                                      apt[1] - tanchor[1]) > HEAL_ARRIVE_R:
                                            continue
                                        direct = math.hypot(apt[0] - spt[0],
                                                            apt[1] - spt[1])
                                        if tot2 > HEAL_DETOUR_K * direct + HEAL_DETOUR_C:
                                            continue
                                        if gap >= HEAL_BEARING_MIN_GAP:
                                            vx, vy = apt[0] - spt[0], apt[1] - spt[1]
                                            ax, ay = (tanchor[0] - sanchor[0],
                                                      tanchor[1] - sanchor[1])
                                            lv = math.hypot(vx, vy)
                                            la = math.hypot(ax, ay)
                                            if lv > 0 and la > 0 and \
                                                    (vx * ax + vy * ay) / (lv * la) < cos_bearing:
                                                continue
                                        chain = hops + ((ui, vi, arrival),)
                                        key = (tot2, len(chain),
                                               tuple(h[0] for h in chain))
                                        if best is None or key < best[0]:
                                            best = (key, chain)
                                    elif part > 0.0:
                                        ept = u["pts"][exit_i]
                                        # Ellipse prune: no continuation from here
                                        # can meet the budget any more.
                                        rest = math.hypot(ept[0] - tanchor[0],
                                                          ept[1] - tanchor[1]) - HEAL_ARRIVE_R
                                        if tot2 + max(0.0, rest) > chain_cap:
                                            continue
                                        ek = vk[exit_i]
                                        if ek not in seen:
                                            seen.add(ek)
                                            stack.append((ek, hops + ((ui, vi, exit_i),),
                                                          tot2, named2, walk2))
                if best is not None:
                    key, chain = best
                    hops = tuple((ui, min(a, b), max(a, b)) for ui, a, b in chain)
                    cands.append((gap, name, ca, cb, key[0], hops))

        # Apply, smallest gap first. A per-name union-find skips pairs an
        # earlier chain already connected; interval claims keep every stretch
        # single-owner even across streets competing for the same connector.
        cands.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
        heal_uf = {}   # name -> {comp: parent}
        applied = 0
        for gap, name, ca, cb, tot, hops in cands:
            m = heal_uf.setdefault(name, {})
            ra, rb = uf_find(m, ca), uf_find(m, cb)
            if ra == rb:
                continue  # already connected via a smaller gap
            conflict = False
            for ui, lo, hi in hops:
                if any(lo < c1 and c0 < hi for c0, c1 in claimed.get(ui, ())):
                    conflict = True
                    break
            if conflict:
                conflict_skips += 1
                continue
            m[ra] = rb
            applied += 1
            healed_pairs += 1
            healed_names.add(name)
            for ui, lo, hi in hops:
                claimed.setdefault(ui, []).append((lo, hi))
                u = unnamed[ui]
                pts = u["pts"][lo:hi + 1]
                if len(pts) < 2:
                    continue
                healed_add.setdefault(name, []).append(
                    {"name": name, "cls": u["cls"], "pts": pts,
                     "bbox": bbox_of(pts), "ow": u.get("ow"),
                     "rab": u.get("rab"), "healed": True})
                absorbed_total += 1
                if u.get("own_name") is not None:
                    transferred_total += 1
                    if name_len.get(name, 0.0) >= name_len.get(u["own_name"], 0.0):
                        sliver_takes += 1
        if applied == 0:
            break

    by_name = {n: effective_group(n) for n in orig_by_name}
    print(f"healed {healed_pairs} gaps in {len(healed_names)} streets "
          f"({absorbed_total} connector stretches, {transferred_total} transferred "
          f"from other streets, {sliver_takes} as junction slivers; "
          f"{conflict_skips} chains skipped on claim conflicts)")

    # --- trim to the city ---
    # The Overpass area filter admits ways that merely CROSS the boundary,
    # and the cross-boundary harvest deliberately adds more (Berģu iela
    # weaves in and out of Riga). The rule that separates the two: an
    # OUTSIDE stretch survives only when it reconnects two separate in-city
    # parts of its street. A continuation that leaves Riga and never comes
    # back (Kleistu iela toward Mārupes novads) is the neighbouring
    # municipality's street, not ours. The 58 apkaimes tile the city, so
    # their union IS the city polygon.
    def seg_d2(px, py, a, b):
        sx, sy = b[0] - a[0], b[1] - a[1]
        l2 = sx * sx + sy * sy
        if l2 == 0.0:
            return (px - a[0]) ** 2 + (py - a[1]) ** 2
        t = max(0.0, min(1.0, ((px - a[0]) * sx + (py - a[1]) * sy) / l2))
        return (px - (a[0] + t * sx)) ** 2 + (py - (a[1] + t * sy)) ** 2

    CITY_BUF = 40.0   # boundary-hugging streets count as inside
    HCELL = 512.0
    hedge = {}
    for h in hoods:
        for ring in h["rings"]:
            for i in range(len(ring) - 1):
                a, b = ring[i], ring[i + 1]
                for cx in range(int((min(a[0], b[0]) - CITY_BUF) // HCELL),
                               int((max(a[0], b[0]) + CITY_BUF) // HCELL) + 1):
                    for cy in range(int((min(a[1], b[1]) - CITY_BUF) // HCELL),
                                   int((max(a[1], b[1]) + CITY_BUF) // HCELL) + 1):
                        hedge.setdefault((cx, cy), []).append((a, b))
    buf2 = CITY_BUF * CITY_BUF

    def strict_city(x, y):
        for h in hoods:
            bb = h["bbox"]
            if bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3] and \
                    any(point_in_ring(x, y, r) for r in h["rings"]):
                return True
        return False

    def inside_city(p):
        x, y = p[0], p[1]
        if strict_city(x, y):
            return True
        return any(seg_d2(x, y, a, b) <= buf2
                   for a, b in hedge.get((int(x // HCELL), int(y // HCELL)), ()))

    trimmed_ways = 0
    trimmed_m = 0.0
    for name in sorted(by_name):
        group = by_name[name]
        if not group:
            continue
        flags = [[inside_city(p) for p in w["pts"]] for w in group]
        if all(all(f) for f in flags):
            continue  # fully in-city — the common case
        # Split each way at boundary crossings; adjacent pieces share the
        # crossing vertex so connectivity survives.
        sub = []
        for w, fl in zip(group, flags):
            pts = w["pts"]
            runs = []
            start = 0
            for k in range(1, len(pts)):
                if fl[k] != fl[start]:
                    runs.append((fl[start], start, k))
                    start = k
            runs.append((fl[start], start, len(pts) - 1))
            for flag, a, b in runs:
                piece = pts[a:b + 1]
                if len(piece) < 2:
                    continue
                nw = {"name": w["name"], "cls": w["cls"], "pts": piece,
                      "bbox": bbox_of(piece), "ow": w.get("ow"),
                      "rab": w.get("rab"), "out": not flag}
                if w.get("patch"):
                    nw["patch"] = True
                sub.append(nw)
        in_sub = [s for s in sub if not s["out"]]
        out_sub = [s for s in sub if s["out"]]
        if not in_sub:
            trimmed_ways += len(out_sub)
            trimmed_m += sum(plen(s["pts"]) for s in out_sub)
            by_name[name] = []
            continue
        _, in_comps, _ = vertex_components(in_sub)
        in_comp_at = {}
        for cid, wis in in_comps.items():
            for wi in wis:
                for p in in_sub[wi]["pts"]:
                    in_comp_at.setdefault(endkey(p), cid)
        keep_out = set()
        if out_sub:
            _, out_comps, _ = vertex_components(out_sub)
            for cid, wis in out_comps.items():
                touched = set()
                for wi in wis:
                    for p in out_sub[wi]["pts"]:
                        c = in_comp_at.get(endkey(p))
                        if c is not None:
                            touched.add(c)
                if len(touched) >= 2:
                    keep_out.update(wis)  # a weave: bridges in-city parts
        dropped = [i for i in range(len(out_sub)) if i not in keep_out]
        if dropped:
            trimmed_ways += len(dropped)
            trimmed_m += sum(plen(out_sub[i]["pts"]) for i in dropped)
        by_name[name] = in_sub + [out_sub[i] for i in sorted(keep_out)]
    if trimmed_ways:
        print(f"trimmed {trimmed_ways} out-of-city stretches "
              f"({trimmed_m / 1000.0:.1f} km) that never return to Riga")

    # Last resort for junction-scale holes: a pair of components still
    # disconnected after every chain round — the crossing is an interchange
    # with no direct way, the connecting stretch belongs to a crossing
    # street, or the pool has nothing there — gets a straight synthetic
    # connector between its closest vertices when the gap is small enough.
    # It takes nobody's pavement, so ownership stays exclusive.
    bridged = 0
    for name in sorted(by_name):
        group = by_name[name]
        if not group:
            continue
        comp_of, comps, _ends = vertex_components(group)
        if len(comps) < 2:
            continue
        cids = sorted(comps)
        pairs = []
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                pa, pb, gap = closest_vertices(group, comps[cids[i]], comps[cids[j]])
                if gap <= BRIDGE_MAX:
                    pairs.append((gap, cids[i], cids[j], pa, pb))
        if not pairs:
            continue
        pairs.sort(key=lambda t: t[0])
        m = {}
        for gap, ca, cb, pa, pb in pairs:
            ra, rb = uf_find(m, ca), uf_find(m, cb)
            if ra == rb:
                continue
            m[ra] = rb
            pts = [list(pa), list(pb)]
            group.append({"name": name, "cls": 4, "pts": pts,
                          "bbox": bbox_of(pts), "ow": False, "rab": False,
                          "healed": True})
            bridged += 1
    if bridged:
        print(f"bridged {bridged} junction-scale holes with synthetic connectors")

    context = []
    for ui, u in enumerate(unnamed):
        if u.get("pool_only"):
            continue
        ivals = claimed.get(ui)
        pieces = subtract_intervals(u["pts"], ivals) if ivals else [u["pts"]]
        for pc in pieces:
            seg = round_pts(simplify(pc, STREET_TOL))
            if len(seg) >= 2:
                context.append({"c": u["cls"], "s": seg, "b": bbox_of(seg)})
    print(f"context roads (unnamed/ramps): {len(context)} ways")

    def corridor_connected(pa, pb, gap):
        """Ownership-blind: is there a nearly straight route over road
        geometry (any pool way; foot/cycle paths excluded) between the two
        components' closest vertices? Decides whether same-named components
        are one street. Dijkstra with the straight-line rest as an
        admissible prune — the search stays inside the budget ellipse."""
        budget = MERGE_K * gap + MERGE_C
        start, target = endkey(pa), endkey(pb)
        if start not in by_vertex or target not in by_vertex:
            return False
        dist = {start: 0.0}
        h = [(0.0, start)]
        while h:
            d, k = heapq.heappop(h)
            if k == target:
                return True
            if d > dist.get(k, 1e18):
                continue
            for ui, vi in by_vertex.get(k, ()):
                u = unnamed[ui]
                # Foot/cycle paths COUNT here: this probe decides whether
                # two same-named pieces are ONE street to a human, and a
                # street cut by a railway with only a footbridge between
                # its halves (Jūrmalas gatve at Zolitūde) still is. Roads
                # for cars are healing's business, not identity's.
                pts = u["pts"]
                for nvi in (vi - 1, vi + 1):
                    if not (0 <= nvi < len(pts)):
                        continue
                    nd = d + math.hypot(pts[nvi][0] - pts[vi][0],
                                        pts[nvi][1] - pts[vi][1])
                    if nd + math.hypot(pts[nvi][0] - pb[0],
                                       pts[nvi][1] - pb[1]) > budget:
                        continue
                    nk = vkeys[ui][nvi]
                    if nd < dist.get(nk, 1e18):
                        dist[nk] = nd
                        heapq.heappush(h, (nd, nk))
        return False

    # --- A-road corridor mask (what tier 0 actually paints) ---
    # A ROUTE (A2, A6, ...) is one signed itinerary that changes street
    # names as it runs, so tail-peeling must happen PER ROUTE across all
    # names — per-name peeling retreated from every name-change junction
    # and chopped A6 at each handover. Rules: slow dangling tails peel (the
    # itinerary is signed into the center at 50 km/h, but the A road ends
    # where the highway character ends); slow ways BETWEEN fast stretches
    # survive (junction speed zones must not fragment a corridor); a tail
    # whose free end reaches the city boundary never peels (the route EXITS
    # there — A2 must run off the map, not stop short of it). The surviving
    # geometry becomes one spatial mask; a street owning enough of it is
    # split at emit into a tier-0 corridor entity and the ordinary city
    # street it becomes — byName quiz grouping keeps them one answer.
    A_MASK_TOL = 20.0
    A_MIN_PART = 500.0
    A_EXIT_R = 150.0
    A_CENTER_EXCLUDE = ("Centrs", "Vecrīga")
    # Curation, same pattern as PARK_EXCLUDE: OSM refs the PAPER itineraries
    # straight through the center (Brīvības iela is "A2" to the old town),
    # while signage diverts transit around the core (A2 leaves via the P4
    # Purvciems corridor). These central streets never join the A mask.
    A_EXCLUDE_NAMES = {
        "Brīvības iela", "Brīvības bulvāris", "Merķeļa iela",
        "Lāčplēša iela", "Satekles iela", "Marijas iela",
        "Aleksandra Čaka iela", "Krišjāņa Barona iela",
        "13. janvāra iela", "Firsa Sadovņikova iela",
    }
    # Signed-route continuations that carry NO A ref in OSM (the signage
    # diverts transit where the paper itinerary goes downtown). Geometry is
    # taken as the shortest path through the listed streets from the
    # route's refed corridor to another route's corridor, buffered to catch
    # both carriageways — so a listed street's unrelated branches (northern
    # Lielvārdes iela) stay ordinary streets.
    A_ROUTE_EXTEND = {
        "A2": ("Brīvības gatve", "Lielvārdes iela", "Gunāra Astras iela",
               "Dārzciema iela", "Slāvu aplis", "Slāvu tilts", "Slāvu iela"),
    }
    A_EXT_CONTACT = 60.0    # joins the refed corridors within this radius
    A_EXT_BUF = 60.0        # corridor buffer around the traced path
    center_hoods = [h for h in hoods if h["name"] in A_CENTER_EXCLUDE]

    def in_center(p):
        for h in center_hoods:
            bb = h["bbox"]
            if bb[0] <= p[0] <= bb[2] and bb[1] <= p[1] <= bb[3] and \
                    any(point_in_ring(p[0], p[1], r) for r in h["rings"]):
                return True
        return False

    def near_city_edge(p):
        """Near the OUTER city ring — some point within A_EXIT_R lies
        outside the city polygon. (The hood-ring grid is useless here: it
        holds every INTERIOR apkaime border too, and the Teika/Purvciems
        border runs along Brīvības gatve itself.)"""
        x, y = p[0], p[1]
        if not strict_city(x, y):
            return True
        d = A_EXIT_R * 0.7071
        for dx, dy in ((A_EXIT_R, 0), (-A_EXIT_R, 0), (0, A_EXIT_R),
                       (0, -A_EXIT_R), (d, d), (d, -d), (-d, d), (-d, -d)):
            if not strict_city(x + dx, y + dy):
                return True
        return False

    route_ways = {}    # route ref -> [(pts, fast, name)]
    route_geoms = {}   # route ref -> surviving polylines (extension contacts)
    for aname, wlist in aref_ways.items():
        for pts, fast, routes in wlist:
            for r in routes:
                route_ways.setdefault(r, []).append((pts, fast, aname))
    surv_len = {}
    a_grid = {}
    for r in sorted(route_ways):
        wlist = route_ways[r]
        n = len(wlist)
        # The A network never enters the historical core: signage routes
        # transit around it (A2 diverts at Jugla toward the P4 corridor
        # through Purvciems), while OSM's refs follow the paper register
        # straight down Brīvības iela to the center. Dropping central ways
        # first also removes the downtown one-way couplet loops that defeat
        # tail-peeling (a loop has no dangling end). Then: slow dangling
        # tails peel; slow ways BETWEEN fast stretches survive (junction
        # speed zones must not fragment a corridor); a tail whose free end
        # reaches the city boundary never peels — the route exits there.
        keep = [i for i in range(n)
                if wlist[i][2] not in A_EXCLUDE_NAMES
                and not in_center(wlist[i][0][len(wlist[i][0]) // 2])]
        # Anchors = components of FAST ways, plus the city edge. A maximal
        # SLOW cluster survives only when it touches TWO distinct anchors:
        # an interior speed zone between fast stretches does, the boundary
        # entry stub does (fast + edge) — but a slow tail toward the center
        # touches one anchor and drops, even when its dual carriageways
        # close into a U at the tip (which defeats way-level tail-peeling),
        # and so do downtown couplet loops hanging off a single fast zone.
        vert = {}
        for i in keep:
            for p in wlist[i][0]:
                vert.setdefault(endkey(p), []).append(i)
        parent = {i: i for i in keep}

        def rfind(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for k, ids in vert.items():
            fast_ids = [i for i in ids if wlist[i][1]]
            slow_ids = [i for i in ids if not wlist[i][1]]
            for group_ids in (fast_ids, slow_ids):
                for j in group_ids[1:]:
                    ra, rb = rfind(group_ids[0]), rfind(j)
                    if ra != rb:
                        parent[ra] = rb
        # The two carriageways of one fast stretch are ONE anchor — their
        # median rungs are never refed, so by-vertex union leaves them as
        # two components, and every dual road's slow tail would touch
        # "two anchors" at its transition line and never drop. Merge fast
        # components lying within carriageway distance; fast zones farther
        # apart ALONG the road stay distinct anchors.
        A_PAIR_R = 60.0
        fcomps = {}
        for i in keep:
            if wlist[i][1]:
                fcomps.setdefault(rfind(i), []).append(i)
        fgeo = {}
        for c, ids in fcomps.items():
            pts = [p for i in ids for p in wlist[i][0]]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            fgeo[c] = (pts, (min(xs), min(ys), max(xs), max(ys)))
        reps = sorted(fcomps)
        for x in range(len(reps)):
            for y in range(x + 1, len(reps)):
                ra, rb = rfind(reps[x]), rfind(reps[y])
                if ra == rb:
                    continue
                pa, ba = fgeo[reps[x]]
                pb, bb2 = fgeo[reps[y]]
                dx = max(0.0, max(ba[0], bb2[0]) - min(ba[2], bb2[2]))
                dy = max(0.0, max(ba[1], bb2[1]) - min(ba[3], bb2[3]))
                if dx * dx + dy * dy > A_PAIR_R * A_PAIR_R:
                    continue
                r2 = A_PAIR_R * A_PAIR_R
                if any((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= r2
                       for p in pa for q in pb):
                    parent[ra] = rb
        cluster_anchors = {}
        for i in keep:
            if wlist[i][1]:
                continue
            c = rfind(i)
            labs = cluster_anchors.setdefault(c, set())
            for p in wlist[i][0]:
                k = endkey(p)
                for j in vert.get(k, ()):
                    if wlist[j][1]:
                        labs.add(("F", rfind(j)))
                if near_city_edge(p):
                    labs.add(("EDGE",))
        alive = [i for i in keep
                 if wlist[i][1] or len(cluster_anchors.get(rfind(i), ())) >= 2]
        if "--debug-a" in sys.argv:
            nfast = sum(1 for i in keep if wlist[i][1])
            fanch = {rfind(i) for i in keep if wlist[i][1]}
            print(f"  [A-mask] route {r}: {len(wlist)} ways, keep {len(keep)}, "
                  f"fast {nfast} in {len(fanch)} anchors, "
                  f"slow clusters {len(cluster_anchors)} "
                  f"{sorted(len(v) for v in cluster_anchors.values())}, "
                  f"alive {len(alive)}")
        for i in alive:
            pts, _fast, aname = wlist[i]
            surv_len[aname] = surv_len.get(aname, 0.0) + plen(pts)
            route_geoms.setdefault(r, []).append(pts)
            for k in range(len(pts) - 1):
                a, b = pts[k], pts[k + 1]
                x0 = min(a[0], b[0]) - A_MASK_TOL
                x1 = max(a[0], b[0]) + A_MASK_TOL
                y0 = min(a[1], b[1]) - A_MASK_TOL
                y1 = max(a[1], b[1]) + A_MASK_TOL
                for cx in range(int(x0 // 64), int(x1 // 64) + 1):
                    for cy in range(int(y0 // 64), int(y1 // 64) + 1):
                        a_grid.setdefault((cx, cy), []).append((a, b))

    def near_polys(p, polys, tol):
        t2 = tol * tol
        for pts in polys:
            for k in range(len(pts) - 1):
                a, b = pts[k], pts[k + 1]
                if min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol and \
                        min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol and \
                        seg_d2(p[0], p[1], a, b) <= t2:
                    return True
        return False

    for r, extnames in A_ROUTE_EXTEND.items():
        own = route_geoms.get(r, [])
        others = [pts for r2, g in route_geoms.items() if r2 != r for pts in g]
        ext_ways = [w for w in ways if w["name"] in extnames]
        if not own or not others or not ext_ways:
            print(f"note: A route extension {r} skipped (missing geometry)")
            continue
        # Dijkstra from the route's corridor to any other route's corridor,
        # walking only the listed streets.
        eadj = {}
        for w in ext_ways:
            pts = w["pts"]
            for k in range(len(pts) - 1):
                a, b = endkey(pts[k]), endkey(pts[k + 1])
                L = math.hypot(pts[k + 1][0] - pts[k][0],
                               pts[k + 1][1] - pts[k][1])
                eadj.setdefault(a, []).append((b, L))
                eadj.setdefault(b, []).append((a, L))
        vpt = {}
        for w in ext_ways:
            for p in w["pts"]:
                vpt.setdefault(endkey(p), p)
        dist = {}
        prev = {}
        h = []
        for k, p in vpt.items():
            if near_polys(p, own, A_EXT_CONTACT):
                dist[k] = 0.0
                heapq.heappush(h, (0.0, k))
        if "--debug-a" in sys.argv:
            print(f"  [A-ext] {r}: {len(ext_ways)} ways, {len(vpt)} vertices, "
                  f"{len(dist)} start vertices")
        goal = None
        while h:
            dv, k = heapq.heappop(h)
            if dv > dist.get(k, 1e18):
                continue
            if near_polys(vpt[k], others, A_EXT_CONTACT):
                goal = k
                break
            for k2, L in eadj.get(k, ()):
                nd = dv + L
                if nd < dist.get(k2, 1e18):
                    dist[k2] = nd
                    prev[k2] = k
                    heapq.heappush(h, (nd, k2))
        if "--debug-a" in sys.argv:
            print(f"  [A-ext] {r}: visited {len(dist)} vertices, goal={goal is not None}")
            if goal is None:
                best = (1e18, None)
                for k in dist:
                    p = vpt[k]
                    for pts2 in others:
                        for k2 in range(len(pts2) - 1):
                            d2 = seg_d2(p[0], p[1], pts2[k2], pts2[k2 + 1])
                            if d2 < best[0]:
                                best = (d2, p)
                print(f"  [A-ext] {r}: closest approach to other routes "
                      f"{best[0] ** 0.5:.0f} m at {best[1]}")
        if goal is None:
            print(f"note: A route extension {r} found no path — "
                  f"taking the listed streets whole")
            corridor = ext_ways
        else:
            path = [vpt[goal]]
            k = goal
            while k in prev:
                k = prev[k]
                path.append(vpt[k])
            # both carriageways and the rungs live inside the buffer; a
            # listed street's unrelated branch does not
            corridor = []
            rab_hit = set()
            for w in ext_ways:
                pts = w["pts"]
                tot = hit = 0.0
                for k2 in range(len(pts) - 1):
                    L = math.hypot(pts[k2 + 1][0] - pts[k2][0],
                                   pts[k2 + 1][1] - pts[k2][1])
                    tot += L
                    mx = (pts[k2][0] + pts[k2 + 1][0]) / 2.0
                    my = (pts[k2][1] + pts[k2 + 1][1]) / 2.0
                    if near_polys((mx, my), [path], A_EXT_BUF):
                        hit += L
                if w.get("rab") and hit > 0.0:
                    rab_hit.add(w["name"])
                if tot > 0.0 and hit / tot >= 0.5:
                    corridor.append(w)
            # a touched roundabout joins as the WHOLE circle — the path
            # rides one arc, never half the circumference
            have = set(map(id, corridor))
            for w in ext_ways:
                if w.get("rab") and w["name"] in rab_hit and id(w) not in have:
                    corridor.append(w)
        for w in corridor:
            pts = w["pts"]
            surv_len[w["name"]] = surv_len.get(w["name"], 0.0) + plen(pts)
            for k2 in range(len(pts) - 1):
                a, b = pts[k2], pts[k2 + 1]
                x0 = min(a[0], b[0]) - A_MASK_TOL
                x1 = max(a[0], b[0]) + A_MASK_TOL
                y0 = min(a[1], b[1]) - A_MASK_TOL
                y1 = max(a[1], b[1]) + A_MASK_TOL
                for cx in range(int(x0 // 64), int(x1 // 64) + 1):
                    for cy in range(int(y0 // 64), int(y1 // 64) + 1):
                        a_grid.setdefault((cx, cy), []).append((a, b))
    # A street belongs to the A network when enough of the peeled mask runs
    # over it — this includes short pass-through streets the route follows
    # between fast anchors, and excludes 50 km/h center extensions wholesale.
    a_names = {n for n, L in surv_len.items() if L >= A_MIN_PART}
    if "--debug-a" in sys.argv:
        for n, L in sorted(surv_len.items()):
            print(f"  [A-mask] {n}: {L:.0f} m surviving")

    def mask_share(w):
        """Length share of a way lying on the A-network mask."""
        pts = w["pts"]
        tot = hit = 0.0
        t2 = A_MASK_TOL * A_MASK_TOL
        for i in range(len(pts) - 1):
            L = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            tot += L
            mx = (pts[i][0] + pts[i + 1][0]) / 2.0
            my = (pts[i][1] + pts[i + 1][1]) / 2.0
            for a, b in a_grid.get((int(mx // 64), int(my // 64)), ()):
                if seg_d2(mx, my, a, b) <= t2:
                    hit += L
                    break
        return 0.0 if tot == 0.0 else hit / tot

    # A street whose halves merged by FOOT identity (a railway cut with only
    # a path between them — Jūrmalas gatve at Zolitūde) still looks split:
    # nothing draws the crossing. Route the gap over the pool (paths
    # allowed, same budget the identity probe used) and draw the REAL
    # crossing geometry as the connector; no route means an honest gap.
    FOOT_BRIDGE_MAX = 400.0

    def foot_bridge(segs):
        comps = seg_touch_comps(segs)
        if len(comps) < 2:
            return 0
        cids = sorted(comps)
        roots = {c: c for c in cids}

        def ff(a):
            while roots[a] != a:
                roots[a] = roots[roots[a]]
                a = roots[a]
            return a

        pairs = []
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                best = (1e18, None, None)
                for si in comps[cids[i]]:
                    for sj in comps[cids[j]]:
                        for p in segs[si]:
                            for q in segs[sj]:
                                d2 = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
                                if d2 < best[0]:
                                    best = (d2, p, q)
                gap = best[0] ** 0.5
                if gap <= FOOT_BRIDGE_MAX:
                    pairs.append((gap, cids[i], cids[j]))
        pairs.sort()
        added = 0
        for gap, ca, cb in pairs:
            if ff(ca) == ff(cb):
                continue
            budget = MERGE_K * gap + MERGE_C
            starts = {}
            targets = set()
            for si in comps[ca]:
                for p in segs[si]:
                    k = endkey(p)
                    if k in by_vertex:
                        starts[k] = (p[0], p[1])
            for sj in comps[cb]:
                for p in segs[sj]:
                    k = endkey(p)
                    if k in by_vertex:
                        targets.add(k)
            if not starts or not targets:
                continue
            dist = {k: 0.0 for k in starts}
            kdrawn = {k: 0.0 for k in starts}
            kpt = dict(starts)
            prevv = {}
            h = [(0.0, k) for k in starts]
            heapq.heapify(h)
            goal = None
            while h:
                dv, k = heapq.heappop(h)
                if dv > dist.get(k, 1e18):
                    continue
                if k in targets and dv > 0.0:
                    goal = k
                    break
                for ui, vi in by_vertex.get(k, ()):
                    u = unnamed[ui]
                    drawn = u.get("own_name") is None and not u.get("pool_only")
                    pts = u["pts"]
                    for nvi in (vi - 1, vi + 1):
                        if not (0 <= nvi < len(pts)):
                            continue
                        step = math.hypot(pts[nvi][0] - pts[vi][0],
                                          pts[nvi][1] - pts[vi][1])
                        nd = dv + step
                        if nd > budget:
                            continue
                        k2 = vkeys[ui][nvi]
                        if nd < dist.get(k2, 1e18):
                            dist[k2] = nd
                            kdrawn[k2] = kdrawn[k] + (step if drawn else 0.0)
                            kpt[k2] = (pts[nvi][0], pts[nvi][1])
                            prevv[k2] = k
                            heapq.heappush(h, (nd, k2))
            if goal is None:
                continue
            if kdrawn.get(goal, 0.0) > 0.5 * dist[goal]:
                continue  # the crossing is already drawn as a ctx road —
                          # the map is continuous there without a connector
            path = [kpt[goal]]
            k = goal
            while k in prevv:
                k = prevv[k]
                path.append(kpt[k])
            seg = round_pts(simplify(list(reversed(path)), STREET_TOL))
            if len(seg) >= 2:
                segs.append(seg)
                roots[ff(ca)] = ff(cb)
                added += 1
        return added

    streets = []
    multi_entity = []
    grp_next = 0
    foot_bridged = 0
    frag_demoted = 0
    dc_entities = 0
    dc_removed = 0.0
    late_bridged = 0

    def emit_cluster(name, cways, forced_cls, gid):
        nonlocal dc_entities, dc_removed, late_bridged, foot_bridged
        raw_segs, removed, ncen = collapse_entity(cways)
        if removed > 0.0:
            dc_entities += 1
            dc_removed += removed
        # Weld AFTER simplify/round — simplification moves lines by up
        # to STREET_TOL, which would reopen freshly welded cracks.
        n_raw = len(raw_segs)
        segs = []
        ncen_kept = 0
        for idx, s in enumerate(raw_segs):
            ss = round_pts(simplify(s, STREET_TOL))
            if len(ss) >= 2:
                segs.append(ss)
                if idx >= n_raw - ncen:
                    ncen_kept += 1
        if not segs:
            return
        weld_entity(segs, ncen_kept)
        for s2 in segs:
            s2[0] = [round(s2[0][0]), round(s2[0][1])]
            s2[-1] = [round(s2[-1][0]), round(s2[-1][1])]
        late_bridged += entity_bridge(segs)
        foot_bridged += foot_bridge(segs)
        allpts = [p for s in segs for p in s]
        # Grade from the street's OWN ways — absorbed connectors never
        # vote (an A-refed fragment must not promote its absorber).
        if forced_cls is not None:
            ecls = forced_cls
        else:
            grades = [w["cls"] for w in cways if not w.get("healed")]
            ecls = min(grades) if grades else min(w["cls"] for w in cways)
        streets.append({
            "name": name,
            "cls": ecls,
            "grp": gid,
            "segs": segs,
            "bbox": bbox_of(allpts),
        })

    for name in sorted(by_name):
        group = by_name[name]
        if not group:
            continue
        comp_of, comps, _ends = vertex_components(group)
        cids = sorted(comps)
        # Corridor-tested clustering: same-name components become ONE entity
        # only when a nearly straight drawn-road corridor connects them —
        # ownership-blind, so K. Valdemāra continues over the Vanšu deck and
        # a dogleg donor stays whole across its taken junction stretch.
        # Disconnected namesakes (Kleistu iela's Babīte branch) stay separate
        # entities: hovering one must never light up a road kilometers away.
        roots = {cid: cid for cid in cids}

        def cfind(a):
            while roots[a] != a:
                roots[a] = roots[roots[a]]
                a = roots[a]
            return a

        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                ci, cj = cids[i], cids[j]
                if cfind(ci) == cfind(cj):
                    continue
                pa, pb, gap = closest_vertices(group, comps[ci], comps[cj])
                if gap <= CLUSTER_JOIN and corridor_connected(pa, pb, gap):
                    roots[cfind(ci)] = cfind(cj)
        clusters = {}
        for cid in cids:
            clusters.setdefault(cfind(cid), []).extend(comps[cid])
        cl_list = [[group[wi] for wi in sorted(wis)]
                   for _, wis in sorted(clusters.items())]
        # A cluster made only of patch ways is a stray same-named drive or
        # path with no road link to the real street — not a quiz target of
        # its own, but still real pavement: draw it as context decor.
        kept_cl = []
        for c in cl_list:
            if any(not w.get("patch") for w in c):
                kept_cl.append(c)
                continue
            for w in c:
                seg = round_pts(simplify(w["pts"], STREET_TOL))
                if len(seg) >= 2:
                    context.append({"c": w["cls"], "s": seg, "b": bbox_of(seg)})
                    frag_demoted += 1
        cl_list = kept_cl
        # A cluster that lies MOSTLY outside the strict city polygon is the
        # neighbouring municipality's road caught by the boundary harvest —
        # the trim buffer keeps border-line weaves alive, but a borderline
        # road that barely clips Riga (Kleistu iela's Mārupes novads branch)
        # is not a Riga street: context decor. Only border-region clusters
        # (those the trim pass touched) need the test.
        keep_city = []
        for c in cl_list:
            if not any("out" in w for w in c):
                keep_city.append(c)
                continue
            tot = ins = 0.0
            for w in c:
                pts = w["pts"]
                for i in range(len(pts) - 1):
                    L = math.hypot(pts[i + 1][0] - pts[i][0],
                                   pts[i + 1][1] - pts[i][1])
                    tot += L
                    if strict_city((pts[i][0] + pts[i + 1][0]) / 2.0,
                                   (pts[i][1] + pts[i + 1][1]) / 2.0):
                        ins += L
            if tot > 0.0 and ins / tot < CITY_MIN_SHARE:
                for w in c:
                    seg = round_pts(simplify(w["pts"], STREET_TOL))
                    if len(seg) >= 2:
                        context.append({"c": w["cls"], "s": seg,
                                        "b": bbox_of(seg)})
                        frag_demoted += 1
            else:
                keep_city.append(c)
        cl_list = keep_city
        # Tiny leftover clusters are strays (a renamed service stub, a
        # mistagged drive), not quiz targets: demote them to context decor.
        if len(cl_list) > 1:
            lens = [sum(plen(w["pts"]) for w in c) for c in cl_list]
            main_i = lens.index(max(lens))
            keep = []
            for k, c in enumerate(cl_list):
                if k != main_i and lens[k] < FRAG_MAX_LEN:
                    for w in c:
                        if w.get("patch"):
                            continue
                        seg = round_pts(simplify(w["pts"], STREET_TOL))
                        if len(seg) >= 2:
                            context.append({"c": w["cls"], "s": seg,
                                            "b": bbox_of(seg)})
                            frag_demoted += 1
                else:
                    keep.append(c)
            cl_list = keep
        if len(cl_list) > 1:
            multi_entity.append((name, len(cl_list)))
        for cways in cl_list:
            # One physical street = one grp, even when the A partition emits
            # it as corridor + city entities: renderers hover the whole
            # street. Genuinely separate namesakes are separate clusters,
            # so they keep separate grp ids.
            gid = grp_next
            grp_next += 1
            if name in a_names:
                part_a = [w for w in cways if mask_share(w) >= 0.6]
                aset = set(map(id, part_a))
                rest = [w for w in cways if id(w) not in aset]
                if part_a and rest:
                    # A ref hole in OSM must not cut the corridor in two.
                    # While the A part is disconnected: bridge its two
                    # components through the street's own pieces when the
                    # joining chain stays corridor-scale; a fast island too
                    # far out to join is not the A road — back to the city
                    # street it goes. Either way the corridor ends whole.
                    A_JOIN_MAX = 800.0
                    while True:
                        _c, comps_a, _e = vertex_components(part_a)
                        if len(comps_a) < 2:
                            break
                        comp_of_key = {}
                        clen = {}
                        for cid, wis in comps_a.items():
                            clen[cid] = sum(plen(part_a[wi]["pts"]) for wi in wis)
                            for wi in wis:
                                for p in part_a[wi]["pts"]:
                                    comp_of_key.setdefault(endkey(p), cid)
                        adj = {}
                        for ri, w in enumerate(rest):
                            for p in w["pts"]:
                                adj.setdefault(endkey(p), []).append(ri)
                        best = None  # [rest indices], min total length
                        for cid, wis in comps_a.items():
                            seen = set()
                            frontier = []
                            fseen = set()
                            for wi in wis:
                                for p in part_a[wi]["pts"]:
                                    k = endkey(p)
                                    if k not in fseen:
                                        fseen.add(k)
                                        frontier.append((k, (), 0.0))
                            for _hop in range(15):
                                if not frontier:
                                    break
                                nxt = []
                                for k, chain, dist in frontier:
                                    for ri in adj.get(k, ()):
                                        if ri in seen:
                                            continue
                                        seen.add(ri)
                                        d2 = dist + plen(rest[ri]["pts"])
                                        if d2 > A_JOIN_MAX:
                                            continue
                                        chain2 = chain + (ri,)
                                        hitother = any(
                                            comp_of_key.get(endkey(p)) not in (None, cid)
                                            for p in rest[ri]["pts"])
                                        if hitother:
                                            if best is None or d2 < best[0]:
                                                best = (d2, list(chain2))
                                        else:
                                            for p in rest[ri]["pts"]:
                                                k2 = endkey(p)
                                                if k2 not in fseen:
                                                    fseen.add(k2)
                                                    nxt.append((k2, chain2, d2))
                                frontier = nxt
                            if best is not None:
                                break
                        if best is not None:
                            for ri in sorted(best[1], reverse=True):
                                part_a.append(rest.pop(ri))
                        else:
                            # unbridgeable: smallest component is not the A road
                            drop = min(clen, key=lambda c: (clen[c], c))
                            dropped = [part_a[wi] for wi in comps_a[drop]]
                            keepids = {id(part_a[wi]) for cid2, wis2 in comps_a.items()
                                       if cid2 != drop for wi in wis2}
                            rest.extend(dropped)
                            part_a = [w for w in part_a if id(w) in keepids]
                if part_a and sum(plen(w["pts"]) for w in part_a) >= A_MIN_PART:
                    if rest:
                        emit_cluster(name, part_a, 0, gid)   # the A corridor...
                        emit_cluster(name, rest, None, gid)  # ...and the city tail
                    else:
                        emit_cluster(name, cways, 0, gid)
                    continue
            emit_cluster(name, cways, None, gid)
    streets.sort(key=lambda s: (s["name"], s["bbox"][0], s["bbox"][1]))
    for i, s in enumerate(streets):
        s["id"] = i
    total_km = sum(plen(seg) for s in streets for seg in s["segs"]) / 1000.0
    assert 1150 <= total_km <= 1450, f"named street total {total_km:.0f} km out of sane range"
    print(f"streets: {len(streets)} entities from {len(by_name)} names, {len(ways)} ways; "
          f"{total_km:.0f} km total")
    print(f"  dual carriageways collapsed on {dc_entities} entities "
          f"({dc_removed / 1000.0:.1f} km of doubled line removed)")
    if late_bridged:
        print(f"  {late_bridged} late splits reconnected at emit "
              f"(no entity ships with an internal gap <= {BRIDGE_MAX:.0f} m)")
    if foot_bridged:
        print(f"  {foot_bridged} rail/path cuts drawn across via their real "
              f"crossing geometry")
    if frag_demoted:
        print(f"  {frag_demoted} far same-name fragments demoted to context")
    if multi_entity:
        print(f"  multi-entity names ({len(multi_entity)}):")
        for name, n in multi_entity[:20]:
            print(f"    {name}: {n}")
        if len(multi_entity) > 20:
            print(f"    ... and {len(multi_entity) - 20} more")

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
        "streets": [{"id": s["id"], "name": s["name"], "cls": s["cls"], "grp": s["grp"], "hoods": s["hoods"],
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
