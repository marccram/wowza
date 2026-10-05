-- WoWZA: in-game window + minimap button. Questions go to the companion app through the
-- clipboard (Ctrl+C); answers come back through load-on-demand slot addons (Transport.lua), with
-- Ctrl+V as the fallback. No /reload is ever needed.

local ADDON, ns = ...
local P = ns.Protocol
local T = ns.Transport

local MAX_QUESTS = 30
local MAX_QUESTION_LEN = 1000
local MAX_HISTORY = 30
local ICON = "Interface\\AddOns\\WoWZA\\icon"
local DEFAULT_W, DEFAULT_H = 560, 480
local MIN_W, MIN_H, MAX_W, MAX_H = 380, 280, 1400, 1100
local DEFAULT_FADE_ALPHA = 0.5 -- opacity while moving (the world map's default fade)
local FADE_SECONDS = 0.25

local frame, input, hint, minimapButton
local playerMoving = false
local SaveLayout, RestoreLayout, UpdateFade -- defined below, used in CreateUI
local state = "ask"   -- ask: typing | copy: payload selected, waiting for Ctrl+C | wait: waiting for Ctrl+V
local outgoing        -- { entry, payload } between Enter and Ctrl+C
local counter = 0

local HINTS = {
    ask = "Type a question and press Enter.",
    copy = "|cffffd100Press Ctrl+C|r to send it to the companion app.  (Esc cancels)",
    wait = "Sent. When you hear the chime, |cffffd100press Ctrl+V|r here to show the answer.",
    thinking = "Sent. WoWZA is thinking...",
}

local function safe(fn, ...)
    local ok, a = pcall(fn, ...)
    if ok then return a end
end

local function esc(s) return (tostring(s or ""):gsub("|", "||")) end

local function SetState(s, msg)
    state = s
    if hint then hint:SetText(msg or HINTS[s]) end
end

---------------------------------------------------------------------------
-- Character context sent with each question
---------------------------------------------------------------------------
local function BuildContext()
    local ctx = {}
    ctx.level = safe(UnitLevel, "player")
    ctx.xp = safe(UnitXP, "player")
    ctx.xpMax = safe(UnitXPMax, "player")
    ctx.class = safe(function() return (UnitClass("player")) end)
    ctx.race = safe(function() return (UnitRace("player")) end)
    ctx.faction = safe(function() return (UnitFactionGroup("player")) end)
    ctx.zone = safe(GetRealZoneText)
    ctx.subzone = safe(GetSubZoneText)
    ctx.gold = safe(function() return math.floor(GetMoney() / 10000) end)
    ctx.avgItemLevel = safe(function() return math.floor((select(2, GetAverageItemLevel()))) end)

    ctx.client = safe(function()
        local version, build, _, toc = GetBuildInfo()
        return string.format("%s.%s (interface %s)", version, build, toc)
    end)

    -- Forever is vanilla content: classic talent trees, not modern specs. Points per tree tell the build.
    ctx.talents = safe(function()
        local out = {}
        for i = 1, GetNumTalentTabs() do
            local a, b, c, _, e = GetTalentTabInfo(i)
            local name, points = a, c -- classic: name, icon, pointsSpent
            if type(a) ~= "string" then name, points = b, e end -- later clients: id, name, desc, icon, pointsSpent
            if type(name) == "string" and type(points) == "number" then
                out[#out + 1] = { tree = name, points = points }
            end
        end
        return #out > 0 and out or nil
    end)
    ctx.spec = safe(function() -- only on clients that have specializations
        local getSpec = (C_SpecializationInfo and C_SpecializationInfo.GetSpecialization) or GetSpecialization
        local getInfo = (C_SpecializationInfo and C_SpecializationInfo.GetSpecializationInfo) or GetSpecializationInfo
        local idx = getSpec()
        local name = idx and select(2, getInfo(idx))
        if type(name) == "string" and name ~= "" then return name end
    end)

    ctx.skills = safe(function()
        local wanted = {
            [TRADE_SKILLS or "Professions"] = true,
            [SECONDARY_SKILLS or "Secondary Skills"] = true,
            [WEAPON_SKILLS or "Weapon Skills"] = true,
        }
        local out, header, seen = {}, nil, {}
        local function add(name, isHeader, rank, maxRank)
            if isHeader then
                header = name
            elseif header and wanted[header] and not seen[name] and type(rank) == "number" then
                seen[name] = true
                out[#out + 1] = string.format("%s %d/%d", name, rank, maxRank or 0)
            end
        end
        if C_SkillInfo and C_SkillInfo.GetNumSkillLines then -- Forever (one table per line)
            for i = 1, C_SkillInfo.GetNumSkillLines() do
                local sk = C_SkillInfo.GetSkillLineInfo(i)
                if type(sk) == "table" and (sk.parentSkillLineID or 0) == 0 then
                    add(sk.name, sk.isHeader, sk.rank, sk.maxRank)
                end
            end
        else
            for i = 1, GetNumSkillLines() do
                local name, isHeader, _, rank, _, _, maxRank = GetSkillLineInfo(i)
                add(name, isHeader, rank, maxRank)
            end
        end
        return #out > 0 and out or nil
    end)

    ctx.gear = safe(function() -- equipped item names, for upgrade and stat questions
        local out = {}
        for slot = 1, 19 do
            local link = GetInventoryItemLink("player", slot)
            local name = link and link:match("%[(.-)%]")
            if name then out[#out + 1] = name end
        end
        return #out > 0 and out or nil
    end)

    local mapID = safe(function() return C_Map.GetBestMapForUnit("player") end)
    ctx.map = mapID and safe(function() return C_Map.GetMapInfo(mapID).name end)
    ctx.position = mapID and safe(function()
        local x, y = C_Map.GetPlayerMapPosition(mapID, "player"):GetXY()
        if x > 0 or y > 0 then return string.format("%.1f, %.1f", x * 100, y * 100) end
    end)

    ctx.quests = safe(function()
        local quests, header = {}, nil
        for i = 1, C_QuestLog.GetNumQuestLogEntries() do
            local info = C_QuestLog.GetInfo(i)
            if info then
                if info.isHeader then
                    header = info.title
                elseif not info.isHidden and #quests < MAX_QUESTS then
                    local objs = {}
                    for _, o in ipairs(C_QuestLog.GetQuestObjectives(info.questID) or {}) do
                        objs[#objs + 1] = o.text
                    end
                    quests[#quests + 1] = {
                        id = info.questID,
                        title = info.title,
                        level = info.level,
                        area = header,
                        complete = C_QuestLog.IsComplete(info.questID) and true or false,
                        objectives = objs,
                    }
                end
            end
        end
        return quests
    end)
    return ctx
end

---------------------------------------------------------------------------
-- Conversation
---------------------------------------------------------------------------
local function History() return WoWZADB.history end

local function FindEntry(id)
    for _, h in ipairs(History()) do
        if h.id == id then return h end
    end
end

local function AddEntry(entry)
    local h = History()
    h[#h + 1] = entry
    while #h > MAX_HISTORY do table.remove(h, 1) end
end

local DIM, RED = { 0.6, 0.6, 0.6 }, { 1, 0.42, 0.42 }
local EMPTY_TEXT = "Ask about your quests, zone, class, talents, rotation or leveling.\n"
    .. "Your level, talents, gear, skills, zone and quest log are included automatically."

-- The conversation as chat bubbles (see Chat.lua): your question, then WoWZA's answer.
local function Render(keepScroll)
    local items = {}
    for _, e in ipairs(History()) do
        items[#items + 1] = { role = "player", text = esc(e.q) }
        if e.status == "error" then
            items[#items + 1] = { role = "wowza", text = esc(e.a), color = RED }
        elseif e.a and e.a ~= "" then
            local body = table.concat(ns.Format.Lines(e.a), "\n")
            if e.status == "partial" then body = body .. "\n|cff999999(still writing...)|r" end
            items[#items + 1] = { role = "wowza", text = body }
        elseif e.status == "sent" or e.status == "pending" then
            items[#items + 1] = { role = "wowza", text = "WoWZA is thinking...", color = DIM }
        end
    end
    ns.Chat.Render(items, keepScroll)
end

-- Item data arrives asynchronously; redraw once it's in so icons and quality colors appear.
local renderQueued = false
local function OnItemInfo(itemID)
    if not ns.Format.pendingItems[itemID] then return end
    ns.Format.pendingItems[itemID] = nil
    if renderQueued then return end
    renderQueued = true
    C_Timer.After(0.2, function()
        renderQueued = false
        Render(true)
    end)
end

local function Ask(text)
    text = strtrim(text or "")
    if text == "" or text:find("^WZ1") then return end
    counter = counter + 1
    WoWZADB.seq = (WoWZADB.seq or 0) + 1
    local sig = T.SignalNumber(WoWZADB.seq)
    local entry = {
        id = string.format("%d-%d", time(), counter),
        q = text:sub(1, MAX_QUESTION_LEN),
        a = "",
        status = "sent",
    }
    local payload = P.EncodeQuestion(entry.id, entry.q, BuildContext(), sig)
    outgoing = { entry = entry, payload = payload, sig = sig }
    -- Check the signal before the companion can see the question (see Transport.lua).
    T.PrepareSignal(sig, outgoing)
    input:SetText(payload)
    input:SetFocus()
    input:HighlightText()
    SetState("copy")
end

local function AnswerArrived(entry)
    Render()
    if state == "wait" then SetState("ask") end
    PlaySound(SOUNDKIT.TELL_MESSAGE)
    if not frame:IsShown() then
        print("|cffffd100WoWZA:|r your answer is ready. Click the minimap button or type /za.")
    end
end

local function IsFinal(e) return e.status == "done" or e.status == "error" end

-- Answers delivered through the slot addons. Returns true once `entry` is final.
local function ApplySlot(data, entry)
    local wasFinal = IsFinal(entry)
    for _, a in ipairs(type(data.answers) == "table" and data.answers or {}) do
        local e = FindEntry(a.id)
        if e and not IsFinal(e) then
            e.a, e.status = a.a, a.status
            if e == entry and IsFinal(e) then T.RecordTook(a.took) end
        end
    end
    if IsFinal(entry) and not wasFinal then AnswerArrived(entry) end
    return IsFinal(entry)
end

local function WatchForAnswer(o)
    local entry = o.entry
    local polled = false
    SetState("wait", HINTS.thinking)
    T.Watch(o.sig, o, {
        onData = function(data)
            polled = true
            return ApplySlot(data, entry)
        end,
        received = function() return entry.status ~= "sent" end,
        onTick = function(elapsed, acked)
            if IsFinal(entry) then return true end -- e.g. pasted with Ctrl+V meanwhile
            if state ~= "wait" then return end
            -- The companion marks a question "pending" in the slots (and raises its ack) on pickup.
            local received = acked or entry.status == "pending" or entry.status == "partial"
            if not received and ((o.useSignals and elapsed > 8) or polled) then
                SetState("wait", "|cffff6b6bThe companion app hasn't picked up your question.|r "
                    .. "Start it, then ask again.")
            else
                SetState("wait", string.format("%s  %ds", HINTS.thinking, elapsed))
            end
        end,
        onGiveUp = function(reason)
            if entry.status ~= "sent" or state ~= "wait" then return end
            local why = reason == "exhausted" and "Answer slots are used up for this session (/reload frees them). "
                or "No answer arrived automatically. "
            SetState("wait", why .. "If the companion shows it, |cffffd100press Ctrl+V|r here.")
        end,
    })
end

local function CommitOutgoing()
    if not outgoing then return end
    local o = outgoing
    AddEntry(o.entry)
    outgoing = nil
    Render()
    if T.slots then
        WatchForAnswer(o)
    else
        SetState("wait") -- no slot addons yet: Ctrl+V the answer
    end
end

local function HandleAnswer(text)
    local msg = P.DecodeAnswer(text)
    if not msg then return end
    if outgoing and outgoing.entry.id == msg.id then CommitOutgoing() end
    local entry = FindEntry(msg.id)
    if not entry then
        entry = { id = msg.id, q = msg.q ~= "" and msg.q or "(asked from the companion app)", a = "" }
        AddEntry(entry)
    end
    entry.a, entry.status = msg.answer, msg.status
    Render()
    if msg.status == "done" or msg.status == "error" then
        SetState("ask")
    elseif msg.status == "pending" then
        SetState("wait", "WoWZA is thinking. |cffffd100Press Ctrl+V|r again in a few seconds.")
    else
        SetState("wait", "Still writing. |cffffd100Press Ctrl+V|r again for the rest.")
    end
end

---------------------------------------------------------------------------
-- Main window (standard Blizzard templates)
---------------------------------------------------------------------------
local function CreateStandardFrame()
    local ok, f = pcall(CreateFrame, "Frame", "WoWZAFrame", UIParent, "ButtonFrameTemplate")
    if ok then
        if ButtonFrameTemplate_HidePortrait then ButtonFrameTemplate_HidePortrait(f) end
        return f
    end
    return CreateFrame("Frame", "WoWZAFrame", UIParent, "BasicFrameTemplateWithInset")
end

local function SetFrameTitle(f, text)
    if f.SetTitle then return f:SetTitle(text) end
    local fs = f.TitleText or (f.TitleContainer and f.TitleContainer.TitleText)
    if fs then fs:SetText(text) end
end

-- Size and position persist across sessions.
function SaveLayout()
    local point, _, relPoint, x, y = frame:GetPoint()
    if not point then return end
    WoWZADB.layout = {
        point = point, relPoint = relPoint, x = x, y = y,
        w = frame:GetWidth(), h = frame:GetHeight(),
    }
end

function RestoreLayout()
    local l = WoWZADB.layout
    frame:ClearAllPoints()
    if l then
        frame:SetSize(math.max(MIN_W, math.min(MAX_W, l.w)), math.max(MIN_H, math.min(MAX_H, l.h)))
        frame:SetPoint(l.point, UIParent, l.relPoint, l.x, l.y)
    else
        frame:SetSize(DEFAULT_W, DEFAULT_H)
        frame:SetPoint("CENTER")
    end
end

-- Like the world map: fade while the player moves, unless the mouse is over the window or you're typing.
function UpdateFade(self, elapsed)
    local target = 1
    if playerMoving and not self:IsMouseOver() and not input:HasFocus() then
        target = WoWZADB.fadeAlpha
    end
    local alpha = self:GetAlpha()
    if alpha ~= target then
        local step = elapsed / FADE_SECONDS
        if alpha > target then
            alpha = math.max(target, alpha - step)
        else
            alpha = math.min(target, alpha + step)
        end
        self:SetAlpha(alpha)
    end
end

local function CreateResizeGrip()
    local grip = CreateFrame("Button", nil, frame)
    grip:SetSize(16, 16)
    grip:SetPoint("BOTTOMRIGHT", -4, 4)
    grip:SetFrameLevel(frame:GetFrameLevel() + 10)
    grip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
    grip:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
    grip:SetPushedTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down")
    grip:SetScript("OnMouseDown", function() frame:StartSizing("BOTTOMRIGHT") end)
    grip:SetScript("OnMouseUp", function()
        frame:StopMovingOrSizing()
        SaveLayout()
    end)
end

local function CreateUI()
    frame = CreateStandardFrame()
    frame:Hide() -- before OnHide is set, so it doesn't clear the saved "open" flag
    frame:SetFrameStrata("HIGH")
    frame:SetToplevel(true)
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:SetClampedToScreen(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
        SaveLayout()
    end)
    frame:SetResizable(true)
    if frame.SetResizeBounds then
        frame:SetResizeBounds(MIN_W, MIN_H, MAX_W, MAX_H)
    else
        frame:SetMinResize(MIN_W, MIN_H)
        frame:SetMaxResize(MAX_W, MAX_H)
    end
    RestoreLayout()
    frame:SetScript("OnUpdate", UpdateFade)
    frame:SetScript("OnShow", function()
        PlaySound(SOUNDKIT.IG_CHARACTER_INFO_OPEN)
        WoWZADB.open = true
        Render()
    end)
    frame:SetScript("OnHide", function()
        PlaySound(SOUNDKIT.IG_CHARACTER_INFO_CLOSE)
        WoWZADB.open = false
    end)
    tinsert(UISpecialFrames, "WoWZAFrame")
    SetFrameTitle(frame, "WoWZA")

    local inset = frame.Inset or frame
    if frame.Inset then
        inset:ClearAllPoints()
        inset:SetPoint("TOPLEFT", 10, -28)
        inset:SetPoint("BOTTOMRIGHT", -10, 86)
    end

    ns.Chat.Create(inset, EMPTY_TEXT)

    hint = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    hint:SetPoint("BOTTOMLEFT", 18, 64)
    hint:SetPoint("BOTTOMRIGHT", -18, 64)
    hint:SetJustifyH("LEFT")

    input = CreateFrame("EditBox", "WoWZAInput", frame, "InputBoxTemplate")
    input:SetPoint("BOTTOMLEFT", 20, 36)
    input:SetPoint("BOTTOMRIGHT", -16, 36)
    input:SetHeight(22)
    input:SetAutoFocus(false)
    input:SetMaxLetters(0) -- payloads are long; question length is capped in Ask()
    if input.SetMaxBytes then input:SetMaxBytes(0) end

    input:SetScript("OnEnterPressed", function(self)
        if state == "copy" then return end
        local text = self:GetText()
        self:SetText("")
        Ask(text)
    end)
    input:SetScript("OnKeyDown", function(self, key)
        if state == "copy" and key == "C" and (IsControlKeyDown() or (IsMetaKeyDown and IsMetaKeyDown())) then
            C_Timer.After(0, function() -- let the copy happen first
                if state ~= "copy" then return end
                self:SetText("")
                CommitOutgoing()
            end)
        end
    end)
    input:SetScript("OnTextChanged", function(self, userInput)
        if not userInput then return end
        local text = self:GetText()
        if text:find("WZ1A:", 1, true) then
            self:SetText("")
            HandleAnswer(text)
        elseif text:find("WZ1Q:", 1, true) and state ~= "copy" then
            self:SetText("")
            SetState("wait", "|cffff6b6bThe companion app hasn't picked up your question.|r "
                .. "Make sure it's running, then press Ctrl+V again.")
        elseif state == "copy" and text ~= outgoing.payload then
            outgoing = nil -- user started typing over the payload
            SetState("ask")
        end
    end)
    input:SetScript("OnEscapePressed", function(self)
        if state == "copy" then
            outgoing = nil
            self:SetText("")
            SetState("ask")
        end
        self:ClearFocus()
    end)

    local askBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    askBtn:SetSize(100, 22)
    askBtn:SetPoint("BOTTOMLEFT", 12, 8)
    askBtn:SetText("Ask")
    askBtn:SetScript("OnClick", function()
        if state == "copy" then
            input:SetFocus()
            input:HighlightText()
            return
        end
        local text = input:GetText()
        input:SetText("")
        Ask(text)
    end)

    local clearBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    clearBtn:SetSize(90, 22)
    clearBtn:SetPoint("BOTTOMRIGHT", -24, 8)
    clearBtn:SetText("Clear")
    clearBtn:SetScript("OnClick", function()
        wipe(WoWZADB.history)
        Render()
    end)

    CreateResizeGrip()

    SetState("ask")
end

local function Toggle()
    if frame:IsShown() then
        frame:Hide()
    else
        frame:Show()
        input:SetFocus()
    end
end

---------------------------------------------------------------------------
-- Minimap button (same look and behavior as LibDBIcon buttons)
---------------------------------------------------------------------------
local function UpdateMinimapPosition()
    local angle = math.rad(WoWZADB.minimap.angle)
    local x, y = math.cos(angle), math.sin(angle)
    local shape = GetMinimapShape and GetMinimapShape() or "ROUND"
    if shape ~= "ROUND" then -- square-ish minimaps: slide along the edge
        local m = math.max(math.abs(x), math.abs(y))
        x, y = x / m, y / m
    end
    local rx, ry = Minimap:GetWidth() / 2 + 5, Minimap:GetHeight() / 2 + 5
    minimapButton:ClearAllPoints()
    minimapButton:SetPoint("CENTER", Minimap, "CENTER", x * rx, y * ry)
end

local function CreateMinimapButton()
    local b = CreateFrame("Button", "WoWZAMinimapButton", Minimap)
    minimapButton = b
    b:SetSize(31, 31)
    b:SetFrameStrata("MEDIUM")
    b:SetFrameLevel(8)
    b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    b:RegisterForDrag("LeftButton")
    b:SetHighlightTexture("Interface\\Minimap\\UI-Minimap-ZoomButton-Highlight")

    local bg = b:CreateTexture(nil, "BACKGROUND")
    bg:SetSize(20, 20)
    bg:SetTexture("Interface\\Minimap\\UI-Minimap-Background")
    bg:SetPoint("TOPLEFT", 7, -5)

    local icon = b:CreateTexture(nil, "ARTWORK")
    icon:SetSize(18, 18)
    icon:SetTexture(ICON)
    icon:SetPoint("TOPLEFT", 7, -6)
    b.icon = icon

    local border = b:CreateTexture(nil, "OVERLAY")
    border:SetSize(53, 53)
    border:SetTexture("Interface\\Minimap\\MiniMap-TrackingBorder")
    border:SetPoint("TOPLEFT")

    b:SetScript("OnClick", function(_, button)
        if button == "RightButton" then
            WoWZADB.minimap.hide = true
            b:Hide()
            print("|cffffd100WoWZA:|r minimap button hidden. Type /za minimap to show it again.")
        else
            Toggle()
        end
    end)
    b:SetScript("OnDragStart", function(self)
        self:LockHighlight()
        self:SetScript("OnUpdate", function()
            local mx, my = Minimap:GetCenter()
            local cx, cy = GetCursorPosition()
            local scale = Minimap:GetEffectiveScale()
            WoWZADB.minimap.angle = math.deg(math.atan2(cy / scale - my, cx / scale - mx))
            UpdateMinimapPosition()
        end)
    end)
    b:SetScript("OnDragStop", function(self)
        self:SetScript("OnUpdate", nil)
        self:UnlockHighlight()
    end)
    b:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_LEFT")
        GameTooltip:AddLine("WoWZA")
        GameTooltip:AddLine("World of Warcraft Zone Advisor", 1, 0.82, 0)
        GameTooltip:AddLine("|cffffffffClick|r to open", 0.8, 0.8, 0.8)
        GameTooltip:AddLine("|cffffffffDrag|r to move", 0.8, 0.8, 0.8)
        GameTooltip:AddLine("|cffffffffRight-click|r to hide", 0.8, 0.8, 0.8)
        GameTooltip:Show()
    end)
    b:SetScript("OnLeave", function() GameTooltip:Hide() end)

    UpdateMinimapPosition()
    if WoWZADB.minimap.hide then b:Hide() end
end

---------------------------------------------------------------------------
-- Entry points: addon compartment, key binding, slash commands
---------------------------------------------------------------------------
function WoWZA_Toggle() Toggle() end
function WoWZA_OnAddonCompartmentClick() Toggle() end

BINDING_HEADER_WOWZA = "WoWZA"
BINDING_NAME_WOWZA_TOGGLE = "Toggle the WoWZA window"

SLASH_WOWZA1 = "/za"
SLASH_WOWZA2 = "/wowza"
SlashCmdList["WOWZA"] = function(msg)
    msg = strtrim(msg or "")
    if msg == "" then
        Toggle()
    elseif msg:lower() == "minimap" then
        WoWZADB.minimap.hide = not WoWZADB.minimap.hide
        minimapButton:SetShown(not WoWZADB.minimap.hide)
    elseif msg:lower():match("^fade") then
        local pct = tonumber(msg:match("(%d+)"))
        if pct then
            WoWZADB.fadeAlpha = math.max(10, math.min(100, pct)) / 100
        end
        print(string.format("|cffffd100WoWZA:|r opacity while moving is %d%%. "
            .. "Use /za fade 10-100 (100 = no fade).", WoWZADB.fadeAlpha * 100))
    elseif msg:lower() == "reset" then
        WoWZADB.layout = nil
        RestoreLayout()
    elseif msg:lower() == "status" then
        local yes = "|cff20ff20yes|r"
        local no = "|cffff6b6bno|r"
        print("|cffffd100WoWZA:|r answer slots installed: " .. (T.slots and yes or no)
            .. (T.slots and string.format(" (%d of %d left this session)", T.SlotsLeft(), T.SLOTS) or "")
            .. ";  instant 'answer ready' signals: "
            .. (T.signals and (yes .. " (" .. T.mode .. ")") or (no .. string.format(
                " (checking at about %ds instead)", math.max(5, math.min(30, T.TypicalSeconds() + 1)))))
            .. "  [" .. tostring(T.selfTest) .. "; sound " .. tostring(GetCVar and GetCVar("Sound_EnableAllSound")) .. "]")
    else
        frame:Show()
        Ask(msg)
    end
end

local loader = CreateFrame("Frame")
loader:RegisterEvent("ADDON_LOADED")
loader:RegisterEvent("PLAYER_STARTED_MOVING")
loader:RegisterEvent("PLAYER_STOPPED_MOVING")
loader:RegisterEvent("GET_ITEM_INFO_RECEIVED")
loader:RegisterEvent("PLAYER_LOGIN")
loader:SetScript("OnEvent", function(_, event, name)
    if event == "PLAYER_LOGIN" then
        T.Init()
        if not T.slots then
            print("|cffffd100WoWZA:|r answer slots not found, so answers need Ctrl+V. "
                .. "Click Install / update addon in the companion app, then fully restart WoW.")
        end
    elseif event == "GET_ITEM_INFO_RECEIVED" then
        OnItemInfo(name)
    elseif event == "PLAYER_STARTED_MOVING" then
        playerMoving = true
    elseif event == "PLAYER_STOPPED_MOVING" then
        playerMoving = false
    elseif name == ADDON then
        WoWZADB = WoWZADB or {}
        local db = WoWZADB
        db.outbox = nil -- from the old /reload bridge
        db.history = db.history or {}
        db.minimap = db.minimap or { hide = false, angle = 200 }
        db.fadeAlpha = db.fadeAlpha or DEFAULT_FADE_ALPHA
        CreateUI()
        CreateMinimapButton()
        if db.open then frame:Show() end
        loader:UnregisterEvent("ADDON_LOADED")
    end
end)
