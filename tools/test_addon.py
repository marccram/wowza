"""Runs the addon's Lua against a mock WoW API: the Ctrl+V fallback flow, then automatic delivery
through slot addons and signal files written by the real companion code into a fake game folder."""
import sys
import tempfile
from pathlib import Path

from lupa.lua51 import LuaRuntime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "companion"))
import bridge  # noqa: E402

MOCK = r"""
frames = {}
local function newObject(kind, name)
  local o = { kind = kind, name = name, scripts = {}, shown = true, text = "", messages = {} }
  return setmetatable(o, { __index = function(t, k)
    local m = rawget(_G.MockMethods, k)
    if m then return m end
    if k:match("^%u") then return function() end end  -- any other widget method is a no-op
  end })
end
MockMethods = {
  SetScript = function(self, s, f) self.scripts[s] = f end,
  GetScript = function(self, s) return self.scripts[s] end,
  Show = function(self) self.shown = true; if self.scripts.OnShow then self.scripts.OnShow(self) end end,
  Hide = function(self) self.shown = false; if self.scripts.OnHide then self.scripts.OnHide(self) end end,
  SetShown = function(self, v) if v then self:Show() else self:Hide() end end,
  IsShown = function(self) return self.shown end,
  SetText = function(self, t) self.text = t; if self.scripts.OnTextChanged then self.scripts.OnTextChanged(self, false) end end,
  GetText = function(self) return self.text end,
  AddMessage = function(self, m) table.insert(self.messages, m) end,
  Clear = function(self) self.messages = {} end,
  CreateTexture = function(self) return newObject("Texture") end,
  CreateFontString = function(self) local o = newObject("FontString"); hintString = o; return o end,
  SetSize = function(self, w, h) self.w, self.h = w, h end,
  GetWidth = function(self) return self.w or 140 end, GetHeight = function(self) return self.h or 140 end,
  SetPoint = function(self, p, rel, rp, x, y)
    if type(rel) ~= "table" then p, rel, rp, x, y = p, nil, p, rel, rp end
    self.point = { p, rel, rp or p, x or 0, y or 0 }
  end,
  GetPoint = function(self) if self.point then return unpack(self.point) end end,
  ClearAllPoints = function(self) self.point = nil end,
  SetAlpha = function(self, a) self.alpha = a end, GetAlpha = function(self) return self.alpha or 1 end,
  IsMouseOver = function(self) return self.mouseOver or false end,
  SetFocus = function(self) self.focus = true end, ClearFocus = function(self) self.focus = false end,
  HasFocus = function(self) return self.focus or false end,
  GetFrameLevel = function() return 1 end,
  GetScrollOffset = function() return 0 end,
  StartSizing = function(self) self.sizing = true end,
  StopMovingOrSizing = function(self) self.sizing = false end,
  GetCenter = function() return 100, 100 end, GetEffectiveScale = function() return 1 end,
  HighlightText = function(self) self.highlighted = true end,
}
function CreateFrame(kind, name, parent, template)
  local f = newObject(kind, name)
  if template == "ButtonFrameTemplate" then f.Inset = newObject("Frame") end
  if name then _G[name] = f end
  table.insert(frames, f)
  return f
end
Minimap = newObject("Frame"); UIParent = newObject("Frame"); GameTooltip = newObject("GameTooltip")
UISpecialFrames = {}; SOUNDKIT = {}; SlashCmdList = {}; tinsert = table.insert
PlaySound = function() end
wipe = function(t) for k in pairs(t) do t[k] = nil end return t end
strtrim = function(s) return (s:gsub("^%s+", ""):gsub("%s+$", "")) end
time = function() return 1727900000 end
C_Timer = { After = function(_, f) f() end }
IsControlKeyDown = function() return true end
GetCursorPosition = function() return 0, 0 end
GetMinimapShape = function() return "ROUND" end
printed = {}; print = function(...) table.insert(printed, table.concat({...}, " ")) end
-- character API
UnitLevel = function() return 12 end; UnitXP = function() return 500 end; UnitXPMax = function() return 4000 end
UnitClass = function() return "Warrior", "WARRIOR", 1 end; UnitRace = function() return "Human" end
UnitFactionGroup = function() return "Alliance" end
GetRealZoneText = function() return "Elwynn Forest" end; GetSubZoneText = function() return "Jasperlode Mine" end
GetMoney = function() return 123456 end; GetAverageItemLevel = function() return 15.2, 14.8 end
GetSpecialization = function() return 1 end; GetSpecializationInfo = function() return 71, "Arms" end
C_Map = { GetBestMapForUnit = function() return 1429 end,
          GetPlayerMapPosition = function() return { GetXY = function() return 0.42, 0.65 end } end }
-- item/spell/quest data
local items = { [2723] = { "Cask of Merlot", 1, 132797 }, [2589] = { "Linen Cloth", 1, 132889 } }
cachedItems = { [2723] = true }            -- 2589 not cached yet: needs a server round trip
GetItemInfo = function(q)
  for id, it in pairs(items) do
    if (q == id and cachedItems[id]) or (type(q) == "string" and q:lower() == it[1]:lower() and cachedItems[id]) then
      local link = "|cffffffff|Hitem:" .. id .. "::::::::|h[" .. it[1] .. "]|h|r"
      return it[1], link, it[2], 1, 1, "", "", 1, "", it[3]
    end
  end
end
C_Spell = {
  GetSpellInfo = function(q)
    if q == 772 or q == "Rend" then return { name = "Rend", iconID = 132155, spellID = 772 } end
  end,
  GetSpellLink = function(id) return "|cff71d5ff|Hspell:" .. id .. ":0|h[Rend]|h|r" end,
}
GetQuestLink = function(id) if id == 60 then return "|cffffff00|Hquest:60:10|h[Kobold Candles]|h|r" end end
-- context API (Forever: classic talents, C_SkillInfo)
GetBuildInfo = function() return "1.60.1", "70205", "Oct 1 2026", 16001 end
GetNumTalentTabs = function() return 3 end
local tabs = { { "Arms", 21 }, { "Fury", 5 }, { "Protection", 0 } }
GetTalentTabInfo = function(i) return tabs[i][1], 132355, tabs[i][2], "WarriorArms" end
GetSpecialization = nil; GetSpecializationInfo = nil
C_SkillInfo = {
  GetNumSkillLines = function() return 6 end,
  GetSkillLineInfo = function(i)
    return ({ { name = "Professions", isHeader = true },
              { name = "Mining", rank = 87, maxRank = 150, skillID = 186 },
              { name = "Secondary Skills", isHeader = true },
              { name = "First Aid", rank = 40, maxRank = 75, skillID = 129 },
              { name = "Weapon Skills", isHeader = true },
              { name = "Two-Handed Swords", rank = 58, maxRank = 60, skillID = 55 } })[i]
  end,
}
GetInventoryItemLink = function(_, slot)
  if slot == 16 then return "|cff1eff00|Hitem:2244::::|h[Krol Blade]|h|r" end
end
C_Map.GetMapInfo = function() return { name = "Elwynn Forest" } end

-- File-backed client, following the rules measured on Forever: a file is read from disk the first
-- time it's used if it existed at launch; a playable sound stays playable for the session.
launchedAddons = {}      -- addon folders present when the "client" started
loadedAddons = {}
validSounds = {}
now = 0
GetTime = function() return now end
tickers = {}
C_Timer.NewTicker = function(_, fn)
  local t = { fn = fn, Cancel = function(self) self.cancelled = true end }
  table.insert(tickers, t)
  return t
end
StopSound = function() end
emptyPlays = false  -- some clients say any existing file "will play" (the player's does)
emptyCached = false -- ...and might keep an empty file empty once read
local seenEmpty, handles = {}, {}
local function handle(playing) handles[#handles + 1] = playing; return #handles end
PlaySoundFile = function(path)
  if validSounds[path] then return true, handle(true) end
  local data = py_read(path)
  if emptyCached and seenEmpty[path] then data = "" end
  if data and #data > 0 then validSounds[path] = true; return true, handle(true) end
  if data then seenEmpty[path] = true end
  if data and emptyPlays then return true, handle(false) end
  return false
end
C_Sound = { IsPlaying = function(h) return handles[h] or false end }
C_AddOns = {
  GetAddOnInfo = function(name)
    if not launchedAddons[name] then return name, nil, nil, false, "MISSING" end
    return name, name, "", true, nil
  end,
  IsAddOnLoaded = function(name) return loadedAddons[name] or false end,
  LoadAddOn = function(name)
    if not launchedAddons[name] then return false, "MISSING" end
    loadedAddons[name] = true
    assert(loadstring(py_read("Interface\\AddOns\\" .. name .. "\\Inbox.lua")))()
    return true
  end,
}
C_QuestLog = {
  GetNumQuestLogEntries = function() return 2 end,
  GetInfo = function(i)
    if i == 1 then return { isHeader = true, title = "Elwynn Forest" } end
    return { title = "Kobold Candles", level = 10, questID = 60, isHidden = false }
  end,
  GetQuestObjectives = function() return { { text = "Large Candle: 2/8" } } end,
  IsComplete = function() return false end,
}
"""


def main():
    wow = Path(tempfile.mkdtemp())  # a fake WoW game folder, filled by the real companion code
    (wow / "Interface" / "AddOns").mkdir(parents=True)

    def py_read(path):
        f = wow / path.replace("\\", "/")
        return f.read_bytes() if f.is_file() else None

    L = LuaRuntime(unpack_returned_tuples=True)
    L.globals().py_read = py_read
    L.execute(MOCK)
    ns = L.table()
    loader = L.eval('function(src, ns) local f = assert(loadstring(src)); f("WoWZA", ns) end')
    for f in ("Protocol.lua", "Format.lua", "Transport.lua", "Core.lua"):
        loader((ROOT / "WoWZA" / f).read_text(encoding="utf-8"), ns)
    g = L.globals()

    # Fire ADDON_LOADED on the loader frame (the last frame with an OnEvent script).
    frames = [f for f in L.eval("frames").values() if f.scripts.OnEvent]
    frames[-1].scripts.OnEvent(frames[-1], "ADDON_LOADED", "WoWZA")
    assert g.WoWZAMinimapButton is not None, "minimap button missing"
    assert g.WoWZAFrame.shown is False

    g.SlashCmdList["WOWZA"]("")
    assert g.WoWZAFrame.shown is True and g.WoWZADB.open is True
    box, log = g.WoWZAInput, None
    log = next(f for f in L.eval("frames").values() if f.kind == "ScrollingMessageFrame")

    # 1. Type + Enter: payload placed in the box and highlighted
    box.text = "What should I do next?"
    box.scripts.OnEnterPressed(box)
    q = bridge.parse_question(box.text)
    assert box.highlighted and q and q["q"] == "What should I do next?", box.text
    c = q["ctx"]
    assert c["subzone"] == "Jasperlode Mine" and "spec" not in c, "no spec API on Forever -> no spec field"
    assert c["talents"] == [{"tree": "Arms", "points": 21}, {"tree": "Fury", "points": 5},
                            {"tree": "Protection", "points": 0}], c.get("talents")
    assert c["skills"] == ["Mining 87/150", "First Aid 40/75", "Two-Handed Swords 58/60"], c.get("skills")
    assert c["gear"] == ["Krol Blade"] and c["map"] == "Elwynn Forest" and c["position"] == "42.0, 65.0"
    assert c["client"] == "1.60.1.70205 (interface 16001)"
    assert c["quests"][0]["area"] == "Elwynn Forest" and c["quests"][0]["id"] == 60
    assert isinstance(q["sig"], int), "question carries its signal number"
    print("Enter -> payload ok:", q["id"], "| ctx keys:", sorted(q["ctx"]))

    # 2. Ctrl+C: box clears, entry committed as "sent"
    box.scripts.OnKeyDown(box, "C")
    assert box.text == "" and len(g.WoWZADB.history) == 1
    print("Ctrl+C -> committed, status:", g.WoWZADB.history[1].status)

    def paste(text):
        box.text = text
        box.scripts.OnTextChanged(box, True)

    # 3. Early paste: clipboard still holds the question -> companion isn't running
    paste("WZ1Q:e30=")
    assert "hasn't picked up" in g.hintString.text and box.text == "", g.hintString.text
    print("paste of unanswered question -> hint:", g.hintString.text)

    # 4. Pending, partial, done
    for status, text in (("pending", ""), ("partial", "Finish Kobold"), ("done", 'Finish Kobold Candles.\n- Use "Rend" | Overpower')):
        paste(bridge.answer_payload(q["id"], status, text, q["q"]))
        e = g.WoWZADB.history[1]
        assert e.status == status and e.a == text and box.text == "", (status, e.status, e.a)
    msgs = list(log.messages.values())
    print("log after done:", msgs)
    assert any("Use \"Rend\" || Overpower" in m for m in msgs), "pipe should be escaped for display"

    # 5. Answer to a question asked from the PC app creates a new entry
    paste(bridge.answer_payload("pc-99", "done", "From the PC", "PC question"))
    assert g.WoWZADB.history[2].q == "PC question"

    # 6. Esc during copy cancels; typing over the payload cancels
    box.text = "Another"
    box.scripts.OnEnterPressed(box)
    box.scripts.OnEscapePressed(box)
    assert box.text == "" and len(g.WoWZADB.history) == 2

    # 7. Minimap toggle and right-click hide
    g.SlashCmdList["WOWZA"]("minimap")
    assert g.WoWZADB.minimap.hide is True

    # 8. Fade while moving, like the world map
    win, loader = g.WoWZAFrame, frames[-1]
    tick = lambda n=10: [win.scripts.OnUpdate(win, 0.05) for _ in range(n)]
    box.scripts.OnEscapePressed(box)  # not typing
    loader.scripts.OnEvent(loader, "PLAYER_STARTED_MOVING")
    tick()
    assert abs(win.alpha - 0.5) < 1e-9, win.alpha
    win.mouseOver = True; tick()
    assert win.alpha == 1, "mouse over keeps it solid"
    win.mouseOver = False; tick(); box.focus = True; tick()
    assert win.alpha == 1, "typing keeps it solid"
    box.focus = False; tick()
    loader.scripts.OnEvent(loader, "PLAYER_STOPPED_MOVING"); tick()
    assert win.alpha == 1
    g.SlashCmdList["WOWZA"]("fade 30")
    loader.scripts.OnEvent(loader, "PLAYER_STARTED_MOVING"); tick(20)
    assert abs(win.alpha - 0.3) < 1e-9, win.alpha
    print("fade ok: 50% default, solid on mouseover/typing, /za fade 30 ->", win.alpha)

    # 9. Resize grip + saved layout
    grip = next(f for f in L.eval("frames").values() if f.kind == "Button" and f.scripts.OnMouseDown)
    grip.scripts.OnMouseDown(grip)
    assert win.sizing
    win.w, win.h = 800, 600  # what dragging the grip does
    grip.scripts.OnMouseUp(grip)
    lay = g.WoWZADB.layout
    assert (lay.w, lay.h) == (800, 600), (lay.w, lay.h)
    win.w = win.h = None; g.RestoreLayout = None
    g.SlashCmdList["WOWZA"]("reset")
    assert (win.w, win.h) == (560, 480) and g.WoWZADB.layout is None
    print("resize ok: saved 800x600, /za reset ->", win.w, "x", win.h)
    # 10. Answer formatting: links, icons, verification, headings, bullets
    F = ns.Format
    answer = "\n".join([
        "Get the [[item:2723|Cask of Merlot]] from [[npc:Barkeep Dobbins]] in [[zone:Goldshire]].",
        "## Steps",
        "- Turn in [[quest:Kobold Candles]] first, a **must** | do",
        "  - nested [[spell:772|Rend]] then [[item:9999|Cask of Merlot]] (wrong ID)",
        "- Farm [[item:2589|Linen Cloth]] (not cached) and [[item:Mystery Thing]]",
        "",
        "",
        "Done.",
    ])
    lines = list(F.Lines(answer).values())
    for ln in lines:
        print("   ", ln)
    joined = "\n".join(lines)
    assert "|T132797:0|t|cffffffff|Hitem:2723" in lines[0], "verified item gets icon + real link"
    assert "|cfffff3a0Barkeep Dobbins|r" in lines[0] and "|cff7fd6ffGoldshire|r" in lines[0]
    assert lines[1] == " " and lines[2] == "|cffffd100Steps|r", "heading with spacing"
    assert lines[3].startswith("  • ") and "|Hquest:60:10|h[Kobold Candles]" in lines[3]
    assert "|cffffffffmust|r || do" in lines[3], "highlight + escaped pipe"
    assert lines[4].startswith("      • ") and "|T132155:0|t|cff71d5ff|Hspell:772" in lines[4]
    assert lines[4].count("|Hitem:2723") == 1, "wrong ID falls back to the name lookup"
    assert "|cffffffff[Linen Cloth]|r" in lines[5] and F.pendingItems[2589], "uncached item waits for server"
    assert "|cffffffff[Mystery Thing]|r" in lines[5]
    assert lines[6] == " " and lines[7] == "Done." and len(lines) == 8, "blank lines collapsed"

    # server sends Linen Cloth -> re-render shows the real link
    L.eval("function() cachedItems[2589] = true end")()
    loader.scripts.OnEvent(loader, "GET_ITEM_INFO_RECEIVED", 2589)
    assert "|Hitem:2589" in "\n".join(F.Lines(answer).values()) and not F.pendingItems[2589]
    print("formatting ok")

    # 11. Automatic delivery. Install with the real companion code, then "launch" the client.
    new_files = bridge.install_addon(wow)
    assert new_files > 300, new_files
    for d in (wow / "Interface" / "AddOns").iterdir():
        g.launchedAddons[d.name] = True
    loader.scripts.OnEvent(loader, "PLAYER_LOGIN")
    T = ns.Transport
    assert T.slots and T.signals, "self-test: empty wav unplayable, valid wav playable"
    assert T.SlotsLeft() == 100

    def tick(seconds):
        for _ in range(int(seconds / 0.5)):
            g.now += 0.5
            for t in list(g.tickers.values()):
                if not t.cancelled:
                    t.fn()

    def ask(text):
        box.text = text
        box.scripts.OnEnterPressed(box)
        payload = box.text
        box.scripts.OnKeyDown(box, "C")
        return bridge.parse_question(payload)

    sound_before = len(list(g.printed.values()))
    win.Hide(win)
    q1 = ask("Best two-hander for me right now?")
    entry = g.WoWZADB.history[len(g.WoWZADB.history)]
    assert entry.status == "sent" and "thinking" in g.hintString.text
    tick(1)
    bridge.raise_signal(wow, "ack", q1["sig"])        # companion picked it up
    tick(9)
    assert "thinking" in g.hintString.text and "hasn't picked up" not in g.hintString.text
    answer = "## Upgrade\n- [[item:2244|Krol Blade]] is already great at 18."
    bridge.write_slots(wow, [{"id": q1["id"], "status": "done", "q": q1["q"], "a": answer}])
    bridge.raise_signal(wow, "sig", q1["sig"])
    tick(0.5)
    assert entry.status == "done" and entry.a == answer, (entry.status, entry.a)
    assert g.hintString.text.startswith("Type a question"), g.hintString.text
    assert T.SlotsLeft() == 99, "one slot per answer when signals work"
    assert any("answer is ready" in p for p in list(g.printed.values())[sound_before:]), "window closed -> chat note"
    print("automatic delivery ok: ack -> thinking, sig -> answer in 1 slot, chat note when window is closed")

    # 12. Companion not running: no ack -> hint after 8 s
    q2 = ask("Is anyone there?")
    tick(10)
    assert "hasn't picked up" in g.hintString.text, g.hintString.text
    print("no companion -> hint:", g.hintString.text[:60], "...")

    def stop_watchers():
        for t in g.tickers.values():
            t.cancelled = True

    def last_entry():
        return g.WoWZADB.history[len(g.WoWZADB.history)]

    stop_watchers()
    first_poll = 13  # no answer times learned yet: typical 12 s, first check 1 s after

    # 13. Signal number already raised earlier this session -> timed slot polls instead
    g.WoWZADB.seq = q1["sig"] - 1            # next question gets q1's (already raised) number
    left = T.SlotsLeft()
    q3 = ask("Where do I train Mining?")
    assert q3["sig"] == q1["sig"]
    e3 = last_entry()
    tick(first_poll - 1)
    assert e3.status == "sent" and T.SlotsLeft() == left, "no false 'ready' from the stale signal"
    bridge.write_slots(wow, [{"id": q3["id"], "status": "done", "q": q3["q"], "a": "Stormwind, Dwarven District."}])
    tick(1.5)
    assert e3.status == "done", e3.status
    print(f"stale signal -> scheduled poll picked up the answer at {first_poll} s")

    # 14. Signals unusable -> polling schedule still delivers
    T.signals = False
    q4 = ask("Fastest route to Redridge?")
    e4 = last_entry()
    bridge.write_slots(wow, [{"id": q4["id"], "status": "error", "q": q4["q"], "a": "Error: rate limited"}])
    tick(first_poll + 0.5)
    assert e4.status == "error" and e4.a == "Error: rate limited"
    print("no signals -> polling delivered (error answers too)")

    # 15b. No signals and no companion: the first poll shows it never arrived; then the companion
    #      starts, marks it pending, and answers
    q5 = ask("Where can I farm Light Hide?")
    e5 = last_entry()
    tick(first_poll + 0.5)
    assert "hasn't picked up" in g.hintString.text, g.hintString.text
    bridge.write_slots(wow, [{"id": q5["id"], "status": "pending", "q": q5["q"], "a": ""}])
    tick(4)  # next poll 4 s later
    assert e5.status == "pending" and "thinking" in g.hintString.text, (e5.status, g.hintString.text)
    bridge.write_slots(wow, [{"id": q5["id"], "status": "done", "q": q5["q"], "a": "Westfall coyotes."}])
    tick(5)  # and 5 s after that
    assert e5.status == "done" and e5.a == "Westfall coyotes."
    print("no signals + companion down -> warned at first poll, recovered when it started")
    stop_watchers()

    # 16. A client like the player's: it says every file "will play" (empty=true), so the self-test
    #     switches to checking whether the sound is really playing.
    g.emptyPlays = True
    bridge.reset_signals(wow)
    T.Init()
    assert T.mode == "playing" and T.signals, T.selfTest
    print("empty-plays client -> self-test:", T.selfTest)
    left = T.SlotsLeft()
    q6 = ask("Which trainer teaches Cleave?")
    e6 = last_entry()
    tick(1)
    bridge.raise_signal(wow, "ack", q6["sig"])
    tick(9)
    assert "thinking" in g.hintString.text and "hasn't picked up" not in g.hintString.text, g.hintString.text
    bridge.write_slots(wow, [{"id": q6["id"], "status": "done", "q": q6["q"], "a": "Any warrior trainer.", "took": 7}])
    bridge.raise_signal(wow, "sig", q6["sig"])
    tick(0.5)
    assert e6.status == "done" and T.SlotsLeft() == left - 1, (e6.status, left, T.SlotsLeft())
    assert T.TypicalSeconds() == 7, "answer time learned from the companion"
    print("'playing' signals -> answer in 1 slot; learned typical answer time:", T.TypicalSeconds(), "s")

    # 17. Worse client: an empty file read once stays empty. The ack never shows, but the slots say
    #     the companion has the question -> stop trusting signals, remember it, keep polling.
    g.emptyCached = True
    q7 = ask("Is Deadmines worth it at 18?")
    e7 = last_entry()
    tick(1)   # the game reads ack/sig while they're empty...
    bridge.write_slots(wow, [{"id": q7["id"], "status": "pending", "q": q7["q"], "a": ""}])
    bridge.raise_signal(wow, "ack", q7["sig"])  # ...so this never becomes visible
    tick(8)
    assert not T.signals and g.WoWZADB.playingBroken, T.selfTest
    assert "thinking" in g.hintString.text, "companion has it -> no false 'not running' warning"
    bridge.write_slots(wow, [{"id": q7["id"], "status": "done", "q": q7["q"], "a": "Yes, with a group."}])
    tick(10)
    assert e7.status == "done", e7.status
    T.Init()
    assert T.mode is None and "didn't work before" in T.selfTest, T.selfTest
    print("stuck signals detected -> polling for the session, not retried next login")
    stop_watchers()

    # 15. Companion helpers: reset clears raised signals, slots skip missing folders
    bridge.reset_signals(wow)
    assert all(f.stat().st_size == 0 for f in (wow / "Interface/AddOns/WoWZA/sig").glob("*.wav"))
    assert bridge.install_addon(wow) == 0, "re-install creates nothing new"
    print("all addon flow checks passed")


if __name__ == "__main__":
    main()
