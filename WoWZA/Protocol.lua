-- Clipboard protocol shared with the companion app. Pure Lua 5.1, no WoW API.
--   Question (WoW -> app):  WZ1Q:<base64 of JSON {id, q, ctx}>
--   Answer   (app -> WoW):  WZ1A:<id>:<status>:<base64 answer>:<base64 question>
-- Answers normally arrive through the slot addons (Transport.lua); the Ctrl+V answer is the fallback.
-- status is one of: pending, partial, done, error.
local _, ns = ...
local P = {}
ns.Protocol = P

local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local B64INV = {}
for i = 1, 64 do B64INV[B64:byte(i)] = i - 1 end

local function b64char(n) return B64:sub(n + 1, n + 1) end

local function b64encode(data)
    local out = {}
    for i = 1, #data, 3 do
        local a, b, c = data:byte(i, i + 2)
        local n = a * 65536 + (b or 0) * 256 + (c or 0)
        out[#out + 1] = b64char(math.floor(n / 262144) % 64)
            .. b64char(math.floor(n / 4096) % 64)
            .. (b and b64char(math.floor(n / 64) % 64) or "=")
            .. (c and b64char(n % 64) or "=")
    end
    return table.concat(out)
end

local function b64decode(s)
    s = s:gsub("[^%w%+/]", "")
    local out = {}
    for i = 1, #s, 4 do
        local n, k = 0, 0
        for j = i, math.min(i + 3, #s) do
            n = n * 64 + B64INV[s:byte(j)]
            k = k + 1
        end
        if k == 4 then
            out[#out + 1] = string.char(math.floor(n / 65536) % 256, math.floor(n / 256) % 256, n % 256)
        elseif k == 3 then
            n = n * 64
            out[#out + 1] = string.char(math.floor(n / 65536) % 256, math.floor(n / 256) % 256)
        elseif k == 2 then
            n = n * 4096
            out[#out + 1] = string.char(math.floor(n / 65536) % 256)
        end
    end
    return table.concat(out)
end

local JSON_ESC = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }

local function json(v)
    local t = type(v)
    if t == "table" then
        local parts = {}
        if #v > 0 or next(v) == nil then
            for i = 1, #v do parts[i] = json(v[i]) end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for k, val in pairs(v) do
            parts[#parts + 1] = json(tostring(k)) .. ":" .. json(val)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    elseif t == "string" then
        return '"' .. (v:gsub('[%c"\\]', function(c)
            return JSON_ESC[c] or string.format("\\u%04x", c:byte())
        end)) .. '"'
    elseif t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then return "null" end
        return tostring(v)
    elseif t == "boolean" then
        return tostring(v)
    end
    return "null"
end

-- sig: the signal number the companion raises for this question (see Transport.lua)
function P.EncodeQuestion(id, q, ctx, sig)
    return "WZ1Q:" .. b64encode(json({ id = id, q = q, ctx = ctx, sig = sig }))
end

function P.DecodeAnswer(text)
    local id, status, a, q = text:match("WZ1A:([%w%-]+):(%a+):([%w%+/=]*):?([%w%+/=]*)")
    if not id then return nil end
    return { id = id, status = status, answer = b64decode(a), q = b64decode(q or "") }
end

-- exposed for tests
P._b64encode, P._b64decode, P._json = b64encode, b64decode, json
