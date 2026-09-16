"""Shared planar-geometry helpers for the data builders.

Extracted verbatim from build_data.py so build_latvia.py can reuse them.
All functions are pure and operate on [(x, y), ...] point lists in
projected meters (y-down, as produced by each builder's project()).
"""


def simplify(points, tol):
    """Iterative Douglas-Peucker on [(x, y), ...]."""
    n = len(points)
    if n < 3:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    t2 = tol * tol
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        best, bestd = -1, 0.0
        for i in range(a + 1, b):
            px, py = points[i]
            if l2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
                cx, cy = ax + t * dx, ay + t * dy
                d2 = (px - cx) ** 2 + (py - cy) ** 2
            if d2 > bestd:
                best, bestd = i, d2
        if bestd > t2:
            keep[best] = True
            stack.append((a, best))
            stack.append((best, b))
    return [p for p, k in zip(points, keep) if k]


def round_pts(points):
    out = []
    for x, y in points:
        p = [round(x), round(y)]
        if not out or out[-1] != p:
            out.append(p)
    return out


def bbox_of(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def bbox_union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def bbox_overlap(a, b, margin=0.0):
    return (a[0] - margin <= b[2] and b[0] - margin <= a[2]
            and a[1] - margin <= b[3] and b[1] - margin <= a[3])


def ring_area(ring):
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(s) / 2.0


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            xcross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < xcross:
                inside = not inside
        j = i
    return inside


def stitch_rings(pieces, tol=1.0):
    """Stitch open polyline pieces into closed rings.

    Returns (rings, leftover_count). Pieces already closed pass straight through.
    """
    def close_enough(p, q):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    pool = [list(p) for p in pieces if len(p) >= 2]
    rings, leftover = [], 0
    while pool:
        ring = pool.pop()
        while not close_enough(ring[0], ring[-1]):
            found = False
            for i, cand in enumerate(pool):
                if close_enough(ring[-1], cand[0]):
                    ring.extend(cand[1:])
                elif close_enough(ring[-1], cand[-1]):
                    ring.extend(reversed(cand[:-1]))
                else:
                    continue
                pool.pop(i)
                found = True
                break
            if not found:
                leftover += 1
                ring = None
                break
        if ring is not None and len(ring) >= 4:
            ring[-1] = ring[0]
            rings.append(ring)
    return rings, leftover
