#!/usr/bin/env python3
"""Convert a Mudlet JSON map export into a CMUD 3 mapper database (.dbm).

A .dbm file is a plain SQLite 3 database. The schema and the conventions used
here were taken from a map written by CMUD itself:

  * ObjectTbl holds rooms. X/Y are the room centre, 240 units between
    neighbouring rooms (DefSize 120 = room size), Y grows to the south.
  * ExitTbl holds one row per exit *direction*. The two halves of a two-way
    link point at each other through ExitIDTo (-1 for one-way exits).
    DirType is 0-based n,ne,e,se,s,sw,w,nw,u,d and 11 for "other".
  * Booleans are the strings 'Y'/'N', colours are Delphi BGR integers with
    0x1FFFFFFF meaning "default", VersTbl holds the next free id per table.

Only the Python standard library is used.
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import reduce
from math import gcd

# the community WoTMUD Mudlet map, used when no input is given
DEFAULT_SOURCE = "https://github.com/weisluke/WoTMUD/blob/main/WoTMUD_map.json"

CL_DEFAULT = 0x1FFFFFFF  # Delphi clDefault
GRID = 240               # CMUD units between adjacent rooms
ROOM_SIZE = 120
DIR_OTHER = 11
ZONE_STYLE = 1           # StyleTbl row used for --mark-zones labels
ZONE_FONT_SIZE = 14

SCHEMA = """
CREATE TABLE DirTbl ([DirID] INTEGER PRIMARY KEY,[DirName] VARCHAR(80),[DirRef] INTEGER DEFAULT 0,[RevId] INTEGER DEFAULT 0,[Dx] INTEGER DEFAULT 0,[Dy] INTEGER DEFAULT 0,[Dz] INTEGER DEFAULT 0);
CREATE TABLE DrawTbl ([DrawID] INTEGER PRIMARY KEY,[X] INTEGER DEFAULT 0,[Y] INTEGER DEFAULT 0,[Z] INTEGER DEFAULT 0,[Dx] INTEGER DEFAULT 0,[Dy] INTEGER DEFAULT 0,[Name] VARCHAR(255),[MetaID] INTEGER,[ZoneID] INTEGER,[Flags] INTEGER DEFAULT 0,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[StyleID] INTEGER,[Hint] VARCHAR(255));
CREATE INDEX DrawM ON DrawTbl ([MetaID]);
CREATE INDEX DrawZ ON DrawTbl ([ZoneID]);
CREATE TABLE ExitKindTbl ([ExitKindID] INTEGER PRIMARY KEY,[Name] VARCHAR(80),[Desc] VARCHAR(255),[Script] TEXT,[Style] INTEGER DEFAULT 0,[Color] INTEGER DEFAULT 536870911,[MetaID] INTEGER,[UserID] INTEGER DEFAULT 0,[DrawDef] BOOLEAN DEFAULT TRUE,[Modified] TIMESTAMP,[InnerWidth] INTEGER DEFAULT 0,[InnerColor] INTEGER DEFAULT 536870911,[ParentID] INTEGER,[Flags] INTEGER DEFAULT 0,[FillColor] INTEGER DEFAULT 536870911,[Dx] INTEGER DEFAULT 0,[IconID] INTEGER,[DoorColor] INTEGER DEFAULT 536870911);
CREATE INDEX EKParent ON ExitKindTbl ([ParentID]);
CREATE TABLE ExitTbl ([ExitID] INTEGER PRIMARY KEY,[FromID] INTEGER,[ToID] INTEGER,[ExitKindID] INTEGER,[Name] VARCHAR(80),[Param] VARCHAR(80),[Label] VARCHAR(80),[X0] INTEGER DEFAULT 0,[Y0] INTEGER DEFAULT 0,[Z0] INTEGER DEFAULT 0,[X1] INTEGER DEFAULT 0,[Y1] INTEGER DEFAULT 0,[Z1] INTEGER DEFAULT 0,[Distance] INTEGER DEFAULT 0,[Script] TEXT,[Color] INTEGER DEFAULT 536870911,[MetaID] INTEGER,[DrawRev] BOOLEAN DEFAULT FALSE,[DirType] INTEGER DEFAULT 0,[DirToType] INTEGER DEFAULT 0,[Tested] BOOLEAN DEFAULT TRUE,[Flags] INTEGER DEFAULT 0,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[ExitIDTo] INTEGER);
CREATE INDEX DirExit ON ExitTbl ([DirType]);
CREATE INDEX DirToExit ON ExitTbl ([DirToType]);
CREATE INDEX ExitFrom ON ExitTbl ([FromID]);
CREATE INDEX ExitKind ON ExitTbl ([ExitKindID]);
CREATE INDEX ExitTo ON ExitTbl ([ToID]);
CREATE INDEX ExitToExit ON ExitTbl ([ExitIDTo]);
CREATE INDEX IMeta ON ExitTbl ([MetaID]);
CREATE TABLE FavTbl ([FavID] INTEGER PRIMARY KEY,[Name] VARCHAR(80),[KindID] INTEGER,[ObjID] INTEGER,[ParentID] INTEGER);
CREATE INDEX FavObjID ON FavTbl ([ObjID]);
CREATE INDEX FName ON FavTbl ([Name]);
CREATE INDEX FPar ON FavTbl ([ParentID]);
CREATE INDEX FParent ON FavTbl ([ParentID]);
CREATE TABLE IconTbl ([IconID] INTEGER PRIMARY KEY,[Bitmap] BLOB,[UID1] INTEGER DEFAULT 0,[UID2] INTEGER DEFAULT 0,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[Filename] VARCHAR(255));
CREATE TABLE KindTbl ([KindID] INTEGER PRIMARY KEY,[Name] VARCHAR(80),[Desc] VARCHAR(255),[IconID] INTEGER,[Color] INTEGER DEFAULT 536870911,[MetaID] INTEGER,[Dx] INTEGER DEFAULT 0,[Dy] INTEGER DEFAULT 0,[Ref] INTEGER DEFAULT 0,[Script] TEXT,[UserID] INTEGER DEFAULT 0,[DrawDef] BOOLEAN DEFAULT TRUE,[Modified] TIMESTAMP,[ParentID] INTEGER,[Flags] INTEGER DEFAULT 0,[StyleID] INTEGER DEFAULT 0,[Pen] INTEGER DEFAULT 0);
CREATE INDEX KindFlag ON KindTbl ([Flags]);
CREATE INDEX KParent ON KindTbl ([ParentID]);
CREATE TABLE MetaTbl ([MetaID] INTEGER PRIMARY KEY,[X] INTEGER DEFAULT 0,[Y] INTEGER DEFAULT 0,[Z] INTEGER DEFAULT 0,[Dx] INTEGER DEFAULT 0,[Dy] INTEGER DEFAULT 0,[Label] VARCHAR(255),[DrawData] BLOB,[HasData] BOOLEAN DEFAULT FALSE,[Ref] INTEGER DEFAULT 0,[UID1] INTEGER DEFAULT 0,[UID2] INTEGER DEFAULT 0,[IconID] INTEGER,[ZoneID] INTEGER,[Color] INTEGER DEFAULT 536870911,[Flags] INTEGER DEFAULT 0,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[ParentID] INTEGER);
CREATE INDEX MU1 ON MetaTbl ([UID1]);
CREATE INDEX MU2 ON MetaTbl ([UID2]);
CREATE INDEX MZone ON MetaTbl ([ZoneID]);
CREATE TABLE NoteTbl ([NoteID] INTEGER PRIMARY KEY,[ObjID] INTEGER,[Note] TEXT,[Category] INTEGER DEFAULT 0,[Flags] INTEGER DEFAULT 0,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[Deleted] BOOLEAN DEFAULT FALSE);
CREATE INDEX NoteCat ON NoteTbl ([Category]);
CREATE INDEX NoteObjID ON NoteTbl ([ObjID]);
CREATE TABLE ObjectTbl ([ObjID] INTEGER PRIMARY KEY,[Name] VARCHAR(255),[IDName] VARCHAR(255),[Hint] VARCHAR(255),[Desc] TEXT,[KindID] INTEGER,[IconID] INTEGER,[RefNum] INTEGER DEFAULT 0,[fKey] INTEGER DEFAULT 0,[X] INTEGER DEFAULT 0,[Y] INTEGER DEFAULT 0,[Z] INTEGER DEFAULT 0,[Dx] INTEGER DEFAULT 0,[Dy] INTEGER DEFAULT 0,[ExitX] INTEGER DEFAULT 0,[ExitY] INTEGER DEFAULT 0,[ExitZ] INTEGER DEFAULT 0,[Cost] INTEGER DEFAULT 0,[Color] INTEGER DEFAULT 536870911,[MetaID] INTEGER,[LabelDir] INTEGER DEFAULT 11,[Enabled] BOOLEAN DEFAULT TRUE,[Script] TEXT,[Param] VARCHAR(80),[UserStr] VARCHAR(255),[UserInt] INTEGER DEFAULT 0,[Content] TEXT,[Flags] INTEGER DEFAULT 0,[Deleted] BOOLEAN DEFAULT FALSE,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[ZoneID] INTEGER,[StyleID] INTEGER,[DateAdded] TIMESTAMP,[ServerId] INTEGER);
CREATE INDEX DObj ON ObjectTbl ([Deleted]);
CREATE INDEX ObjfKey ON ObjectTbl ([fKey]);
CREATE INDEX ObjKindID ON ObjectTbl ([KindID]);
CREATE INDEX ObjRef ON ObjectTbl ([RefNum]);
CREATE INDEX ObjZoneID ON ObjectTbl ([ZoneID]);
CREATE INDEX OID ON ObjectTbl ([IDName]);
CREATE INDEX OName ON ObjectTbl ([Name]);
CREATE INDEX OX ON ObjectTbl ([X]);
CREATE INDEX OY ON ObjectTbl ([Y]);
CREATE INDEX OZ ON ObjectTbl ([Z]);
CREATE TABLE PortalTbl ([PortalId] INTEGER PRIMARY KEY,[ToID] INTEGER DEFAULT 0,[Cost] INTEGER DEFAULT 0,[ZoneID] INTEGER,[Name] VARCHAR(255),[Flags] INTEGER DEFAULT 0,[Enable] BOOLEAN DEFAULT TRUE,[Modified] TIMESTAMP);
CREATE INDEX PortalN ON PortalTbl ([Name]);
CREATE TABLE StyleTbl ([StyleID] INTEGER PRIMARY KEY,[Name] VARCHAR(80),[ParentID] INTEGER,[FontName] VARCHAR(80),[FontSize] INTEGER DEFAULT 10,[FontStyle] INTEGER DEFAULT 0,[Color] INTEGER DEFAULT 536870911,[Color2] INTEGER DEFAULT 536870911,[Flags] INTEGER DEFAULT 0,[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP);
CREATE TABLE VersTbl ([VersId] INTEGER PRIMARY KEY,[VersNum] INTEGER DEFAULT 0,[IconID] INTEGER DEFAULT 1,[MetaID] INTEGER DEFAULT 1,[DrawID] INTEGER DEFAULT 1,[KindID] INTEGER DEFAULT 1,[FavID] INTEGER DEFAULT 1,[ZoneID] INTEGER DEFAULT 1,[ObjID] INTEGER DEFAULT 1,[ExitKindID] INTEGER DEFAULT 1,[NoteID] INTEGER DEFAULT 1,[StyleID] INTEGER DEFAULT 1,[ExitID] INTEGER DEFAULT 1,[DirID] INTEGER DEFAULT 1,[PortalID] INTEGER DEFAULT 1);
CREATE TABLE ZoneTbl ([ZoneID] INTEGER PRIMARY KEY,[Name] VARCHAR(80),[ZoneFile] VARCHAR(80),[UserID] INTEGER DEFAULT 0,[Modified] TIMESTAMP,[Script] TEXT,[Desc] TEXT,[X] INTEGER DEFAULT 0,[Y] INTEGER DEFAULT 0,[Z] INTEGER DEFAULT 0,[Dx] INTEGER DEFAULT 0,[Dy] INTEGER DEFAULT 0,[Background] VARCHAR(80),[XScale] INTEGER DEFAULT 0,[YScale] INTEGER DEFAULT 0,[XOffset] INTEGER DEFAULT 0,[YOffset] INTEGER DEFAULT 0,[Divisor] INTEGER DEFAULT 1,[Multiplier] INTEGER DEFAULT 1,[DefSize] INTEGER DEFAULT 0,[DefSizeY] INTEGER DEFAULT 0,[Res] INTEGER DEFAULT 0,[Color] INTEGER DEFAULT 536870911,[Parent] INTEGER,[MinX] INTEGER DEFAULT 0,[MinY] INTEGER DEFAULT 0,[MinZ] INTEGER DEFAULT 0,[MaxX] INTEGER DEFAULT 0,[MaxY] INTEGER DEFAULT 0,[MaxZ] INTEGER DEFAULT 0,[GridXInc] INTEGER DEFAULT 120,[GridYInc] INTEGER DEFAULT 120,[GridXOff] INTEGER DEFAULT 0,[GridYOff] INTEGER DEFAULT 0,[GridCol] INTEGER DEFAULT 0,[Flags] INTEGER DEFAULT 0);
CREATE INDEX DrawZone ON ZoneTbl ([ZoneID]);
CREATE INDEX ZName ON ZoneTbl ([Name]);
CREATE INDEX ZPar ON ZoneTbl ([Parent]);
CREATE INDEX ZParent ON ZoneTbl ([Parent]);
"""

# DirID, DirName, DirRef, RevId, Dx, Dy, Dz  (DirID is 1-based, ExitTbl.DirType is DirID - 1)
DIR_ROWS = [
    (1, "north", 110, 5, 0, -200, 0),
    (2, "ne", 106, 6, 200, -200, 0),
    (3, "east", 101, 7, 200, 0, 0),
    (4, "se", 108, 8, 200, 200, 0),
    (5, "south", 115, 1, 0, 200, 0),
    (6, "sw", 107, 2, -200, 200, 0),
    (7, "west", 119, 3, -200, 0, 0),
    (8, "nw", 104, 4, -200, -200, 0),
    (9, "up", 117, 10, 0, 0, 100),
    (10, "down", 100, 9, 0, 0, -100),
]

# ExitKindID, Name, Flags, FillColor
EXIT_KINDS = [(0, "Normal Exit", 0, CL_DEFAULT), (1, "Door", 1, 0xFFFFFF), (2, "Locked Door", 3, 0)]
KIND_NORMAL, KIND_DOOR, KIND_LOCKED = 0, 1, 2
# --mark-zones adds a coloured twin of each kind at this id offset. CMUD ignores
# ExitTbl.Color when drawing; an exit takes its colour from its kind.
KIND_BOUNDARY = len(EXIT_KINDS)

# Mudlet exit name -> (DirType, userData keys that may hold the door name)
DIRS = {
    "north": (0, "n"), "northeast": (1, "ne"), "east": (2, "e"), "southeast": (3, "se"),
    "south": (4, "s"), "southwest": (5, "sw"), "west": (6, "w"), "northwest": (7, "nw"),
    "up": (8, "u"), "down": (9, "d"),
}
DOOR_KEYS = {k for name, (_, short) in DIRS.items() for k in (name, short)}
REVERSE = {0: 4, 1: 5, 2: 6, 3: 7, 4: 0, 5: 1, 6: 2, 7: 3, 8: 9, 9: 8, DIR_OTHER: DIR_OTHER}
# unit step in Mudlet space (y grows north) for the flat directions
FLAT_VEC = {0: (0, 1), 1: (1, 1), 2: (1, 0), 3: (1, -1), 4: (0, -1), 5: (-1, -1), 6: (-1, 0), 7: (-1, 1)}

# Mudlet's built-in environment colours 1-16 (also exported as 257-272)
ANSI = [(128, 0, 0), (0, 128, 0), (128, 128, 0), (0, 0, 128), (128, 0, 128), (0, 128, 128),
        (192, 192, 192), (0, 0, 0), (255, 0, 0), (0, 255, 0), (255, 255, 0), (0, 0, 255),
        (255, 0, 255), (0, 255, 255), (255, 255, 255), (128, 128, 128)]


def bgr(rgb):
    r, g, b = rgb[:3]
    return (b << 16) | (g << 8) | r


def crlf(text):
    return (text or "").replace("\r\n", "\n").replace("\n", "\r\n")


def detect_scale(areas):
    """CMUD units per Mudlet unit, chosen so the usual room spacing becomes GRID."""
    coords = {r["id"]: (a["id"], r["coordinates"]) for a in areas for r in a["rooms"]}
    steps = Counter()
    for a in areas:
        for r in a["rooms"]:
            for e in r.get("exits", []):
                d = DIRS.get(e["name"], (None,))[0]
                tgt = coords.get(e["exitId"])
                if d not in FLAT_VEC or not tgt or tgt[0] != a["id"]:
                    continue
                vx, vy = FLAT_VEC[d]
                dx, dy = tgt[1][0] - r["coordinates"][0], tgt[1][1] - r["coordinates"][1]
                along = dx * vx + dy * vy if 0 in (vx, vy) else (dx * vx + dy * vy) / 2
                if along > 0:
                    steps[along] += 1
    return GRID / steps.most_common(1)[0][0] if steps else GRID


def detect_zstep(areas):
    zs = {abs(int(r["coordinates"][2])) for a in areas for r in a["rooms"]} - {0}
    return reduce(gcd, zs) if zs else 1


def build_zones(areas, mode, zone_key):
    """Return [(name, parent_index_or_None, rooms, labels)]."""
    zones = []
    for a in areas:
        rooms, labels = a["rooms"], a.get("labels", [])
        if not rooms and not labels:
            continue
        if mode == "area":
            zones.append((a["name"], None, rooms, labels))
            continue
        groups = defaultdict(list)
        for r in rooms:
            groups[(r.get("userData", {}).get(zone_key) or "").strip()].append(r)
        if len(groups) <= 1:
            zones.append((a["name"], None, rooms, labels))
            continue
        # labels follow the room they sit closest to, preferring the same level
        group_labels = defaultdict(list)
        for lb in labels:
            lx, ly, lz = lb["coordinates"]
            lx, ly = lx + lb["size"][0] / 2, ly - lb["size"][1] / 2
            _, name = min(((r["coordinates"][2] != lz, (r["coordinates"][0] - lx) ** 2 + (r["coordinates"][1] - ly) ** 2), n)
                          for n, rs in groups.items() for r in rs)
            group_labels[name].append(lb)
        # the area itself holds the unzoned rooms and parents the rest
        parent = len(zones)
        zones.append((a["name"], None, groups.pop("", []), group_labels[""]))
        for name in sorted(groups):
            zones.append((name, parent, groups[name], group_labels[name]))
    # CMUD picks zones by name, so keep names unique
    seen = Counter(z[0] for z in zones)
    out = []
    for name, parent, rooms, labels in zones:
        if seen[name] > 1 and parent is not None:
            name = "%s (%s)" % (name, zones[parent][0])
        out.append((name[:80], parent, rooms, labels))
    return out


def convert(data, out_path, mode="area", zone_key="zone", scale=None, zstep=None,
            mark_zones=False, boundary_color=0x0000FF, env_overrides=None):
    areas = data["areas"]
    scale = scale or detect_scale(areas)
    zstep = zstep or detect_zstep(areas)
    # SOURCE_DATE_EPOCH pins the "Modified" stamps so the same input gives the same file
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    now = (datetime.fromtimestamp(int(epoch), timezone.utc) if epoch else datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    stats = Counter()

    env_colors = {i + 1: bgr(c) for i, c in enumerate(ANSI)}
    env_colors.update({i + 257: bgr(c) for i, c in enumerate(ANSI)})
    env_colors.update({c["id"]: bgr(c["color24RGB"]) for c in data.get("customEnvColors", [])})
    env_colors.update(env_overrides or {})

    def pos(c):
        return round(c[0] * scale), round(-c[1] * scale), round(c[2] / zstep)

    # Mudlet ids are kept as ObjID so scripts and notes keyed on them still work
    all_ids = [r["id"] for a in areas for r in a["rooms"]]
    next_obj = max(all_ids, default=0) + 1
    obj_id = {}
    for rid in all_ids:
        if rid > 0:
            obj_id[rid] = rid
        else:
            obj_id[rid] = next_obj
            next_obj += 1

    zones = build_zones(areas, mode, zone_key)
    zone_rows, obj_rows, note_rows, draw_rows, exits = [], [], [], [], []
    sub_zone = {}  # ObjID -> userData zone name, used to find zone boundaries

    for zi, (zname, parent, rooms, labels) in enumerate(zones):
        zone_id = zi + 1
        xs, ys, zs = [], [], []
        for r in rooms:
            ud = dict(r.get("userData", {}))
            x, y, z = pos(r["coordinates"])
            xs += [x - ROOM_SIZE, x + ROOM_SIZE]
            ys += [y - ROOM_SIZE, y + ROOM_SIZE]
            zs.append(z)
            oid = obj_id[r["id"]]
            desc = ud.pop("description", "")
            sub_zone[oid] = (ud.pop(zone_key, None) or "").strip()
            symbol = r.get("symbol")
            if isinstance(symbol, dict):
                symbol = symbol.get("text")
            weight = r.get("weight", 1)
            obj_rows.append((oid, (r.get("name") or "")[:255], (symbol or "")[:255], crlf(desc),
                             r["id"], x, y, z, weight if weight > 1 else 0,
                             env_colors.get(r.get("environment"), CL_DEFAULT), zone_id))

            for e in r.get("exits", []):
                if e["exitId"] not in obj_id:
                    stats["exits to missing rooms (skipped)"] += 1
                    continue
                d, door_key = DIRS.get(e["name"], (DIR_OTHER, None))
                door_name = ""
                for k in (door_key, e["name"]):
                    if k in ud:
                        door_name = str(ud.pop(k))
                        break
                door = e.get("door")
                kind = KIND_LOCKED if door == "locked" else KIND_DOOR if door or door_name else KIND_NORMAL
                exits.append({"from": oid, "to": obj_id[e["exitId"]], "dir": d, "kind": kind,
                              "name": e["name"][:80] if d == DIR_OTHER else "",
                              "param": door_name[:80], "pair": None})
            stats["stub exits (not representable, skipped)"] += len(r.get("stubExits", []))

            # whatever userData is left (keys, look texts, doors without an exit) becomes notes
            for k, v in sorted(ud.items()):
                label = "door (%s)" % k if k in DOOR_KEYS else k
                note_rows.append((len(note_rows) + 1, oid, crlf("%s: %s" % (label, v))))

        for lb in labels:
            if not lb.get("text"):
                stats["image-only labels (skipped)"] += 1
                continue
            x, y, z = pos(lb["coordinates"])
            w, h = (round(v * scale) for v in lb["size"])
            xs += [x, x + w]
            ys += [y, y + h]
            draw_rows.append((len(draw_rows) + 1, x, y, z, w, h, lb["text"][:255], zone_id, 0))

        # name each userData zone in the middle of its rooms, on its busiest level
        if mark_zones and mode == "area":
            members = defaultdict(list)
            for r in rooms:
                members[sub_zone[obj_id[r["id"]]]].append(pos(r["coordinates"]))
            if len(members) > 1:
                for name, pts in sorted(members.items()):
                    if not name:
                        continue
                    z = Counter(p[2] for p in pts).most_common(1)[0][0]
                    level = [p for p in pts if p[2] == z]
                    # label box sized like CMUD sizes its own: ~7 x 21 units per point per character
                    w, h = round(len(name) * ZONE_FONT_SIZE * 7.8), ZONE_FONT_SIZE * 21
                    x = round(sum(p[0] for p in level) / len(level) - w / 2)
                    y = round(sum(p[1] for p in level) / len(level) - h / 2)
                    xs += [x, x + w]
                    ys += [y, y + h]
                    draw_rows.append((len(draw_rows) + 1, x, y, z, w, h, name[:255], zone_id, ZONE_STYLE))
                    stats["zone name labels"] += 1

        min_x, max_x = (min(xs), max(xs)) if xs else (0, 0)
        min_y, max_y = (min(ys), max(ys)) if ys else (0, 0)
        home = pos(rooms[0]["coordinates"]) if rooms else (0, 0, 0)
        zone_rows.append((zone_id, zname, now, home[0], home[1], home[2],
                          min_x - 720, min_y - 360, -1 if parent is None else parent + 1,
                          min_x, min_y, min(zs, default=0), max_x, max_y, max(zs, default=0)))

    # Pair the two halves of each link: exact opposites first, then anything
    # left over between the same two rooms (e.g. north out, east back).
    by_ends = defaultdict(list)
    for i, e in enumerate(exits):
        e["id"] = i + 1
        by_ends[(e["from"], e["to"])].append(e)
    for exact in (True, False):
        for e in exits:
            if e["pair"]:
                continue
            for f in by_ends.get((e["to"], e["from"]), ()):
                if f is not e and not f["pair"] and (not exact or f["dir"] == REVERSE[e["dir"]]):
                    e["pair"], f["pair"] = f, e
                    break
    exit_rows = []
    for e in exits:
        p = e["pair"]
        stats["one-way exits"] += p is None
        kind = e["kind"]
        if mark_zones and sub_zone[e["from"]] != sub_zone[e["to"]]:
            kind += KIND_BOUNDARY
            stats["zone boundary exits (coloured)"] += 1
        exit_rows.append((e["id"], e["from"], e["to"], kind, e["name"], e["param"],
                          e["dir"], p["dir"] if p else REVERSE[e["dir"]], now, p["id"] if p else -1))

    db = sqlite3.connect(out_path)
    db.execute("PRAGMA page_size = 8192")
    db.execute("PRAGMA encoding = 'UTF-8'")
    db.executescript(SCHEMA)
    with db:
        db.executemany("INSERT INTO DirTbl VALUES (?,?,?,?,?,?,?)", DIR_ROWS)
        kinds = [(i, name, CL_DEFAULT, flags, fill) for i, name, flags, fill in EXIT_KINDS]
        if mark_zones:
            kinds += [(i + KIND_BOUNDARY, "Zone Boundary " + name, boundary_color, flags, fill)
                      for i, name, flags, fill in EXIT_KINDS]
        db.executemany(
            "INSERT INTO ExitKindTbl VALUES (?,?,'','',0,?,-1,0,'Y',0,-1,%d,-1,?,?,0,-1,%d)"
            % (CL_DEFAULT, CL_DEFAULT), kinds)
        db.execute("INSERT INTO KindTbl VALUES (0,'Room','',-1,?,-1,0,0,0,'',0,'Y',0,-1,0,0,0)", (CL_DEFAULT,))
        db.execute("INSERT INTO StyleTbl VALUES (0,'Default',-1,'Arial',10,0,?,?,0,0,0)", (CL_DEFAULT, CL_DEFAULT))
        if mark_zones:
            # FontStyle is a Delphi TFontStyles set, 1 = bold
            db.execute("INSERT INTO StyleTbl VALUES (?,'Zone Name',-1,'Arial',?,1,?,?,0,0,0)",
                       (ZONE_STYLE, ZONE_FONT_SIZE, CL_DEFAULT, CL_DEFAULT))
        db.executemany(
            "INSERT INTO ZoneTbl (ZoneID,Name,ZoneFile,UserID,Modified,Script,[Desc],X,Y,Z,Dx,Dy,Background,"
            "XScale,YScale,XOffset,YOffset,Divisor,Multiplier,DefSize,DefSizeY,Res,Color,Parent,"
            "MinX,MinY,MinZ,MaxX,MaxY,MaxZ,GridXInc,GridYInc,GridXOff,GridYOff,GridCol,Flags) "
            "VALUES (?,?,'',0,?,'','',?,?,?,600,0,'',1,1,?,?,1,100,%d,%d,0,%d,?,?,?,?,?,?,?,120,120,0,0,0,0)"
            % (ROOM_SIZE, ROOM_SIZE, CL_DEFAULT), zone_rows)
        db.executemany(
            "INSERT INTO ObjectTbl (ObjID,Name,IDName,Hint,[Desc],KindID,IconID,RefNum,fKey,X,Y,Z,Dx,Dy,"
            "ExitX,ExitY,ExitZ,Cost,Color,MetaID,LabelDir,Enabled,Script,Param,UserStr,UserInt,Content,"
            "Flags,Deleted,UserID,Modified,ZoneID,StyleID,DateAdded,ServerId) "
            "VALUES (?,?,?,'',?,0,-1,?,0,?,?,?,0,0,0,0,0,?,?,-1,11,'Y','','','',0,'',0,'N',0,0,?,-1,0,0)",
            obj_rows)
        db.executemany(
            "INSERT INTO ExitTbl (ExitID,FromID,ToID,ExitKindID,Name,Param,Label,X0,Y0,Z0,X1,Y1,Z1,Distance,"
            "Script,Color,MetaID,DrawRev,DirType,DirToType,Tested,Flags,UserID,Modified,ExitIDTo) "
            "VALUES (?,?,?,?,?,?,'',0,0,0,0,0,0,0,'',%d,-1,'N',?,?,'Y',1,0,?,?)" % CL_DEFAULT, exit_rows)
        db.executemany("INSERT INTO NoteTbl VALUES (?,?,?,0,0,0,0,'N')", note_rows)
        db.executemany("INSERT INTO DrawTbl VALUES (?,?,?,?,?,?,?,-1,?,0,0,0,?,'')", draw_rows)
        db.execute(
            "INSERT INTO VersTbl (VersId,VersNum,IconID,MetaID,DrawID,KindID,FavID,ZoneID,ObjID,ExitKindID,"
            "NoteID,StyleID,ExitID,DirID,PortalID) VALUES (1,42,1,1,?,1,1,?,?,?,?,?,?,11,1)",
            (len(draw_rows) + 1, len(zone_rows) + 1, next_obj, len(kinds), len(note_rows) + 1,
             ZONE_STYLE + 1 if mark_zones else 1, len(exit_rows) + 1))
    db.close()

    stats.update({"zones": len(zone_rows), "rooms": len(obj_rows), "exits": len(exit_rows),
                  "doors": sum(e["kind"] != KIND_NORMAL for e in exits),
                  "notes": len(note_rows), "text labels": len(draw_rows) - stats["zone name labels"]})
    return scale, zstep, stats


def parse_color(text):
    raw = bytes.fromhex(text.lstrip("#"))
    if len(raw) != 3:
        raise ValueError(text)
    return bgr(raw)


def resolve_source(source):
    """Return (file path or URL to read, default output name) for a file or http(s) source."""
    url = urllib.parse.urlsplit(source)
    if url.scheme not in ("http", "https"):
        return source, os.path.splitext(source)[0] + ".dbm"
    parts = url.path.split("/")
    # a GitHub page link (/user/repo/blob/branch/path) serves HTML, fetch the raw file instead
    if url.netloc == "github.com" and len(parts) > 4 and parts[3] in ("blob", "raw"):
        source = "https://raw.githubusercontent.com" + "/".join(parts[:3] + parts[4:])
    return source, (os.path.splitext(urllib.parse.unquote(parts[-1]))[0] or "map") + ".dbm"


def load_map(source):
    if "://" not in source:
        with open(source, encoding="utf-8") as fh:
            return json.load(fh)
    print("downloading %s" % source)
    with urllib.request.urlopen(source, timeout=60) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input", nargs="?", default=DEFAULT_SOURCE,
                    help="Mudlet JSON map export, file or URL (default: latest %s)" % DEFAULT_SOURCE)
    ap.add_argument("-o", "--output", help="CMUD map to write (default: input name with .dbm)")
    ap.add_argument("-f", "--force", action="store_true", help="overwrite the output file if it exists")
    ap.add_argument("--zones", choices=("area", "userdata"), default="area",
                    help="one CMUD zone per Mudlet area (default), or split each area by a room userData value")
    ap.add_argument("--zone-key", default="zone", help="userData key used by --zones userdata (default: zone)")
    ap.add_argument("--mark-zones", action="store_true",
                    help="show the --zone-key zones on the map: name labels (area mode) and coloured boundary exits")
    ap.add_argument("--boundary-color", metavar="RRGGBB",
                    help="exit colour used by --mark-zones (default: ff0000)")
    ap.add_argument("--env-color", action="append", default=[], metavar="ID=RRGGBB",
                    help="room colour for a Mudlet environment id, overriding the map's own (repeatable)")
    ap.add_argument("--scale", type=float, help="CMUD units per Mudlet unit (default: auto, usual spacing -> 240)")
    ap.add_argument("--z-step", type=int, help="Mudlet z units per CMUD level (default: auto)")
    args = ap.parse_args()

    if args.boundary_color and not args.mark_zones:
        ap.error("--boundary-color only has an effect together with --mark-zones")
    try:
        boundary = parse_color(args.boundary_color or "ff0000")
    except ValueError:
        ap.error("--boundary-color must be RRGGBB hex")
    env_overrides = {}
    for item in args.env_color:
        try:
            env_id, color = item.split("=")
            env_overrides[int(env_id)] = parse_color(color)
        except ValueError:
            ap.error("--env-color must look like 20=696969, got %r" % item)
    source, default_out = resolve_source(args.input)
    out = args.output or default_out
    if os.path.exists(out) and not args.force:
        sys.exit("%s already exists, use --force to overwrite" % out)
    try:
        data = load_map(source)
    except (OSError, ValueError) as err:
        sys.exit("cannot read %s: %s" % (source, err))
    if os.path.exists(out):
        os.remove(out)
    scale, zstep, stats = convert(data, out, args.zones, args.zone_key, args.scale, args.z_step,
                                  args.mark_zones, boundary, env_overrides)

    print("wrote %s  (scale x%g, z step %d)" % (out, scale, zstep))
    for k, v in stats.items():
        print("  %-42s %d" % (k, v))


if __name__ == "__main__":
    main()
