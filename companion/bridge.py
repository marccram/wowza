"""Clipboard bridge with the WoW addon, addon installation, and conversation history.

Protocol (see WoWZA/Protocol.lua):
  Question (WoW -> app):  WZ1Q:<base64 of JSON {id, q, ctx}>
  Answer   (app -> WoW):  WZ1A:<id>:<status>:<base64 answer>:<base64 question>
Answers normally go back through the slot addons (write_slots); the clipboard answer is the fallback.
"""
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

from settings import APP_DIR, STATE_FILE, addon_source_dir

HISTORY_TURNS = 8
Q_PREFIX, A_PREFIX = "WZ1Q:", "WZ1A:"

SYSTEM_PROMPT = """You are WoWZA (World of Warcraft Zone Advisor), an expert advisor whose answers appear in a small window \
inside the game. The player is on WoW Forever: vanilla (Classic-era) content on the modern client, with \
classic three-tree talents rather than specializations. Questions usually come with a JSON snapshot of their \
character: level, class, talent points per tree, profession, secondary and weapon skills, equipped gear, \
zone, subzone, map name and coordinates (x, y as percentages), gold, and their quest log (quest IDs, titles, \
objectives, progress; complete = ready to turn in). Infer their build from the talent points.

Who you're talking to:
The player is experienced. They already know how to use the map, the quest log and tracker, quest text, \
the auction house, trainers, chat, and sites like Wowhead. Never suggest any of those, and never pad an \
answer with generic gameplay tips ("look for sparkling objects", "ask other players", "bandage between \
pulls"). Every line should be something they couldn't trivially work out themselves.

Game database:
Questions often come with a [Game database: QuestieDB] block: real Forever data on the quests, items, \
NPCs and objects the question names. It includes quest givers, objectives, drop sources with drop rates, \
vendors, mob levels, and spawn coordinates (zone map percentages, as the player sees them), with the spot \
nearest the player. Treat it as authoritative and prefer it over your memory. Give its coordinates and \
drop rates, and recommend the nearest, level-appropriate source. It already uses the exact names, so \
copy its [[...]] markup. If it doesn't cover what was asked (for example skinning, gathering or crafting), \
answer from your own knowledge and say so.

How to answer:
- Lead with the direct answer in the first line.
- Be specific: exact NPC, mob, item, spell and zone names, where things are (coordinates when you know \
them), drop sources, quest givers, and the order to do things in. Use their context: level, class, spec, \
zone, coordinates and quest progress.
- If you don't know the specific answer, write one short sentence saying exactly what you don't know, then \
give your single best concrete lead. Hedge once, not on every line. Don't fill the gap with general advice, \
don't trail off with "...", and don't announce that you won't invent things. Just don't invent names, numbers \
or coordinates.
- Answer only what was asked. Bring up other quests or plans only if they directly affect the answer.
- WoW Forever may differ from other versions. Mention it only when it actually changes the answer.
- Keep it under about 180 words. A short answer is fine when the question is simple.

Formatting (the addon renders this; use nothing else, no tables or other markdown):
- "## Heading" for short section headings, only when the answer has distinct parts.
- "- " for bullets. "**text**" to highlight a key word or two, sparingly.
- Required: every time you name a game thing, wrap it so it shows as an in-game link or colored name.
  This applies to every item, quest, spell, NPC, mob and zone, every time it appears. Never write one as plain text.
  [[item:Exact Item Name]], or [[item:ID|Exact Item Name]] when you're confident of the ID
  [[quest:Exact Quest Title]] (titles exactly as in their quest log)
  [[spell:Exact Spell Name]]
  [[npc:Name]] (for NPCs and mobs alike)
  [[zone:Name]] (zones, towns and subzones)
  The addon checks every name against the game's data, so use exact in-game names.

Example of a correctly formatted answer:
[[item:Crisp Spider Meat]] drops from [[npc:Tarantula]] around [[zone:Lakeshire]].
- Turn it in to [[npc:Chef Breanna]] for [[quest:Redridge Goulash]].
- [[spell:Hamstring]] stops runners."""


# --- clipboard protocol -------------------------------------------------------

def parse_question(text):
    """Return {id, q, ctx} if text is a question payload from the addon, else None."""
    text = (text or "").strip()
    if not text.startswith(Q_PREFIX):
        return None
    try:
        data = json.loads(base64.b64decode(text[len(Q_PREFIX):]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id") or not data.get("q"):
        return None
    sig = data.get("sig")
    return {
        "id": safe_id(data["id"]),
        "q": data["q"],
        "ctx": data.get("ctx") or None,
        "sig": sig if isinstance(sig, int) and 1 <= sig <= SLOT_COUNT else None,
    }


def answer_payload(entry_id, status, answer, question=""):
    b64 = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")
    return f"{A_PREFIX}{safe_id(entry_id)}:{status}:{b64(answer)}:{b64(question)}"


def safe_id(entry_id):
    return re.sub(r"[^A-Za-z0-9-]", "", str(entry_id))[:40] or "x"


# --- addon install --------------------------------------------------------------

def addon_dir(wow_dir):
    return Path(wow_dir) / "Interface" / "AddOns" / "WoWZA"


def addon_installed(wow_dir):
    return bool(wow_dir) and (addon_dir(wow_dir) / "WoWZA.toc").exists()


def install_addon(wow_dir):
    """Copies the addon and creates the slot addons and signal files.

    Returns the number of new files; if it's above zero, WoW must be fully restarted to see them."""
    migrate_old_addon(wow_dir)
    dest = addon_dir(wow_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for f in addon_source_dir().iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    (dest / "Responses.lua").unlink(missing_ok=True)  # leftover from the old /reload bridge
    return ensure_transport_files(wow_dir)


OLD_ADDON = "ClaudeAdvisor"  # the addon's name before the rename to WoWZA


def old_addon_installed(wow_dir):
    return bool(wow_dir) and (Path(wow_dir) / "Interface" / "AddOns" / OLD_ADDON).is_dir()


def migrate_old_addon(wow_dir):
    """Carry the old addon's saved data (history, window, minimap) over to WoWZA, then remove the
    old addon and its answer slots so the two don't both load."""
    for old in (Path(wow_dir) / "WTF" / "Account").glob(f"*/SavedVariables/{OLD_ADDON}.lua"):
        new = old.with_name("WoWZA.lua")
        if not new.exists():  # the old file stays as a backup
            new.write_text(old.read_text(encoding="utf-8").replace(f"{OLD_ADDON}DB", "WoWZADB"),
                           encoding="utf-8")
    for d in (Path(wow_dir) / "Interface" / "AddOns").glob(f"{OLD_ADDON}*"):
        if d.is_dir() and re.fullmatch(rf"{OLD_ADDON}(_S\d{{3}})?", d.name):
            shutil.rmtree(d)


def wow_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WowB.exe", "/NH"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return "WowB.exe" in out


# --- answer slots and signals (see WoWZA/Transport.lua) ----------------------------
#
# WoW reads a file the first time it's used, if the file existed when the client launched
# (measured on Forever by wow-ai, github.com/chelinho139/wow-ai, MIT). So the slot addons and
# signal files are created up front, and answers are written into them later.

SLOT_COUNT = 100


def _silent_wav():
    """A valid one-second silent WAV. An empty file never really plays; this one plays long enough
    for the game to see it playing (C_Sound.IsPlaying) a moment after it starts."""
    rate, samples = 8000, 8000
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, rate, rate, 1, 8)
    data = struct.pack("<4sI", b"data", samples) + bytes([128]) * samples
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(data), b"WAVE") + fmt + data


SILENT_WAV = _silent_wav()


def slot_dir(wow_dir, i):
    return Path(wow_dir) / "Interface" / "AddOns" / f"WoWZA_S{i:03d}"


def signal_file(wow_dir, kind, n):
    return addon_dir(wow_dir) / kind / f"{n:03d}.wav"


def slots_installed(wow_dir):
    return bool(wow_dir) and (slot_dir(wow_dir, SLOT_COUNT) / "Inbox.lua").exists()


def ensure_transport_files(wow_dir):
    made = 0

    def ensure(path, content):
        nonlocal made
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            made += 1

    for i in range(1, SLOT_COUNT + 1):
        toc = (f"## Interface: 16001\n## Title: WoWZA answer slot {i:03d}\n"
               "## Notes: Delivers answers to WoWZA. Load-on-demand; leave it enabled.\n"
               "## LoadOnDemand: 1\n## Dependencies: WoWZA\n## Group: WoWZA\n\nInbox.lua\n")
        ensure(slot_dir(wow_dir, i) / f"WoWZA_S{i:03d}.toc", toc.encode())
        ensure(slot_dir(wow_dir, i) / "Inbox.lua", b"WoWZASlotData = nil\n")
        ensure(signal_file(wow_dir, "sig", i), b"")
        ensure(signal_file(wow_dir, "ack", i), b"")
    ensure(addon_dir(wow_dir) / "ctl" / "empty.wav", b"")
    valid = addon_dir(wow_dir) / "ctl" / "valid.wav"
    if valid.exists() and valid.read_bytes() != SILENT_WAV:
        valid.write_bytes(SILENT_WAV)  # older 10 ms version; the game sees the change after a restart
        made += 1
    ensure(valid, SILENT_WAV)
    return made


def reset_signals(wow_dir):
    """Empty every signal so the next game session starts clean."""
    for kind in ("sig", "ack"):
        for f in (addon_dir(wow_dir) / kind).glob("*.wav"):
            if f.stat().st_size:
                f.write_bytes(b"")


def raise_signal(wow_dir, kind, n):
    f = signal_file(wow_dir, kind, n)
    if f.parent.is_dir():
        f.write_bytes(SILENT_WAV)


def _lua_str(s):
    s = str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    return f'"{s}"'


def write_slots(wow_dir, answers):
    """Write the latest answers ({id, status, q, a}) into every slot the game hasn't loaded yet.

    We can't tell which slot the game loads next, so all of them get the same content."""
    lines = ["-- Written by the WoWZA companion app.", "WoWZASlotData = {",
             f"  ts = {int(time.time())},", "  answers = {"]
    for a in answers:
        took = f', took = {a["took"]:.1f}' if isinstance(a.get("took"), (int, float)) else ""
        lines.append(f'    {{ id = {_lua_str(a["id"])}, status = {_lua_str(a["status"])}, '
                     f'q = {_lua_str(a["q"])}, a = {_lua_str(a["a"])}{took} }},')
    lines += ["  },", "}", ""]
    content = "\n".join(lines).encode("utf-8")
    for i in range(1, SLOT_COUNT + 1):
        target = slot_dir(wow_dir, i) / "Inbox.lua"
        if not target.parent.is_dir():
            continue
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)


# --- conversation -----------------------------------------------------------------

def build_messages(history_items, question, ctx, facts=None):
    messages = []
    for h in history_items[-HISTORY_TURNS:]:
        messages.append({"role": "user", "content": h["q"]})
        messages.append({"role": "assistant", "content": h["a"]})
    content = f"[Question]\n{question}"
    if facts:
        content = f"[Game database: QuestieDB]\n{facts}\n\n{content}"
    if ctx:
        content = f"[Character context]\n{json.dumps(ctx, indent=1, ensure_ascii=False)}\n\n{content}"
    messages.append({"role": "user", "content": content})
    return messages


class History:
    """Answered questions plus the latest character snapshot, persisted in %APPDATA%."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items, self.last_ctx = [], None
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self.items = data.get("history", [])
                self.last_ctx = data.get("last_ctx")
            except (OSError, ValueError):
                pass

    def ids(self):
        with self.lock:
            return {h["id"] for h in self.items}

    def snapshot(self):
        with self.lock:
            return list(self.items)

    def add(self, entry_id, question, answer):
        with self.lock:
            self.items.append({"id": entry_id, "q": question, "a": answer})
            self.items = self.items[-200:]
            self._save()

    def set_ctx(self, ctx):
        with self.lock:
            self.last_ctx = ctx
            self._save()

    def _save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"history": self.items, "last_ctx": self.last_ctx}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
