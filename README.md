# Pazīsti Latviju

A Seterra-style game for learning the geography of Latvia and Riga. The game
names a place — a street, neighborhood, municipality, city, river, lake … —
and you click it on the map. Green: first try. Yellow: second. Orange: third.
Red: revealed after three misses (or a skip).

The UI is Latvian by default; English is available from the ⚙️ settings
popover (the choice is remembered).

## Play

Open `index.html` in a browser (double-clicking works — no server needed), or:

```
python3 tools/serve.py
```

and visit <http://localhost:8747>. (The bundled server disables HTTP caching,
so rebuilt data and code changes always show up on plain reload.)

- **Citywide levels**: Major arteries, Whole city (marathon), a
  **Neighborhoods quiz** (click the apkaime polygon — "Click: Teika"), a
  **Bridges quiz** (every named bridge, viaduct, and overpass, from Vanšu
  tilts to Gaisa tilts to the canal footbridges), and a **Parks quiz** —
  the city's named parks, gardens, and squares.
- **Public transport levels**: Trams (8), Trolleybuses (21), Buses day (51)
  and night (8), and the five Train lines by direction — the game names a
  line ("Click: Tramvajs 10 (Bišumuiža – Centrāltirgus)") and you click
  anywhere along its route.
- **Neighborhood levels**: one per apkaime (Vecrīga, Centrs, Āgenskalns, …).
- **Latvia levels**: state cities & municipalities, city dots in two
  population tiers, the full pagasti mosaic (marathon + one level per
  novads), historical lands, rivers, lakes, the A-road network, castles,
  and national parks & reserves.
- Controls: drag to pan, scroll to zoom, `S` to skip, `Esc` for menu.
- Bottom-left buttons: light/dark theme and the ⚙️ settings popover — map
  panning/zooming locks, sound (synthesized effects, no audio files),
  **fast mode** (with panning locked, a mouse press counts as the click),
  language, and Reset view. All preferences are remembered.
- Best results per level are stored in your browser (localStorage).

## Regenerating the map data

`data/riga_data.js` is generated from OpenStreetMap via the Overpass API:

```
python3 tools/fetch_osm.py     # downloads raw data into data/raw/ (cached)
python3 tools/build_data.py    # processes it into data/riga_data.js
```

`fetch_osm.py` skips files already in `data/raw/` — delete them to force a
fresh download. Both scripts use only the Python standard library.

The Latvia dataset (`data/latvia_data.js`) is built the same way:

```
python3 tools/fetch_osm.py latvia_
python3 tools/build_latvia.py
```

### How a "street" is defined

OSM has no street objects, only road segments whose `name` label changes for
many reasons mid-road. The build reconstructs human streets with these rules:

1. A street = all ways sharing a name (street classes), grouped by proximity.
   Compound border names ("A / B") count toward both A and B; ways just
   outside the city boundary join their in-city street; named ways of minor
   classes (service/track/path…) only complete existing streets, never
   create new ones.
2. If a street's geometry is in several disconnected pieces and a *nearly
   direct* chain of other road segments (any class, named or not, ≤1.4× the
   straight-line distance +60 m, ≤1 km) connects two pieces, that chain is
   drawn as part of the street too — clipped where it meets it, and shared
   with whatever street it belongs to itself.
3. Gaps that remain are real: the road is under construction, pedestrianized,
   or the same name is used for genuinely separate pieces.

## Data license

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright)
contributors, available under the Open Database License (ODbL).
