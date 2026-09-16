# Riga Streets

A Seterra-style game for learning the street layout of Riga. The game names a
street — you click it on the map. Green: first try. Yellow: second. Orange:
third. Red: revealed after three misses (or a skip).

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
- **Study mode**: pan/zoom freely, hover for names, click to pin labels.
  The **Districts** toggle switches from streets to neighborhoods (hover a
  district for its name, pin district labels); Shading and Majors-only
  toggles adjust the backdrop.
- Controls: drag to pan, scroll to zoom, `S` to skip a street, `Esc` for menu.
- Bottom-left buttons: light/dark theme and sound on/off (synthesized effects,
  no audio files). Both preferences are remembered.
- Best results per level are stored in your browser (localStorage).

## Regenerating the map data

`data/riga_data.js` is generated from OpenStreetMap via the Overpass API:

```
python3 tools/fetch_osm.py     # downloads raw data into data/raw/ (cached)
python3 tools/build_data.py    # processes it into data/riga_data.js
```

`fetch_osm.py` skips files already in `data/raw/` — delete them to force a
fresh download. Both scripts use only the Python standard library.

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
