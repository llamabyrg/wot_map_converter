# wot_map_converter

Converts a [Mudlet](https://www.mudlet.org/) JSON map export into a
[CMUD](https://www.zuggsoft.com/) 3 mapper database (`.dbm`).

It was written for the [WoTMUD](https://www.wotmud.info/) community map kept at
[weisluke/WoTMUD](https://github.com/weisluke/WoTMUD), and downloads that map by
default, but it works on any Mudlet JSON export.

* One file, `mudlet2cmud.py`, Python 3 standard library only.
* Builds the `.dbm` from scratch; no existing CMUD map is needed as a template.
* `wotmud.dbm` in this repo is a ready-made conversion of the
  WoTMUD map (see [the command that built it](#the-map-in-this-repo)).

## Quick start

```sh
# download the latest WoTMUD map from GitHub and write WoTMUD_map.dbm
python3 mudlet2cmud.py

# convert a local Mudlet JSON map export
python3 mudlet2cmud.py mymap.json -o mymap.dbm
```

Open the result from CMUD's mapper, or copy it over your session's map file
**while CMUD is closed**. CMUD writes into a map as soon as
it opens it, so never overwrite a map it currently has open.

## Options

```
python3 mudlet2cmud.py [input] [-o OUTPUT] [-f] [--zones {area,userdata}]
                       [--zone-key KEY] [--mark-zones] [--boundary-color RRGGBB]
                       [--env-color ID=RRGGBB]... [--scale N] [--z-step N]
```

| Option | Default | What it does |
|---|---|---|
| `input` | the WoTMUD map on GitHub | Mudlet JSON map: a file, or an `http(s)` URL. GitHub page links (`github.com/.../blob/...`) are rewritten to the raw file automatically. |
| `-o`, `--output FILE` | input name + `.dbm` | The CMUD map to write. |
| `-f`, `--force` | off | Overwrite the output if it exists. Without it the script stops before downloading or converting anything. |
| `--zones area` | ✔ | One CMUD zone per Mudlet area. |
| `--zones userdata` | | Split each Mudlet area into one CMUD zone per value of a room `userData` key (see `--zone-key`). Coordinates are unchanged, the area becomes the parent zone and keeps rooms that have no value, and each map label goes to the zone of the room nearest to it. |
| `--zone-key KEY` | `zone` | The room `userData` key that names a room's zone. Used by `--zones userdata` and `--mark-zones`. |
| `--mark-zones` | off | Make the `--zone-key` zones visible on the map: a bold name label in the middle of each zone (area mode only), and every exit that crosses from one zone to another drawn in the boundary colour. |
| `--boundary-color RRGGBB` | `ff0000` | Colour of the zone-crossing exits. Only valid with `--mark-zones`. |
| `--env-color ID=RRGGBB` | the map's own colours | Override the room colour of one Mudlet environment (terrain) id. Repeatable. |
| `--scale N` | auto | CMUD units per Mudlet unit. Auto picks the value that turns the map's most common room spacing into CMUD's 240. |
| `--z-step N` | auto | Mudlet z units per CMUD level. Auto uses the greatest common divisor of all z values. |

At the end the script prints what it wrote and what it had to skip.

### Zones

The WoTMUD map has one huge Mudlet area, `WoTMUD`, holding the whole outdoor
world (about 15,000 rooms), plus an area for each city and special zone. Every
room also carries the MUD's own zone name in `userData.zone`.

* `--zones area` (default) keeps that structure: one continuous overworld.
* `--zones userdata` breaks it into roughly 290 CMUD zones of about 100 rooms,
  which is closer to how hand-made CMUD maps are usually organised.
* `--mark-zones` is the middle ground: keep the continuous overworld, but label
  the zones and colour the links between them.

`--mark-zones` adds three exit types to the map, *Zone Boundary Normal Exit*,
*Zone Boundary Door* and *Zone Boundary Locked Door*, plus a *Zone Name* text
style. Changing those in CMUD restyles every boundary or zone label at once.
On a light map background a strong colour such as `ff6600` reads much better
than yellow.

### Room colours

Room colours come from the Mudlet environment colours stored in the map. If you
dislike one, override it by environment id:

```sh
python3 mudlet2cmud.py --env-color 20=696969    # indoor rooms dark gray instead of white
```

## What is converted

| Mudlet | CMUD |
|---|---|
| Area | Zone (or several, with `--zones userdata`) |
| Room id | Room number, kept as both `ObjID` and `RefNum`, so room numbers match between the two clients |
| Room name, `userData.description` | Room name and description |
| Coordinates | Scaled to CMUD's 240-unit grid, Y flipped (Mudlet's north is +y, CMUD's is -y), z divided by the z step |
| Environment colour | Room colour |
| Room symbol (e.g. `P`, `W`) | Room short name (`IDName`) |
| Room weight > 1 | Room cost |
| Exits | One row per direction, the two halves of a two-way link paired with each other; one-way exits stay one-way |
| Special exits | Exit with direction "other" and the command as its name |
| Door state `open` / `closed` / `locked` | Exit type *Door* / *Door* / *Locked Door* |
| Door name in `userData` (`n`, `e`, `s`, `w`, `u`, `d`, ... or the full direction name) | The exit's door name; an exit with a door name counts as a door even without a door state |
| Any other room `userData` (keys, look texts, door names with no matching exit) | Room notes |
| Text labels | Map labels |

Not converted: stub exits (CMUD has no equivalent), image-only labels, custom
exit lines, and area-level `userData`.

## The map in this repo

`wotmud.dbm` was built with:

```sh
python3 mudlet2cmud.py --mark-zones --boundary-color ff6600 -f -o wotmud.dbm \
  --env-color 20=696969 --env-color 22=d2b48c --env-color 30=ffa500
```

That is the latest WoTMUD map, one zone per area, zone names and orange zone
boundaries, and three terrain colours set back to the ones from an older
revision of the map (dark gray indoor rooms, tan roads, orange instead of gold).
Re-run the same command to refresh it when the source map changes.

## Notes on the CMUD map format

A `.dbm` is a plain SQLite 3 database. Nothing about it is documented, so this
is what was worked out from maps written by CMUD 3.34, and what the script
relies on:

* `ObjectTbl` holds rooms; `X`/`Y` are the room centre, rooms are 120 units
  wide and sit 240 apart.
* `ExitTbl` holds one row per exit direction. `ExitIDTo` points at the row for
  the way back (`-1` for a one-way exit) and both rows must agree with each
  other. `DirType` is 0-9 for n, ne, e, se, s, sw, w, nw, up, down and 11 for
  "other".
* An exit is drawn in the colour of its exit type (`ExitKindTbl.Color`). The
  per-exit `ExitTbl.Color` column is ignored when drawing.
* Booleans are the strings `'Y'`/`'N'`, colours are Delphi `BGR` integers with
  `0x1FFFFFFF` meaning "default", and `VersTbl` holds the next free id for each
  table.
* `ZoneTbl.X/Y/Z` is the last viewed position; CMUD rewrites it as you use the
  map.

Known issue, unrelated to the converter: under Wine, choosing a zone from the
mapper's zone drop-down can send CMUD into a redraw loop. It happens with maps
made by CMUD itself too, and does not happen on Windows. Walking between zones
works everywhere.

## Credits

The WoTMUD Mudlet map is maintained at
[weisluke/WoTMUD](https://github.com/weisluke/WoTMUD); this project only
converts it.
