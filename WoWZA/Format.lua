-- Turns the lightweight markup in answers into WoW text: chat-style links with icons, colors, bullets.
--   ## Heading            gold heading line
--   - item                bullet
--   **text**              highlighted
--   [[item:ID|Name]]      item link (ID optional), verified against the game's item data
--   [[quest:Name]]        quest link (matched against the quest log), [[spell:ID|Name]], [[npc:Name]], [[zone:Name]]
local _, ns = ...
local F = {}
ns.Format = F

F.pendingItems = {} -- itemID -> true while waiting for the server to send item data

local GetItemInfo = (C_Item and C_Item.GetItemInfo) or GetItemInfo

local COLOR = {
    heading = "|cffffd100",
    highlight = "|cffffffff",
    quest = "|cffffff00", -- quest links in chat
    spell = "|cff71d5ff", -- spell links in chat
    npc = "|cfffff3a0",
    zone = "|cff7fd6ff",
    item = "|cffffffff",
}
F.BODY_COLOR = { 0.88, 0.88, 0.88 } -- slightly off-white so highlights stand out

local function esc(s) return (s:gsub("|", "||")) end
local function same(a, b) return a and b and a:lower() == b:lower() end
local function icon(tex) return tex and ("|T" .. tex .. ":0|t") or "" end
local function plain(color, name) return color .. "[" .. esc(name) .. "]|r" end

local function itemFrom(query)
    local ok, name, link, _, _, _, _, _, _, _, tex = pcall(GetItemInfo, query)
    if ok and name then return name, link, tex end
end

local function ItemText(id, name)
    if id then
        local iname, link, tex = itemFrom(id)
        if iname then
            if name == "" or same(iname, name) then return icon(tex) .. link end
        else
            F.pendingItems[id] = true -- GetItemInfo asked the server; we re-render when it answers
        end
    end
    if name ~= "" then
        local _, link, tex = itemFrom(name) -- only works for items this client has seen
        if link then return icon(tex) .. link end
    end
    return plain(COLOR.item, name)
end

local function QuestText(id, name)
    local qid
    local ok = pcall(function()
        for i = 1, C_QuestLog.GetNumQuestLogEntries() do
            local info = C_QuestLog.GetInfo(i)
            if info and not info.isHeader and same(info.title, name) then
                qid = info.questID
                return
            end
        end
    end)
    qid = qid or id
    if qid and GetQuestLink then
        local okLink, link = pcall(GetQuestLink, qid)
        if okLink and link and (name == "" or same(link:match("%[(.-)%]"), name)) then return link end
    end
    return plain(COLOR.quest, name)
end

local function spellFrom(query)
    local ok, n, tex, sid = pcall(function()
        if C_Spell and C_Spell.GetSpellInfo then
            local info = C_Spell.GetSpellInfo(query)
            if info then return info.name, info.iconID, info.spellID end
        elseif GetSpellInfo then
            local sname, _, sicon, _, _, _, spellID = GetSpellInfo(query)
            return sname, sicon, spellID
        end
    end)
    if ok and n then return n, tex, sid end
end

local function SpellText(id, name)
    local n, tex, sid
    if id then
        n, tex, sid = spellFrom(id)
        if n and name ~= "" and not same(n, name) then n = nil end
    end
    if not n and name ~= "" then n, tex, sid = spellFrom(name) end
    if not n then return plain(COLOR.spell, name) end
    local getLink = (C_Spell and C_Spell.GetSpellLink) or GetSpellLink
    local ok, link = pcall(getLink, sid)
    if ok and link then return icon(tex) .. link end
    return icon(tex) .. plain(COLOR.spell, n)
end

local function Entity(kind, body)
    local idStr, name = body:match("^%s*(%d+)%s*|(.*)$")
    name = strtrim(name or body)
    local id = tonumber(idStr)
    kind = kind:lower()
    if kind == "item" then return ItemText(id, name) end
    if kind == "quest" then return QuestText(id, name) end
    if kind == "spell" then return SpellText(id, name) end
    if kind == "npc" then return COLOR.npc .. esc(name) .. "|r" end
    if kind == "zone" then return COLOR.zone .. esc(name) .. "|r" end
    return esc(name)
end

local function Inline(s)
    local tokens = {}
    s = s:gsub("%[%[(%a+):(.-)%]%]", function(kind, body)
        tokens[#tokens + 1] = Entity(kind, body)
        return "\001" .. #tokens .. "\002"
    end)
    s = esc(s)
    s = s:gsub("%*%*(.-)%*%*", COLOR.highlight .. "%1|r")
    s = s:gsub("\001(%d+)\002", function(i) return tokens[tonumber(i)] end)
    return s
end

-- Returns display lines for an answer.
function F.Lines(text)
    local out = {}
    for line in (text .. "\n"):gmatch("(.-)\r?\n") do
        local heading = line:match("^%s*#+%s*(.-)%s*$")
        local indent, bullet = line:match("^(%s*)[-*]%s+(.+)$")
        if heading and heading ~= "" then
            if #out > 0 and out[#out] ~= " " then out[#out + 1] = " " end
            out[#out + 1] = COLOR.heading .. Inline(heading) .. "|r"
        elseif bullet then
            out[#out + 1] = (#indent > 0 and "      " or "  ") .. "• " .. Inline(bullet)
        elseif line:match("^%s*$") then
            if #out > 0 and out[#out] ~= " " then out[#out + 1] = " " end
        else
            out[#out + 1] = Inline(line)
        end
    end
    while out[#out] == " " do out[#out] = nil end
    return out
end
