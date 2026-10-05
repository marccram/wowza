-- Chat view: the conversation as message bubbles, like a texting or AI chat app. The player's
-- questions sit on the right next to their portrait; WoWZA's answers sit on the left next to its icon.
local _, ns = ...
local C = {}
ns.Chat = C

local ICON = "Interface\\AddOns\\WoWZA\\icon"
local AVATAR = 28       -- avatar size
local EDGE = 4          -- avatar to the edge of the view
local GAP = 6           -- avatar to bubble
local PAD_X, PAD_Y = 10, 7
local SPACING = 10      -- between messages
local MAX_SHARE = 0.80  -- of the remaining width a bubble may use

local STYLE = {
    player = { bg = { 0.22, 0.45, 0.75, 0.30 }, border = { 0.40, 0.62, 0.95, 0.55 }, text = { 1, 1, 1 } },
    wowza = { bg = { 0.55, 0.40, 0.18, 0.22 }, border = { 0.85, 0.66, 0.32, 0.45 }, text = ns.Format.BODY_COLOR },
}
local BACKDROP = {
    bgFile = "Interface\\Buttons\\WHITE8X8",
    edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
    edgeSize = 12,
    insets = { left = 3, right = 3, top = 3, bottom = 3 },
}

local scroll, content, empty
local rows = {}
local relayoutQueued = false

-- Links in answers behave like links in chat: hover for the tooltip, click to pin it, shift-click to link.
local function OnLinkEnter(self, link)
    GameTooltip:SetOwner(self, "ANCHOR_CURSOR")
    if pcall(GameTooltip.SetHyperlink, GameTooltip, link) then GameTooltip:Show() end
end
local function OnLinkLeave() GameTooltip:Hide() end
local function OnLinkClick(self, link, text, button) SetItemRef(link, text, button, self) end

local function NewRow()
    local row = CreateFrame("Frame", nil, content)
    row.avatar = row:CreateTexture(nil, "ARTWORK")
    row.avatar:SetSize(AVATAR, AVATAR)
    row.bubble = CreateFrame("Frame", nil, row, "BackdropTemplate")
    row.bubble:SetBackdrop(BACKDROP)
    row.text = row.bubble:CreateFontString(nil, "OVERLAY", "ChatFontNormal")
    row.text:SetPoint("TOPLEFT", PAD_X, -PAD_Y)
    row.text:SetJustifyH("LEFT")
    row.text:SetJustifyV("TOP")
    row.text:SetWordWrap(true)
    row.text:SetNonSpaceWrap(true)
    pcall(row.bubble.SetHyperlinksEnabled, row.bubble, true)
    pcall(row.bubble.SetScript, row.bubble, "OnHyperlinkEnter", OnLinkEnter)
    pcall(row.bubble.SetScript, row.bubble, "OnHyperlinkLeave", OnLinkLeave)
    pcall(row.bubble.SetScript, row.bubble, "OnHyperlinkClick", OnLinkClick)
    return row
end

-- Sizes and places one message; returns its height.
local function Layout(row, item, y, width)
    local style = STYLE[item.role] or STYLE.wowza
    local maxText = math.floor((width - AVATAR - GAP - EDGE) * MAX_SHARE) - 2 * PAD_X
    row.text:SetWidth(maxText)
    row.text:SetText(item.text)
    local textWidth = maxText
    if not item.text:find("\n", 1, true) then -- short single-line messages shrink to fit
        local natural = row.text:GetUnboundedStringWidth()
        if natural and natural < maxText then
            textWidth = math.ceil(natural) + 1
            row.text:SetWidth(textWidth)
        end
    end
    local height = math.max((row.text:GetStringHeight() or 14) + 2 * PAD_Y, AVATAR)
    local color = item.color or style.text
    row.text:SetTextColor(color[1], color[2], color[3])

    row.bubble:SetSize(textWidth + 2 * PAD_X, height)
    row.bubble:SetBackdropColor(unpack(style.bg))
    row.bubble:SetBackdropBorderColor(unpack(style.border))

    row:ClearAllPoints()
    row:SetPoint("TOPLEFT", content, "TOPLEFT", 0, -y)
    row:SetSize(width, height)
    row.avatar:ClearAllPoints()
    row.bubble:ClearAllPoints()
    if item.role == "player" then
        if not pcall(SetPortraitTexture, row.avatar, "player") then row.avatar:SetTexture(ICON) end
        row.avatar:SetPoint("TOPRIGHT", row, "TOPRIGHT", -EDGE, 0)
        row.bubble:SetPoint("TOPRIGHT", row.avatar, "TOPLEFT", -GAP, 0)
    else
        row.avatar:SetTexture(ICON)
        row.avatar:SetPoint("TOPLEFT", row, "TOPLEFT", EDGE, 0)
        row.bubble:SetPoint("TOPLEFT", row.avatar, "TOPRIGHT", GAP, 0)
    end
    row:Show()
    return height
end

-- items: { { role = "player" | "wowza", text = "...", color = {r, g, b} (optional) }, ... }
function C.Render(items, keepScroll)
    if not scroll then return end
    C.items = items
    local width = scroll:GetWidth()
    if not width or width < 100 then width = 400 end
    content:SetWidth(width)
    local previous = scroll:GetVerticalScroll() or 0

    empty:SetWidth(width - 40)
    empty:SetShown(#items == 0)
    local y = 8
    for i, item in ipairs(items) do
        rows[i] = rows[i] or NewRow()
        y = y + Layout(rows[i], item, y, width) + SPACING
    end
    for i = #items + 1, #rows do rows[i]:Hide() end
    content:SetHeight(math.max(y, empty:IsShown() and 60 or 1))

    C_Timer.After(0.05, function() -- the scroll range updates after layout
        local range = scroll:GetVerticalScrollRange() or 0
        scroll:SetVerticalScroll(keepScroll and math.min(previous, range) or range)
    end)
end

function C.Create(parent, emptyText)
    scroll = CreateFrame("ScrollFrame", "WoWZAChatScroll", parent, "UIPanelScrollFrameTemplate")
    scroll:SetPoint("TOPLEFT", 4, -6)
    scroll:SetPoint("BOTTOMRIGHT", -28, 6)
    content = CreateFrame("Frame", nil, scroll)
    content:SetSize(400, 1)
    scroll:SetScrollChild(content)

    empty = content:CreateFontString(nil, "OVERLAY", "GameFontDisable")
    empty:SetPoint("TOP", content, "TOP", 0, -24)
    empty:SetJustifyH("CENTER")
    empty:SetText(emptyText)

    -- Reflow when the window is resized.
    scroll:HookScript("OnSizeChanged", function()
        if relayoutQueued or not C.items then return end
        relayoutQueued = true
        C_Timer.After(0, function()
            relayoutQueued = false
            C.Render(C.items, true)
        end)
    end)
    return scroll
end
