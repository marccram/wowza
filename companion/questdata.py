"""Game data from QuestieDB (github.com/Questie/QuestieDB): quests, NPCs, objects, items and drop
rates for WoW Forever. Each player's companion downloads it once, after asking, into
%APPDATA%\\WoWZA\\questiedb; it is not bundled with the app.

For each question we find the quests, items, NPCs and objects it names and hand Claude the facts:
who gives a quest and where, what drops an item and how often, and where things spawn.
"""
import math
import pickle
import re
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from settings import APP_DIR

DATA_DIR = APP_DIR / "questiedb"
INDEX_FILE = DATA_DIR / "index.pickle"
INDEX_VERSION = 1
SOURCE = "https://raw.githubusercontent.com/Questie/QuestieDB/master/"
FILES = {
    "foreverQuestDB.lua": "data/Forever/foreverQuestDB.lua",
    "foreverNpcDB.lua": "data/Forever/foreverNpcDB.lua",
    "foreverObjectDB.lua": "data/Forever/foreverObjectDB.lua",
    "foreverItemDB.lua": "data/Forever/foreverItemDB.lua",
    "classicItemDrops.lua": "support/Forever/DropTables/classicItemDrops.lua",
    "zones.lua": "src/corrections/enum/zones.lua",
}
DOWNLOAD_MB = 6.9

MAX_ENTITIES = 6    # entities named in a question that we describe
MAX_SOURCES = 4     # drop sources per item
MAX_CHARS = 3500    # size of the block handed to Claude

# Words that are also names of something in the database but almost never mean it in a question.
COMMON = set("""
where what when which find farm get kill quest quests item items drop drops level levels best good
spider spiders wolf wolves boar boars bear bears murloc murlocs gnoll gnolls kobold kobolds bandit
bandits skeleton skeletons zombie undead water food armor weapon sword axe mace bow gun staff ring
cloth leather wool silk mail plate herb ore copper tin iron gold silver chest crate barrel box sack
book letter note map key head heart meat egg eggs fish feather feathers scale scales claw claws fang
fangs tusk tusks hide hides pelt pelts horn horns tooth teeth wing wings eye eyes blood bone bones
trainer vendor guard guards captain sergeant lieutenant warrior mage priest rogue hunter druid
paladin shaman warlock spec talent talents rotation macro help need want please thanks
""".split())


# What players call an NPC role -> the titles NPCs actually carry.
TITLE_ALIASES = {
    "flight master": ["flight master", "gryphon master", "hippogryph master", "wind rider master", "bat handler"],
    "flight path": ["flight master", "gryphon master", "hippogryph master", "wind rider master", "bat handler"],
    "bank": ["banker"],
    "auction house": ["auctioneer"],
    "stable master": ["stable master"],
}


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("'", "").replace("’", "")).split())


def _title(enum_name):
    small = {"of", "the", "and"}
    words = enum_name.lower().split("_")
    return " ".join(w if (i and w in small) else w.capitalize() for i, w in enumerate(words))


# --- download and index ---------------------------------------------------------------

def downloaded():
    return all((DATA_DIR / name).exists() for name in FILES)


def download(progress=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for i, (name, path) in enumerate(FILES.items(), 1):
        if progress:
            progress(f"Downloading {name} ({i}/{len(FILES)})")
        with urlopen(SOURCE + path, timeout=60) as resp:
            data = resp.read()
        tmp = DATA_DIR / (name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(DATA_DIR / name)
    INDEX_FILE.unlink(missing_ok=True)


def _lua_tables():
    """Run the QuestieDB files in an embedded Lua and return their raw tables."""
    from lupa.lua51 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute("""
        QuestieDB = {}
        QuestieLoader = { ImportModule = function() return QuestieDB end,
                          CreateModule = function(_, name) local m = {}; _G[name] = m; return m end }
    """)
    out = {}
    for name, var in (("foreverQuestDB.lua", "questData"), ("foreverNpcDB.lua", "npcData"),
                      ("foreverObjectDB.lua", "objectData"), ("foreverItemDB.lua", "itemData")):
        lua.execute((DATA_DIR / name).read_text(encoding="utf-8"))
        out[var] = lua.eval(f"assert(loadstring(QuestieDB.{var}))()")
    lua.execute((DATA_DIR / "classicItemDrops.lua").read_text(encoding="utf-8"))
    out["drops"] = lua.eval("assert(loadstring(QuestieClassicItemDrops.wowheadData))()")
    zones_chunk = lua.eval("function(src) local enum = {}; assert(loadstring(src))('x', { Enum = enum }); "
                           "return enum.zoneIDs end")
    out["zones"] = zones_chunk((DATA_DIR / "zones.lua").read_text(encoding="utf-8"))
    return lua, out


def _py(v):
    """Lua table -> list (array-like) or dict (sparse, e.g. a record with empty fields); others unchanged."""
    from lupa.lua51 import lua_type
    if lua_type(v) != "table":
        return v
    items = list(v.items())
    if items and all(isinstance(k, int) for k, _ in items) and \
            sorted(k for k, _ in items) == list(range(1, len(items) + 1)):
        return [_py(x) for _, x in sorted(items, key=lambda kv: kv[0])]
    return {k: _py(x) for k, x in items}


def _field(rec, i):
    """Field i (1-based, as in the QuestieDB key tables) of a record that may have empty fields."""
    if isinstance(rec, dict):
        return rec.get(i)
    if isinstance(rec, list):
        return rec[i - 1] if i - 1 < len(rec) else None
    return None


def _spawns(raw):
    """{zoneID: [(x, y), ...]} with only real map coordinates."""
    out = {}
    if isinstance(raw, dict):
        for zone, coords in raw.items():
            coords = coords.values() if isinstance(coords, dict) else (coords or [])
            pts = [(c[0], c[1]) for c in coords if isinstance(c, list) and len(c) >= 2
                   and isinstance(c[0], (int, float)) and c[0] >= 0 and c[1] >= 0]
            if pts:
                out[int(zone)] = pts
    return out


def _ids(v):
    if isinstance(v, dict):
        v = [v[k] for k in sorted(v)]
    return [int(x) for x in v if isinstance(x, (int, float))] if isinstance(v, list) else []


def build_index():
    _, raw = _lua_tables()
    quests, npcs, objects, items = {}, {}, {}, {}
    for qid, r in raw["questData"].items():
        r = _py(r)
        started, finished, obj = _field(r, 2), _field(r, 3), _field(r, 10)
        objectives = {"npc": [], "object": [], "item": []}
        for kind, i in (("npc", 1), ("object", 2), ("item", 3)):
            group = _field(obj, i) or []
            for o in (group.values() if isinstance(group, dict) else group):
                first = _field(o, 1)
                if isinstance(first, (int, float)):
                    objectives[kind].append(int(first))
        quests[int(qid)] = {
            "name": _field(r, 1), "starts": [_ids(_field(started, 1)), _ids(_field(started, 2)), _ids(_field(started, 3))],
            "ends": [_ids(_field(finished, 1)), _ids(_field(finished, 2))],
            "req": _field(r, 4), "level": _field(r, 5),
            "objectives": objectives, "zone": _field(r, 17), "next": _field(r, 22),
            "pre": _ids(_field(r, 13)) or _ids(_field(r, 12)), "sourceItems": _ids(_field(r, 21)),
        }
    for nid, r in raw["npcData"].items():
        r = _py(r)
        npcs[int(nid)] = {"name": _field(r, 1), "min": _field(r, 4), "max": _field(r, 5), "rank": _field(r, 6),
                          "spawns": _spawns(_field(r, 7)), "zone": _field(r, 9),
                          "qstarts": _ids(_field(r, 10)), "qends": _ids(_field(r, 11)),
                          "friendly": _field(r, 13), "sub": _field(r, 14)}
    for oid, r in raw["objectData"].items():
        r = _py(r)
        objects[int(oid)] = {"name": _field(r, 1), "qstarts": _ids(_field(r, 2)), "qends": _ids(_field(r, 3)),
                             "spawns": _spawns(_field(r, 4)), "zone": _field(r, 5)}
    for iid, r in raw["itemData"].items():
        r = _py(r)
        items[int(iid)] = {"name": _field(r, 1), "npcs": _ids(_field(r, 2)), "objects": _ids(_field(r, 3)),
                           "items": _ids(_field(r, 4)), "startQuest": _field(r, 5), "vendors": _ids(_field(r, 14)),
                           "level": _field(r, 9), "reqLevel": _field(r, 10)}
    drops = {int(i): {int(n): float(p) for n, p in _py(d).items()} for i, d in raw["drops"].items()}
    zones = {int(v): _title(k) for k, v in _py(raw["zones"]).items() if isinstance(v, (int, float))}
    index = {"version": INDEX_VERSION, "built": time.time(), "quests": quests, "npcs": npcs,
             "objects": objects, "items": items, "drops": drops, "zones": zones}
    with open(INDEX_FILE, "wb") as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)
    return index


# --- lookups --------------------------------------------------------------------------

class QuestData:
    def __init__(self):
        self.ready = False
        self.error = None
        self.db = None
        self._lock = threading.Lock()

    def load(self):
        """Load (building the index first if needed). Safe to call from a background thread."""
        with self._lock:
            if self.ready or not downloaded():
                return self.ready
            try:
                db = None
                if INDEX_FILE.exists():
                    with open(INDEX_FILE, "rb") as f:
                        db = pickle.load(f)
                    if db.get("version") != INDEX_VERSION:
                        db = None
                self.db = db or build_index()
                self._index_names()
                self.ready, self.error = True, None
            except Exception as e:  # missing lupa, corrupt download, format change
                self.error = str(e)
            return self.ready

    def _index_names(self):
        names = {}
        for kind in ("quests", "items", "npcs", "objects"):
            for eid, e in self.db[kind].items():
                if isinstance(e.get("name"), str):
                    names.setdefault(_norm(e["name"]), []).append((kind, eid))
        self.names = names
        titles = {}  # NPC titles like "First Aid Trainer", "Flight Master", "Innkeeper"
        for nid, n in self.db["npcs"].items():
            if isinstance(n.get("sub"), str) and n["spawns"]:
                titles.setdefault(_norm(n["sub"]), []).append(nid)
        self.titles = titles
        self.zone_ids = {_norm(name): zid for zid, name in self.db["zones"].items()}
        self.zone_terms = set(self.zone_ids) | {z.removesuffix(" city") for z in self.zone_ids}
        self.max_words = 7

    # -- formatting helpers --

    def zone_name(self, zid):
        return self.db["zones"].get(zid, f"zone {zid}")

    def _where(self, spawns, here):
        """'Redridge Mountains: 34 spots, mostly around 15,62 and 30,70; nearest you 18.0,55.3'"""
        parts = []
        zones = sorted(spawns.items(), key=lambda kv: (kv[0] != here.get("zone"), -len(kv[1])))
        for zid, pts in zones[:3]:
            text = f"[[zone:{self.zone_name(zid)}]] " + (f"at {pts[0][0]:.1f}, {pts[0][1]:.1f}" if len(pts) == 1
                                                          else f"{len(pts)} spots, mostly around " + self._clusters(pts))
            if zid == here.get("zone") and here.get("pos"):
                px, py = here["pos"]
                nx, ny = min(pts, key=lambda p: (p[0] - px) ** 2 + (p[1] - py) ** 2)
                dist = math.hypot(nx - px, ny - py)
                text += f"; nearest to the player {nx:.1f}, {ny:.1f} ({dist:.0f} map % away)"
            parts.append(text)
        return "; ".join(parts) if parts else "no known spawn points"

    @staticmethod
    def _clusters(pts, cell=10, top=3):
        buckets = {}
        for x, y in pts:
            buckets.setdefault((int(x // cell), int(y // cell)), []).append((x, y))
        best = sorted(buckets.values(), key=len, reverse=True)[:top]
        return ", ".join(f"({sum(p[0] for p in b) / len(b):.0f}, {sum(p[1] for p in b) / len(b):.0f})" for b in best)

    def _source_rank(self, nid, rate, here, prefer):
        """Mobs in the player's (or the quest's) zone and near their level first, then by drop rate."""
        n = self.db["npcs"].get(nid) or {}
        in_zone = bool(set(n.get("spawns", {})) & prefer)
        level = here.get("level")
        gap = abs(((n.get("min") or 0) + (n.get("max") or 0)) / 2 - level) if level else 0
        return (not in_zone, gap > 8, -rate)

    def _npc_line(self, nid, here):
        n = self.db["npcs"].get(nid)
        if not n:
            return None
        lvl = n["min"] if n["min"] == n["max"] else f"{n['min']}-{n['max']}"
        sub = f" <{n['sub']}>" if n.get("sub") else ""
        hostile = "" if n.get("friendly") else ", hostile"
        rank = {1: ", elite", 2: ", rare elite", 3: ", boss", 4: ", rare spawn"}.get(n.get("rank"), "")
        return f"[[npc:{n['name']}]]{sub} (level {lvl}{rank}{hostile}): {self._where(n['spawns'], here)}"

    def _object_line(self, oid, here):
        o = self.db["objects"].get(oid)
        return o and f"object \"{o['name']}\": {self._where(o['spawns'], here)}"

    def _sources_note(self):
        return ("(QuestieDB covers mob drops, objects, vendors and quests. It does not cover skinning, "
                "mining, herbalism or crafting.)")

    def _giver(self, ids, here):
        npcs, objects, items = (ids + [[], [], []])[:3]
        out = [self._npc_line(i, here) for i in npcs[:2]] + [self._object_line(i, here) for i in objects[:2]]
        out += [f"item [[item:{self.db['items'][i]['name']}]]" for i in items[:2] if i in self.db["items"]]
        return [x for x in out if x]

    def describe_item(self, iid, here, indent="", quest_zone=None):
        it = self.db["items"].get(iid)
        if not it:
            return []
        lines = [f"{indent}Item [[item:{it['name']}]] (id {iid}):"]
        rates = self.db["drops"].get(iid, {})
        prefer = {z for z in (here.get("zone"), quest_zone) if z}
        sources = [n for n in set(it["npcs"]) | set(rates)
                   if n in self.db["npcs"] and self.db["npcs"][n]["spawns"]]
        sources.sort(key=lambda n: self._source_rank(n, rates.get(n, 0), here, prefer))
        local = [n for n in sources if set(self.db["npcs"][n]["spawns"]) & prefer]
        shown = local or sources  # when there are sources nearby, leave out the far-away ones
        if len(sources) > 100:  # cloth, potions...: not one mob's drop
            lines.append(f"{indent}  - a common drop from {len(sources)} different mobs; nearby examples:")
            shown = shown[:2]
        for nid in shown[:MAX_SOURCES]:
            rate = f"{rates[nid]:.0f}% drop" if nid in rates else "drops"
            lines.append(f"{indent}  - {rate} from {self._npc_line(nid, here)}")
        if len(sources) > min(len(shown), MAX_SOURCES):
            lines.append(f"{indent}  - and {len(sources) - min(len(shown), MAX_SOURCES)} more mobs elsewhere")
        objects = [o for o in it["objects"] if self.db["objects"].get(o, {}).get("spawns")]
        objects.sort(key=lambda o: not (set(self.db["objects"][o]["spawns"]) & prefer))
        for oid in objects[:MAX_SOURCES]:
            lines.append(f"{indent}  - looted from {self._object_line(oid, here)}")
        for vid in it["vendors"][:2]:
            line = self._npc_line(vid, here)
            if line:
                lines.append(f"{indent}  - sold by {line}")
        if it.get("startQuest") in self.db["quests"]:
            lines.append(f"{indent}  - starts [[quest:{self.db['quests'][it['startQuest']]['name']}]]")
        if len(lines) == 1:
            lines.append(f"{indent}  - no known source")
        return lines

    def describe_quest(self, qid, here):
        q = self.db["quests"].get(qid)
        if not q:
            return []
        zone = f", {self.zone_name(q['zone'])}" if isinstance(q.get("zone"), int) and q["zone"] > 0 else ""
        lines = [f"Quest [[quest:{q['name']}]] (id {qid}, level {q['level']}, requires {q['req']}{zone}):"]
        givers, enders = self._giver(q["starts"], here), self._giver(q["ends"], here)
        for g in givers:
            lines.append(f"  - given by {g}" + (" (also where you turn it in)" if g in enders else ""))
        for g in enders:
            if g not in givers:
                lines.append(f"  - turn in to {g}")
        if q["pre"]:
            lines.append("  - requires first: " + ", ".join(
                f"[[quest:{self.db['quests'][p]['name']}]]" for p in q["pre"][:3] if p in self.db["quests"]))
        for nid in q["objectives"]["npc"]:
            line = self._npc_line(nid, here)
            if line:
                lines.append(f"  - kill {line}")
        for oid in q["objectives"]["object"]:
            line = self._object_line(oid, here)
            if line:
                lines.append(f"  - use {line}")
        quest_zone = q["zone"] if isinstance(q.get("zone"), int) and q["zone"] > 0 else None
        for iid in q["objectives"]["item"]:
            lines += self.describe_item(iid, here, indent="  ", quest_zone=quest_zone)
        return lines

    def describe(self, kind, eid, here):
        if kind == "quests":
            return self.describe_quest(eid, here)
        if kind == "items":
            return self.describe_item(eid, here)
        if kind == "npcs":
            n = self.db["npcs"][eid]
            lines = [f"NPC {self._npc_line(eid, here)}"]
            for label, key in (("starts", "qstarts"), ("finishes", "qends")):
                qs = [self.db["quests"][q]["name"] for q in n[key][:4] if q in self.db["quests"]]
                if qs:
                    lines.append(f"  - {label} " + ", ".join(f"[[quest:{x}]]" for x in qs))
            return lines
        o = self.db["objects"][eid]
        lines = [f"Object {self._object_line(eid, here)}"]
        for label, key in (("starts", "qstarts"), ("finishes", "qends")):
            qs = [self.db["quests"][q]["name"] for q in o[key][:4] if q in self.db["quests"]]
            if qs:
                lines.append(f"  - {label} " + ", ".join(f"[[quest:{x}]]" for x in qs))
        return lines

    # -- NPCs by title ("where is the first aid trainer") --

    def title_mentions(self, text, ctx):
        """NPC titles the question asks for, and the question with those words removed."""
        q = _norm(text)
        cls = _norm((ctx or {}).get("class") or "")
        if cls:
            q = re.sub(r"\b(my )?class trainer\b", f"{cls} trainer", q)
        found = []
        for m in re.finditer(r"\btrain(?:ing)? (?:my |in |up )?([a-z]+)(?: ([a-z]+))?", q):  # "train first aid"
            a, b = m.group(1), m.group(2)
            for cand in ([f"{a} {b} trainer"] if b else []) + [f"{a} trainer"]:
                if cand in self.titles:
                    found.append(cand)
                    q = q.replace(m.group(0), " ")
                    break
        words, used = q.split(), set()
        for size in range(min(4, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                span = set(range(start, start + size))
                if span & used:
                    continue
                phrase = " ".join(words[start:start + size])
                for cand in (phrase, phrase[:-1] if phrase.endswith("s") else None):
                    if cand and (cand in self.titles or cand in TITLE_ALIASES) and (size > 1 or cand not in COMMON):
                        found.append(cand)
                        used |= span
                        break
        rest = " ".join(w for i, w in enumerate(words) if i not in used)
        return found, rest

    def describe_title(self, title, here, faction):
        side = {"alliance": "A", "horde": "H"}.get((faction or "").lower())
        pool = [i for t in TITLE_ALIASES.get(title, [title]) for i in self.titles.get(t, [])]
        ids = [i for i in pool if not side or side in (self.db["npcs"][i].get("friendly") or "")]
        if not ids:
            return []

        def rank(nid):
            spawns = self.db["npcs"][nid]["spawns"]
            if here.get("zone") in spawns and here.get("pos"):
                px, py = here["pos"]
                return (0, min(math.hypot(x - px, y - py) for x, y in spawns[here["zone"]]))
            return (1 if here.get("zone") not in spawns else 0, 0)
        ids.sort(key=rank)
        who = f"friendly to the {faction.title()}" if side else "any faction"
        label = title.title() if title in TITLE_ALIASES else self.db["npcs"][ids[0]].get("sub")
        lines = [f"{label}s ({who}, nearest first):"]
        lines += [f"  - {self._npc_line(i, here)}" for i in ids[:4]]
        if len(ids) > 4:
            lines.append(f"  - and {len(ids) - 4} more elsewhere")
        return lines

    # -- question matching --

    def mentions(self, text):
        """Entities named in the text, longest names first: [(kind, [ids])]."""
        words = _norm(text).split()
        found, used = [], set()
        for size in range(min(self.max_words, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                span = set(range(start, start + size))
                if span & used:
                    continue
                phrase = " ".join(words[start:start + size])
                candidates = [phrase]
                if phrase.endswith("es"):
                    candidates.append(phrase[:-2])
                if phrase.endswith("s"):
                    candidates.append(phrase[:-1])
                for cand in candidates:
                    hits = self.names.get(cand)
                    if not hits or (size == 1 and (len(cand) < 5 or cand in COMMON)) or cand in self.zone_terms:
                        continue  # zone names ("Stormwind") also name signs and objects
                    found.append((size, cand, hits))
                    used |= span
                    break
        found.sort(key=lambda f: -f[0])
        return [hits for _, _, hits in found]

    def _here(self, ctx):
        here = {}
        if ctx:
            here["zone"] = self.zone_ids.get(_norm(ctx.get("zone") or ""))
            if isinstance(ctx.get("level"), int):
                here["level"] = ctx["level"]
            try:
                x, y = (float(v) for v in str(ctx.get("position", "")).split(","))
                here["pos"] = (x, y)
            except ValueError:
                pass
        return here

    def facts_for(self, question, ctx):
        """The game-data block for a question, or None if nothing relevant was found."""
        if not self.ready:
            return None
        here = self._here(ctx)
        log_ids = {q.get("id") for q in (ctx or {}).get("quests") or [] if isinstance(q, dict)}
        titles, question = self.title_mentions(question, ctx)
        title_lines = []
        for t in titles[:2]:
            title_lines += self.describe_title(t, here, (ctx or {}).get("faction"))
        picked = []
        for hits in self.mentions(question):
            kinds = {}
            for kind, eid in hits:
                kinds.setdefault(kind, []).append(eid)
            # A quest in the player's log wins; otherwise the kind with the most matches (same-name NPCs etc.).
            for kind in ("quests", "items", "npcs", "objects"):
                ids = kinds.get(kind)
                if not ids:
                    continue
                if kind == "quests":
                    ids = sorted(ids, key=lambda i: i not in log_ids)[:1]
                elif kind == "npcs":
                    ids = sorted(ids, key=lambda i: -len(sum(self.db["npcs"][i]["spawns"].values(), [])))[:2]
                else:
                    ids = ids[:1]
                picked += [(kind, i) for i in ids]
                break
        if not picked and not title_lines:
            # "where do I find the spiders for my quest": use the player's quests in this zone.
            if re.search(r"\bquests?\b", question, re.I):
                zone_name = (ctx or {}).get("zone")
                picked = [("quests", q["id"]) for q in (ctx or {}).get("quests") or []
                          if isinstance(q, dict) and q.get("id") in self.db["quests"]
                          and (not zone_name or q.get("area") == zone_name)][:3]
        lines, seen = list(title_lines), set()
        for kind, eid in picked[:MAX_ENTITIES]:
            if (kind, eid) in seen:
                continue
            seen.add((kind, eid))
            block = self.describe(kind, eid, here)
            if sum(len(x) + 1 for x in lines + block) > MAX_CHARS:
                break
            lines += block
        return "\n".join(lines + [self._sources_note()]) if lines else None
