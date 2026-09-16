#!/usr/bin/env python3
"""Fetch raw OSM data for the Riga streets game from the Overpass API.

Downloads street geometry (one query per highway class), apkaime
(neighborhood) boundaries and water polygons into data/raw/*.json.
Files already present are skipped — delete a file to force a refresh.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

AREA = "area(3613048688)->.riga;"  # Riga city, OSM relation 13048688
HOSTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "riga-streets-game/1.0 (personal learning project)"

STREET_CLASSES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "pedestrian",
]

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def queries():
    qs = {}
    for cls in STREET_CLASSES:
        qs[f"streets_{cls}"] = (
            f'[out:json][timeout:180];{AREA}'
            f'way(area.riga)["highway"="{cls}"]["name"];out geom qt;'
        )
    # Unnamed roads and link ramps: drawn as context so the network looks
    # complete (bridge decks and junction pieces often carry no name tag).
    qs["streets_unnamed"] = (
        f'[out:json][timeout:180];{AREA}'
        'way(area.riga)["highway"~"^(motorway|trunk|primary|secondary|tertiary|'
        'unclassified|residential|living_street|pedestrian)$"][!"name"];out geom qt;'
    )
    qs["streets_links"] = (
        f'[out:json][timeout:180];{AREA}'
        'way(area.riga)["highway"~"^(motorway_link|trunk_link|primary_link|'
        'secondary_link|tertiary_link)$"];out geom qt;'
    )
    # Named ways of otherwise-excluded classes (e.g. an unpaved stretch of a
    # street tagged highway=track): used only to patch existing streets.
    qs["streets_named_extra"] = (
        f'[out:json][timeout:180];{AREA}'
        'way(area.riga)["highway"~"^(service|track|footway|cycleway|path|steps)$"]'
        '["name"];out geom qt;'
    )
    # Unnamed service/track ways: absorption-pool only — candidates for
    # bridging same-named street pieces; never drawn on their own.
    qs["streets_pool_minor"] = (
        f'[out:json][timeout:300];{AREA}'
        'way(area.riga)["highway"~"^(service|track)$"][!"name"];out geom qt;'
    )
    # Named ways JUST OUTSIDE the city boundary (any highway class): streets
    # like Berģu iela weave across it, and the area filter leaves holes.
    qs["streets_boundary"] = (
        f'[out:json][timeout:300][bbox:56.85,23.92,57.09,24.33];{AREA}'
        '(way["highway"]["name"];'
        '- way(area.riga)["highway"]["name"];);out geom qt;'
    )
    # Everything locals call a bridge: bridge:name ways, named bridge
    # outlines, and roads/railways named tilts/tiltiņš/viadukts/pārvads/...
    qs["bridges"] = (
        f'[out:json][timeout:120];{AREA}('
        'way(area.riga)["bridge:name"];'
        'way(area.riga)["man_made"="bridge"]["name"];'
        'way(area.riga)["highway"]["name"~"(tilts|tiltiņš|viadukts|pārvads|estakāde)$"];'
        'way(area.riga)["railway"]["name"~"(tilts|tiltiņš|viadukts|pārvads|estakāde)$"];'
        'way(area.riga)["railway"]["bridge:name"];'
        ");out geom qt;"
    )
    qs["parks"] = (
        f'[out:json][timeout:120];{AREA}('
        'way(area.riga)["leisure"~"^(park|garden)$"]["name"];'
        'relation(area.riga)["leisure"~"^(park|garden)$"]["name"];'
        ");out geom qt;"
    )
    # Public transport routes: relations (tags + member ids) fetched apart
    # from member-way geometry, so shared corridor ways download only once.
    transit_rel = ('relation(area.riga)["type"="route"]'
                   '["route"~"^(tram|trolleybus|bus|train)$"]')
    qs["transit_routes"] = f"[out:json][timeout:180];{AREA}{transit_rel};out body;"
    qs["transit_ways"] = (
        f"[out:json][timeout:300];{AREA}{transit_rel}->.r;"
        "way(r.r)(56.85,23.92,57.09,24.33);out geom qt;"
    )
    qs["apkaimes"] = (
        f'[out:json][timeout:180];{AREA}'
        'relation(area.riga)["boundary"="administrative"]["admin_level"="10"];'
        "out geom qt;"
    )
    qs["water"] = (
        f'[out:json][timeout:300];{AREA}('
        'way(area.riga)["natural"="water"];'
        'relation(area.riga)["natural"="water"];'
        'way(area.riga)["waterway"="riverbank"];'
        'relation(area.riga)["waterway"="riverbank"];'
        ");out geom qt;"
    )
    return qs


def fetch(query, name):
    payload = urllib.parse.urlencode({"data": query}).encode()
    delays = [0, 10, 30]
    for host in HOSTS:
        for attempt, delay in enumerate(delays, 1):
            if delay:
                time.sleep(delay)
            try:
                req = urllib.request.Request(host, data=payload, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=360) as resp:
                    body = resp.read()
                json.loads(body)  # an HTML error page would fail here
                return body
            except Exception as exc:
                host_name = urllib.parse.urlparse(host).netloc
                print(f"  {name}: attempt {attempt} on {host_name} failed: {exc}", flush=True)
    raise RuntimeError(f"all attempts failed for {name}")


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, query in queries().items():
        path = RAW_DIR / f"{name}.json"
        if path.exists():
            print(f"{name}: cached ({path.stat().st_size / 1e6:.1f} MB)", flush=True)
            continue
        t0 = time.time()
        body = fetch(query, name)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.rename(path)
        print(f"{name}: {len(body) / 1e6:.1f} MB in {time.time() - t0:.0f}s", flush=True)
        time.sleep(2)

    total_ways = 0
    names = set()
    for cls in STREET_CLASSES:
        data = json.loads((RAW_DIR / f"streets_{cls}.json").read_text())
        ways = [e for e in data["elements"] if e["type"] == "way"]
        total_ways += len(ways)
        names.update(w["tags"]["name"] for w in ways)
        print(f"  {cls}: {len(ways)} ways")
    for extra in ("streets_unnamed", "streets_links", "streets_named_extra"):
        data = json.loads((RAW_DIR / f"{extra}.json").read_text())
        n = sum(1 for e in data["elements"] if e["type"] == "way")
        print(f"  {extra}: {n} ways")
    hoods = json.loads((RAW_DIR / "apkaimes.json").read_text())
    n_hoods = sum(1 for e in hoods["elements"] if e["type"] == "relation")
    water = json.loads((RAW_DIR / "water.json").read_text())
    n_water = len(water["elements"])
    print(f"TOTAL: {total_ways} street ways, {len(names)} distinct names, "
          f"{n_hoods} apkaimes, {n_water} water elements")
    if not 8000 <= total_ways <= 9500:
        print(f"WARNING: way count {total_ways} far from the ~8608 seen during planning")
    if n_hoods != 58:
        print(f"WARNING: expected 58 apkaimes, got {n_hoods}")
    if total_ways == 0 or n_hoods < 50:
        sys.exit("FATAL: data looks wrong, aborting")


if __name__ == "__main__":
    main()
