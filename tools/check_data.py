#!/usr/bin/env python3
"""Data-quality report + regression gate for data/riga_data.js.

Compares the built street entities against everything the build ingests
from data/raw/ (base classes, links, named-extra patches, boundary ways,
compound folds), measures residual dual-carriageway doubling and split
components, and enforces regression gates for defects that once plagued
the pipeline (Baznīcas iela absorbing Skolas iela, doubled carriageways,
far same-name stubs). Exits 1 when a gate fails.

Optional argument: a previous riga_data.js to diff per-name entity counts
against (eyeball review of proximity-cluster merges/splits).
"""
import json
import math
import sys
from pathlib import Path

from geo import point_in_ring

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"

BASE_CLASSES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "pedestrian",
]
STREET_CLS_FILES = [f"streets_{c}" for c in BASE_CLASSES] + ["streets_links"]

CELL = 64.0
DBL_MIN, DBL_MAX = 4.0, 55.0        # doubled-carriageway lateral window
DBL_HEAD = math.cos(math.radians(20.0))
DBL_SELF_MIN = 150.0                # same-polyline pairing only beyond this
CTX_DUP_MAX = 6.0                   # ctx-vs-street duplicate distance
COMP_TOL = 30.0                     # entity component tolerance


def load_raw(name):
    return json.loads((RAW_DIR / f"{name}.json").read_text())


def parse_js(path):
    txt = Path(path).read_text()
    return json.loads(txt.split("=", 1)[1].rstrip().rstrip(";"))


def plen(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def doubled_length(polys, sample_filter=None):
    """Meters of polyline that has an ANTI-parallel partner DBL_MIN..DBL_MAX
    away in the same set (both sides counted). Partner must be another
    polyline, or the same one at param distance > DBL_SELF_MIN (one-way
    loops). `sample_filter(px, py)` restricts which samples count — used to
    limit the metric to oneway geometry, since a two-way loop street's legs
    are anti-parallel too without being carriageways."""
    if not polys:
        return 0.0
    segs = []
    cums = []
    for pi, pts in enumerate(polys):
        cum = [0.0]
        for i in range(len(pts) - 1):
            cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0],
                                            pts[i + 1][1] - pts[i][1]))
            segs.append((pts[i], pts[i + 1], pi, cum[i]))
        cums.append(cum)
    grid = {}
    for si, (a, b, pi, c0) in enumerate(segs):
        for cx in range(int(min(a[0], b[0]) // CELL), int(max(a[0], b[0]) // CELL) + 1):
            for cy in range(int(min(a[1], b[1]) // CELL), int(max(a[1], b[1]) // CELL) + 1):
                grid.setdefault((cx, cy), []).append(si)
    total = 0.0
    for pi, pts in enumerate(polys):
        cum = cums[pi]
        t = 0.0
        while t <= cum[-1]:
            i = 0
            while i < len(cum) - 2 and cum[i + 1] < t:
                i += 1
            dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
            ln = math.hypot(dx, dy)
            if ln > 0.0:
                span = cum[i + 1] - cum[i]
                f = 0.0 if span == 0.0 else (t - cum[i]) / span
                px, py = pts[i][0] + f * dx, pts[i][1] + f * dy
                hx, hy = dx / ln, dy / ln
                hit = False
                if sample_filter is not None and not sample_filter(px, py):
                    t += 10.0
                    continue
                for cx in range(int((px - DBL_MAX) // CELL), int((px + DBL_MAX) // CELL) + 1):
                    for cy in range(int((py - DBL_MAX) // CELL), int((py + DBL_MAX) // CELL) + 1):
                        for sj in grid.get((cx, cy), ()):
                            a, b, pj, c0 = segs[sj]
                            sx, sy = b[0] - a[0], b[1] - a[1]
                            sl = math.hypot(sx, sy)
                            if sl == 0.0 or (hx * sx + hy * sy) / sl > -DBL_HEAD:
                                continue
                            tt = ((px - a[0]) * sx + (py - a[1]) * sy) / (sl * sl)
                            tt = max(0.0, min(1.0, tt))
                            d = math.hypot(px - (a[0] + tt * sx), py - (a[1] + tt * sy))
                            if not (DBL_MIN <= d <= DBL_MAX):
                                continue
                            if pj == pi and abs((c0 + tt * sl) - t) < DBL_SELF_MIN:
                                continue
                            hit = True
                            break
                        if hit:
                            break
                    if hit:
                        break
                if hit:
                    total += 10.0
            t += 10.0
    return total


def entity_components(segs):
    """Union segs that physically TOUCH — vertices within COMP_TOL, or an
    endpoint lying on another piece's segment (a T-weld joint). Returns a
    list of (component seg-index list, total length)."""
    n = len(segs)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    boxes = []
    for s in segs:
        xs = [p[0] for p in s]
        ys = [p[1] for p in s]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    t2 = COMP_TOL * COMP_TOL

    def touching(i, j):
        for p in segs[i]:
            for q in segs[j]:
                if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= t2:
                    return True
        for a, b in ((i, j), (j, i)):
            for end in (0, -1):
                p = segs[a][end]
                pts = segs[b]
                for k in range(len(pts) - 1):
                    u, v = pts[k], pts[k + 1]
                    sx, sy = v[0] - u[0], v[1] - u[1]
                    l2 = sx * sx + sy * sy
                    if l2 == 0:
                        continue
                    t = max(0.0, min(1.0, ((p[0] - u[0]) * sx + (p[1] - u[1]) * sy) / l2))
                    ex = p[0] - (u[0] + t * sx)
                    ey = p[1] - (u[1] + t * sy)
                    if ex * ex + ey * ey <= t2:
                        return True
        return False

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            bi, bj = boxes[i], boxes[j]
            dx = max(0.0, max(bi[0], bj[0]) - min(bi[2], bj[2]))
            dy = max(0.0, max(bi[1], bj[1]) - min(bi[3], bj[3]))
            if dx * dx + dy * dy > t2:
                continue
            if touching(i, j):
                parent[find(i)] = find(j)
    comps = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    return [(wis, sum(plen(segs[i]) for i in wis)) for wis in comps.values()]


def main():
    data = parse_js(BASE / "data" / "riga_data.js")
    proj = data["meta"]["proj"]
    kx = 111320.0 * math.cos(math.radians(proj["lat0"]))
    ky = 111132.0

    def project(geom):
        return [((p["lon"] - proj["lon_min"]) * kx,
                 (proj["lat_max"] - p["lat"]) * ky) for p in geom]

    # --- raw ingest baseline, mirroring the build's intake rules ---
    # Only ways whose midpoint lies strictly inside the city polygon (the
    # apkaimes union) count: the build trims out-of-city continuations, so
    # comparing against them would report phantom losses.
    hoods = data["hoods"]

    def strict_city(x, y):
        for h in hoods:
            bb = h["bbox"]
            if bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3] and \
                    any(point_in_ring(x, y, r) for r in h["rings"]):
                return True
        return False

    def mid_inside(pts):
        m = pts[len(pts) // 2]
        return strict_city(m[0], m[1])

    raw_ways = {}   # name -> [pts]
    ow_grid = {}    # spatial grid of raw oneway segments (8 m inflated)

    def add_oneway(pts):
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            x0, x1 = min(a[0], b[0]) - 8.0, max(a[0], b[0]) + 8.0
            y0, y1 = min(a[1], b[1]) - 8.0, max(a[1], b[1]) + 8.0
            for cx in range(int(x0 // CELL), int(x1 // CELL) + 1):
                for cy in range(int(y0 // CELL), int(y1 // CELL) + 1):
                    ow_grid.setdefault((cx, cy), []).append((a, b))

    def near_oneway(px, py):
        for a, b in ow_grid.get((int(px // CELL), int(py // CELL)), ()):
            sx, sy = b[0] - a[0], b[1] - a[1]
            sl2 = sx * sx + sy * sy
            if sl2 == 0.0:
                continue
            tt = max(0.0, min(1.0, ((px - a[0]) * sx + (py - a[1]) * sy) / sl2))
            dx, dy = px - (a[0] + tt * sx), py - (a[1] + tt * sy)
            if dx * dx + dy * dy <= 64.0:
                return True
        return False

    for f in STREET_CLS_FILES:
        for el in load_raw(f)["elements"]:
            if el["type"] != "way" or not el.get("geometry"):
                continue
            tags = el.get("tags", {})
            name = tags.get("name", "").strip()
            if not name:
                continue
            pts = project(el["geometry"])
            if len(pts) < 2:
                continue
            if tags.get("oneway") in ("yes", "1", "true", "-1"):
                add_oneway(pts)
            if mid_inside(pts):
                raw_ways.setdefault(name, []).append(pts)
    for el in load_raw("streets_named_extra")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        name = el.get("tags", {}).get("name", "").strip()
        if name not in raw_ways:
            continue
        pts = project(el["geometry"])
        if len(pts) >= 2 and mid_inside(pts):
            raw_ways[name].append(pts)
    bboxes = {}
    for name, polys in raw_ways.items():
        xs = [p[0] for pts in polys for p in pts]
        ys = [p[1] for pts in polys for p in pts]
        bboxes[name] = (min(xs), min(ys), max(xs), max(ys))
    for el in load_raw("streets_boundary")["elements"]:
        if el["type"] != "way" or not el.get("geometry"):
            continue
        name = el.get("tags", {}).get("name", "").strip()
        if name not in raw_ways:
            continue
        pts = project(el["geometry"])
        if len(pts) < 2:
            continue
        bb = bboxes[name]
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        if bb[0] - 350 <= mx <= bb[2] + 350 and bb[1] - 350 <= my <= bb[3] + 350 \
                and mid_inside(pts):
            raw_ways[name].append(pts)
    for comp_name in [n for n in list(raw_ways) if " / " in n]:
        parts = [p.strip() for p in comp_name.split(" / ")]
        matched = [p for p in parts if p in raw_ways]
        polys = raw_ways.pop(comp_name)
        for p in matched:
            raw_ways[p].extend(polys)

    raw_len = {n: sum(plen(pts) for pts in polys) for n, polys in raw_ways.items()}

    # --- built side ---
    streets = data["streets"]
    by_name = {}
    for s in streets:
        by_name.setdefault(s["name"], []).append(s)
    built_len = {n: sum(plen(seg) for s in ents for seg in s["segs"])
                 for n, ents in by_name.items()}
    total_built = sum(built_len.values()) / 1000.0
    total_raw = sum(raw_len.get(n, 0.0) for n in by_name) / 1000.0
    print(f"built: {len(streets)} entities, {len(by_name)} names, {total_built:.1f} km "
          f"(raw ingest of those names: {total_raw:.1f} km)")

    # --- per-name deltas, collapse-adjusted ---
    # Collapsing a doubled pair removes about half its doubled length, so the
    # expected built length is raw - raw_doubled/2.
    print("computing raw doubled-carriageway lengths...")
    raw_dbl = {n: doubled_length(raw_ways[n], near_oneway)
               for n in by_name if n in raw_ways}
    # Plain streets (no meaningful oneway doubling) have expected == raw, so
    # a large delta on them is a real anomaly: a gain means absorbed
    # geometry (fine when it reconstructs a fragmented street via unnamed
    # connectors, catastrophic when it steals a parallel named street), a
    # loss means geometry taken away (the Skolas iela disease) or dropped.
    gains = []
    losses = []
    plain_gains = []
    plain_losses = []
    for n in sorted(by_name):
        if n not in raw_len:
            continue
        expected = raw_len[n] - raw_dbl.get(n, 0.0) / 2.0
        delta = built_len[n] - expected
        if delta > 150.0:
            gains.append((delta, n))
            if raw_dbl.get(n, 0.0) < 100.0:
                plain_gains.append((delta, n))
        if delta < -150.0:
            losses.append((delta, n))
            if raw_dbl.get(n, 0.0) < 100.0:
                plain_losses.append((delta, n))
    gains.sort(reverse=True)
    losses.sort()
    print(f"names gaining >150 m vs collapse-adjusted raw: {len(gains)} "
          f"({len(plain_gains)} on plain streets)")
    for d, n in gains[:20]:
        print(f"  +{d:7.0f} m  {n}  (raw {raw_len[n]:.0f}, dbl {raw_dbl.get(n, 0):.0f}, "
              f"built {built_len[n]:.0f})")
    print(f"names losing >150 m vs collapse-adjusted raw: {len(losses)} "
          f"({len(plain_losses)} on plain streets)")
    for d, n in losses[:20]:
        print(f"  {d:8.0f} m  {n}  (raw {raw_len[n]:.0f}, dbl {raw_dbl.get(n, 0):.0f}, "
              f"built {built_len[n]:.0f})")

    # --- residual doubling in the built layers ---
    print("computing built doubled-carriageway lengths...")
    built_dbl = 0.0
    for s in streets:
        built_dbl += doubled_length(s["segs"], near_oneway)
    print(f"built streets doubled line (on oneway geometry): {built_dbl / 1000.0:.1f} km")

    # --- ctx duplicating street geometry ---
    ctx = data["ctx"]
    sgrid = {}
    for s in streets:
        for seg in s["segs"]:
            for i in range(len(seg) - 1):
                a, b = seg[i], seg[i + 1]
                for cx in range(int(min(a[0], b[0]) // CELL), int(max(a[0], b[0]) // CELL) + 1):
                    for cy in range(int(min(a[1], b[1]) // CELL), int(max(a[1], b[1]) // CELL) + 1):
                        sgrid.setdefault((cx, cy), []).append((a, b))
    dup = 0.0
    for c in ctx:
        pts = c["s"]
        cum = [0.0]
        for i in range(len(pts) - 1):
            cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0],
                                            pts[i + 1][1] - pts[i][1]))
        t = 0.0
        while t <= cum[-1]:
            i = 0
            while i < len(cum) - 2 and cum[i + 1] < t:
                i += 1
            dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
            ln = math.hypot(dx, dy)
            if ln > 0.0:
                span = cum[i + 1] - cum[i]
                f = 0.0 if span == 0.0 else (t - cum[i]) / span
                px, py = pts[i][0] + f * dx, pts[i][1] + f * dy
                for a, b in sgrid.get((int(px // CELL), int(py // CELL)), ()):
                    sx, sy = b[0] - a[0], b[1] - a[1]
                    sl = math.hypot(sx, sy)
                    if sl == 0.0 or abs((dx * sx + dy * sy) / (ln * sl)) < DBL_HEAD:
                        continue
                    tt = max(0.0, min(1.0, ((px - a[0]) * sx + (py - a[1]) * sy) / (sl * sl)))
                    if math.hypot(px - (a[0] + tt * sx), py - (a[1] + tt * sy)) <= CTX_DUP_MAX:
                        dup += 10.0
                        break
            t += 10.0
    print(f"ctx duplicating street line: {dup / 1000.0:.2f} km")

    # --- split components ---
    print("computing entity components...")
    multi = []
    for s in streets:
        comps = entity_components(s["segs"])
        if len(comps) > 1:
            multi.append((s, comps))
    hist = {}
    for s, comps in multi:
        hist[len(comps)] = hist.get(len(comps), 0) + 1
    print(f"multi-component entities: {len(multi)} of {len(streets)}  {dict(sorted(hist.items()))}")

    # --- visible holes: entity gaps with NOTHING drawn across them ---
    # A split whose gap is spanned by other drawn geometry (the middle
    # belongs to a crossing street, ctx ramps cover an interchange) looks
    # continuous on the map — only the highlight pauses, which is correct
    # ownership. A gap with nothing near its midpoint is a real hole.
    dgrid = dict(sgrid)  # streets grid built above; add ctx on top
    for c in ctx:
        pts = c["s"]
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            for cx in range(int(min(a[0], b[0]) // CELL), int(max(a[0], b[0]) // CELL) + 1):
                for cy in range(int(min(a[1], b[1]) // CELL), int(max(a[1], b[1]) // CELL) + 1):
                    dgrid.setdefault((cx, cy), [])
                    if dgrid[(cx, cy)] is sgrid.get((cx, cy)):
                        dgrid[(cx, cy)] = list(dgrid[(cx, cy)])
                    dgrid[(cx, cy)].append((a, b))

    def near_drawn(px, py, tol):
        t2 = tol * tol
        for cx in range(int((px - tol) // CELL), int((px + tol) // CELL) + 1):
            for cy in range(int((py - tol) // CELL), int((py + tol) // CELL) + 1):
                for a, b in dgrid.get((cx, cy), ()):
                    sx, sy = b[0] - a[0], b[1] - a[1]
                    l2 = sx * sx + sy * sy
                    if l2 == 0.0:
                        continue
                    tt = max(0.0, min(1.0, ((px - a[0]) * sx + (py - a[1]) * sy) / l2))
                    ex, ey = px - (a[0] + tt * sx), py - (a[1] + tt * sy)
                    if ex * ex + ey * ey <= t2:
                        return True
        return False

    holes = []
    tight_splits = []   # entity gaps <= 90 m must not exist (emit invariant)
    for s, comps in multi:
        worst = None
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                best = (1e18, None, None)
                for si in comps[i][0]:
                    for sj in comps[j][0]:
                        for p in s["segs"][si]:
                            for q in s["segs"][sj]:
                                d2 = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
                                if d2 < best[0]:
                                    best = (d2, p, q)
                gap = math.sqrt(best[0])
                if gap <= 90.0:
                    tight_splits.append((gap, s["name"]))
                if gap > 2000.0:
                    continue  # far components are effectively separate streets
                mx, my = (best[1][0] + best[2][0]) / 2, (best[1][1] + best[2][1]) / 2
                if not near_drawn(mx, my, 30.0):
                    if worst is None or gap > worst:
                        worst = gap
        if worst is not None:
            holes.append((worst, s["name"]))
    holes.sort(reverse=True)
    print(f"visible holes (gap midpoint bare within 30 m): {len(holes)}")
    for g, n in holes[:15]:
        print(f"  {g:6.0f} m gap  {n}")

    # --- hairline cracks: endpoints a few meters shy of their own street ---
    # (unshared OSM junction nodes, collapse cut offsets — the build welds
    # these; anything left in the 2-15 m band is a regression)
    cracks = 0
    cracks_wide = 0
    for s in streets:
        segs = s["segs"]
        if len(segs) < 2:
            continue
        egrid = {}
        for pi, pts in enumerate(segs):
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                for cx in range(int(min(a[0], b[0]) // CELL), int(max(a[0], b[0]) // CELL) + 1):
                    for cy in range(int(min(a[1], b[1]) // CELL), int(max(a[1], b[1]) // CELL) + 1):
                        egrid.setdefault((cx, cy), []).append((pi, i))
        for pi, pts in enumerate(segs):
            for end in (0, -1):
                p = pts[end]
                best = None
                for cx in range(int((p[0] - 24) // CELL), int((p[0] + 24) // CELL) + 1):
                    for cy in range(int((p[1] - 24) // CELL), int((p[1] + 24) // CELL) + 1):
                        for pj, j in egrid.get((cx, cy), ()):
                            if pj == pi:
                                continue
                            a, b = segs[pj][j], segs[pj][j + 1]
                            sx, sy = b[0] - a[0], b[1] - a[1]
                            l2 = sx * sx + sy * sy
                            if l2 == 0:
                                continue
                            tt = max(0.0, min(1.0, ((p[0] - a[0]) * sx + (p[1] - a[1]) * sy) / l2))
                            d = math.hypot(p[0] - (a[0] + tt * sx), p[1] - (a[1] + tt * sy))
                            if best is None or d < best:
                                best = d
                if best is not None and 2.0 < best <= 20.0:
                    if best <= 15.0:
                        cracks += 1
                    else:
                        cracks_wide += 1
    print(f"hairline cracks: {cracks} in 2-15 m (welded band), "
          f"{cracks_wide} in 15-20 m (lane ends, by design)")

    # --- optional per-name entity-count diff against a previous build ---
    if len(sys.argv) > 1:
        old = parse_js(sys.argv[1])
        old_counts = {}
        for s in old["streets"]:
            old_counts[s["name"]] = old_counts.get(s["name"], 0) + 1
        new_counts = {n: len(e) for n, e in by_name.items()}
        changed = [(n, old_counts.get(n, 0), new_counts.get(n, 0))
                   for n in sorted(set(old_counts) | set(new_counts))
                   if old_counts.get(n, 0) != new_counts.get(n, 0)]
        print(f"entity-count changes vs {sys.argv[1]}: {len(changed)}")
        for n, o, w in changed:
            print(f"  {n}: {o} -> {w}")

    # --- regression gates (2026-09 OSM snapshot; adjust after refetches) ---
    fails = []

    def gate(ok, msg):
        print(("PASS  " if ok else "FAIL  ") + msg)
        if not ok:
            fails.append(msg)

    gate(700 <= built_len.get("Skolas iela", 0) <= 780,
         f"Skolas iela length {built_len.get('Skolas iela', 0):.0f} m in [700, 780] "
         "(the old healing handed 82% of it to Baznīcas iela)")
    gate(650 <= built_len.get("Baznīcas iela", 0) <= 800,
         f"Baznīcas iela length {built_len.get('Baznīcas iela', 0):.0f} m in [650, 800] "
         "(was 1936 m after absorbing six neighbors)")
    gate(built_len.get("Šķeltu iela", 0) < 150,
         f"Šķeltu iela {built_len.get('Šķeltu iela', 0):.0f} m — a 33 m border "
         "stub, demoted to ctx (once blew up to 920 m of absorbed geometry)")
    gate(len(by_name.get("Krišjāņa Valdemāra iela", [])) == 1,
         "Krišjāņa Valdemāra iela is one entity across the Daugava "
         "(corridor over the Vanšu deck)")
    gate(len(by_name.get("Kleistu iela", [])) == 1,
         "Kleistu iela is ONE clean line — its border-line branch into "
         "Mārupes novads is context decor, not a Riga street")
    gate(850 <= built_len.get("Vanšu tilts", 0) <= 1100,
         f"Vanšu tilts street {built_len.get('Vanšu tilts', 0):.0f} m in [850, 1100] "
         "(collapsed deck; the avenue must not steal the bridge)")
    gate(14000 <= built_len.get("Latgales iela", 0) <= 17000
         and len(by_name.get("Latgales iela", [])) == 2,
         f"Latgales iela {built_len.get('Latgales iela', 0):.0f} m in [14, 17] km, "
         "two entities: the A6 corridor + the city street it becomes")
    tier0 = [s for s in streets if s["cls"] == 0]
    t0_split = [s["name"] for s in tier0
                if len(entity_components(s["segs"])) > 1]
    gate(13 <= len(tier0) <= 20 and not t0_split,
         f"{len(tier0)} A-road entities, every one a single connected "
         f"corridor{'' if not t0_split else ' — SPLIT: ' + str(t0_split)}")
    t0_segs = [seg for s in tier0 for seg in s["segs"]]
    t0_net = len(entity_components(t0_segs)) if t0_segs else 99
    gate(t0_net <= 2,
         f"the A-road network forms {t0_net} connected component(s) <= 2 "
         "(A2 joins the southern system via the signed Slāvu route)")
    gate(built_dbl / 1000.0 < 40.0,
         f"built doubled line {built_dbl / 1000.0:.1f} km < 40 km "
         "(the old committed data measures 256.5 km on this metric)")
    gate(dup / 1000.0 < 6.5,
         f"ctx duplicating street line {dup / 1000.0:.2f} km < 6.5 km (was 11.78)")
    gate(len(plain_losses) < 15,
         f"{len(plain_losses)} plain streets lost >150 m (< 15; deliberate "
         "demotions of disconnected same-named strays to ctx — theft-class "
         "regressions are caught by the Skolas/Baznīcas/Šķeltu gates)")
    gate(len(plain_gains) < 55,
         f"{len(plain_gains)} plain streets gained >150 m (< 55; boundary "
         "continuations and unnamed-connector reconstruction are legitimate)")
    gate(len(multi) < 120,
         f"{len(multi)} multi-component entities < 120 (217 in the old data; "
         "every remaining split is corridor-connected — the highlight pauses "
         "over another street's stretch while the map stays continuous)")
    gate(len(holes) < 40,
         f"{len(holes)} entities with a visibly bare gap < 40 (rail yards, "
         "field crossings — nothing to draw there per OSM)")
    gate(cracks < 15,
         f"{cracks} hairline cracks in the welded 2-15 m band < 15 "
         "(467 before the entity weld existed)")
    gate(len(tight_splits) == 0,
         f"{len(tight_splits)} entities with an internal gap <= 90 m "
         "(emit invariant: late splits reconnect at build time — "
         "the Aleksandra Čaka iela crumb-drop bug class)"
         + ("" if not tight_splits else " " + str(tight_splits[:5])))
    gate(1800 <= len(streets) <= 1940,
         f"entity count {len(streets)} in [1800, 1940]")

    if fails:
        print(f"\n{len(fails)} gate(s) FAILED")
        sys.exit(1)
    print("\nall gates passed")


if __name__ == "__main__":
    main()
