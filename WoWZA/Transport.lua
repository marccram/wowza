-- Automatic answer delivery: no /reload and no Ctrl+V.
--
-- WoW reads a file from disk the first time it's used, as long as the file existed when the
-- client launched. This was measured on the Forever client by wow-ai
-- (github.com/chelinho139/wow-ai, MIT) and wow-forever-codex; the design below follows theirs.
--
--  * Answer slots: WoWZA_S001..S100 are load-on-demand addons. The companion writes the
--    latest answers into every slot's Inbox.lua; loading a fresh slot reads them. Each slot can
--    be loaded once per UI session (a /reload frees them all).
--  * Signals: the companion fills ack\NNN.wav when it picks up question NNN and sig\NNN.wav once
--    the answer is in the slots, so we know when a slot is worth loading. Two ways to read one:
--      "willplay": PlaySoundFile says an empty file won't play and a valid one will.
--      "playing":  some clients say every file will play; then we start it and ask
--                  C_Sound.IsPlaying a moment later. An empty file never really plays.
--    A filled file stays filled until WoW restarts, so a number that already reads as filled
--    can't be reused this session. Without working signals we poll on a learned schedule.
local _, ns = ...
local T = {}
ns.Transport = T

T.SLOTS = 100
T.mode = nil      -- "willplay", "playing", or nil (no signals: timed polls)
T.signals = false -- T.mode ~= nil
T.slots = false   -- slot addons were present when WoW launched
T.selfTest = "not run"

local BASE = "Interface\\AddOns\\WoWZA\\"
local TICK = 0.5
local PROBE_DELAY = 0.15 -- the valid file plays for a second; an empty one stops at once
local TIMEOUT = 180
local ACK_GRACE = 8      -- seconds without an ack before we check whether the companion has it
local SAFETY_POLLS = { 45, 90, 150 } -- with signals, in case one is missed
local nextSlot = 1

local IsLoaded = (C_AddOns and C_AddOns.IsAddOnLoaded) or IsAddOnLoaded
local Load = (C_AddOns and C_AddOns.LoadAddOn) or LoadAddOn
local GetInfo = (C_AddOns and C_AddOns.GetAddOnInfo) or GetAddOnInfo

local function SlotName(i) return string.format("WoWZA_S%03d", i) end
local function SignalPath(kind, n) return string.format("%s%s\\%03d.wav", BASE, kind, n) end

local function WillPlay(path)
    local ok, willPlay, handle = pcall(PlaySoundFile, path, "Master")
    if ok and willPlay and handle then pcall(StopSound, handle) end
    return (ok and willPlay) and true or false
end

local function IsReallyPlaying(path, cb)
    local ok, willPlay, handle = pcall(PlaySoundFile, path, "Master")
    if not (ok and willPlay and handle) then return cb(false) end
    C_Timer.After(PROBE_DELAY, function()
        local okp, playing = pcall(C_Sound.IsPlaying, handle)
        pcall(StopSound, handle)
        cb((okp and playing) and true or false)
    end)
end

-- Reads one signal file with the current mode; cb(raised) may run now or a moment later.
local function Check(path, cb)
    if T.mode == "willplay" then return cb(WillPlay(path)) end
    if T.mode == "playing" then return IsReallyPlaying(path, cb) end
    cb(false)
end

function T.SignalNumber(seq) return ((seq - 1) % T.SLOTS) + 1 end

-- When the companion's answers usually take; used to time polls without signals.
function T.TypicalSeconds()
    local took = WoWZADB.took or {}
    if #took == 0 then return 12 end
    local sorted = {}
    for i, v in ipairs(took) do sorted[i] = v end
    table.sort(sorted)
    return sorted[math.ceil(#sorted / 2)]
end

function T.RecordTook(seconds)
    if type(seconds) ~= "number" then return end
    local took = WoWZADB.took or {}
    took[#took + 1] = seconds
    while #took > 7 do table.remove(took, 1) end
    WoWZADB.took = took
end

local function PollSchedule()
    local first = math.max(5, math.min(30, T.TypicalSeconds() + 1))
    local s = {}
    for _, extra in ipairs({ 0, 4, 9, 15, 23, 35, 55, 85, 120 }) do s[#s + 1] = first + extra end
    return s
end

function T.Init()
    local ok, name, _, _, _, reason = pcall(GetInfo, SlotName(1))
    T.slots = ok and name ~= nil and reason ~= "MISSING"

    T.mode, T.signals = nil, false
    if type(PlaySoundFile) ~= "function" then
        T.selfTest = "no PlaySoundFile"
        return
    end
    local empty, valid = WillPlay(BASE .. "ctl\\empty.wav"), WillPlay(BASE .. "ctl\\valid.wav")
    T.selfTest = string.format("willplay: empty=%s valid=%s", tostring(empty), tostring(valid))
    if valid and not empty then
        T.mode, T.signals = "willplay", true
        return
    end
    if not (C_Sound and C_Sound.IsPlaying) or WoWZADB.playingBroken then
        T.selfTest = T.selfTest .. (WoWZADB.playingBroken and "; playing: off (didn't work before)" or "")
        return
    end
    IsReallyPlaying(BASE .. "ctl\\empty.wav", function(e)
        IsReallyPlaying(BASE .. "ctl\\valid.wav", function(v)
            T.selfTest = T.selfTest .. string.format("; playing: empty=%s valid=%s", tostring(e), tostring(v))
            if v and not e then T.mode, T.signals = "playing", true end
        end)
    end)
end

-- The signal turned out not to work in practice (the companion had the question, but its ack
-- never showed). Stop using it this session, and don't try "playing" again.
function T.SignalsFailed()
    if T.mode == "playing" then WoWZADB.playingBroken = true end
    T.selfTest = (T.selfTest or "") .. "; failed in use"
    T.mode, T.signals = nil, false
end

function T.SlotsLeft()
    local left = 0
    for i = nextSlot, T.SLOTS do
        if not IsLoaded(SlotName(i)) then left = left + 1 end
    end
    return left
end

-- Decides whether signal number n can be used for a new question; call before the companion
-- could see it. Sets opts.useSignals (possibly a moment later).
function T.PrepareSignal(n, opts)
    opts.useSignals = false
    if not T.signals then return end
    Check(SignalPath("sig", n), function(sigRaised)
        Check(SignalPath("ack", n), function(ackRaised)
            opts.useSignals = T.signals and not sigRaised and not ackRaised
        end)
    end)
end

-- Loads the next unused slot and returns what the companion wrote there.
local function LoadNextSlot()
    while nextSlot <= T.SLOTS do
        local name = SlotName(nextSlot)
        nextSlot = nextSlot + 1
        if not IsLoaded(name) then
            WoWZASlotData = nil
            local loaded, reason = Load(name)
            if loaded then return WoWZASlotData end
            if reason == "MISSING" or reason == "DISABLED" or reason == "CORRUPT" then
                T.slots = false
                return nil
            end
        end
    end
    return nil -- pool used up until the next /reload
end

-- Watches for the answer to one question.
--   n: its signal number; opts.useSignals: set by PrepareSignal
--   cb.onData(data) -> true once the answer is final (called after each slot load)
--   cb.received() -> true once the slots show the companion has the question
--   cb.onTick(elapsed, acked) -> true to stop watching; cb.onGiveUp(reason)
function T.Watch(n, opts, cb)
    local w = { started = GetTime(), acked = false, sigSeen = false, busy = false,
                polls = 1, safety = 1, verified = false, schedule = PollSchedule() }

    local function load()
        local data = T.slots and LoadNextSlot()
        return type(data) == "table" and cb.onData(data)
    end

    local function step(elapsed, ackRaised, sigRaised)
        if ackRaised then w.acked = true end
        local useSignals = opts.useSignals and T.signals
        local due = false
        if useSignals and sigRaised and not w.sigSeen then
            w.sigSeen = true -- stays raised now; later polls use the schedule
            due = true
        elseif useSignals and not w.sigSeen then
            if not w.acked and not w.verified and elapsed > ACK_GRACE then
                -- No ack yet: is the companion down, or is the signal not getting through?
                w.verified = true
                if load() then return true end
                if cb.received() then
                    T.SignalsFailed()
                    opts.useSignals = false
                end
            elseif SAFETY_POLLS[w.safety] and elapsed >= SAFETY_POLLS[w.safety] then
                due, w.safety = true, w.safety + 1
            end
        elseif w.schedule[w.polls] and elapsed >= w.schedule[w.polls] then
            due = true
            while w.schedule[w.polls] and w.schedule[w.polls] <= elapsed do w.polls = w.polls + 1 end
        end
        if due and load() then return true end
        if cb.onTick(elapsed, w.acked) then return true end
        if not T.slots or elapsed > TIMEOUT or nextSlot > T.SLOTS then
            cb.onGiveUp(not T.slots and "slots" or (nextSlot > T.SLOTS and "exhausted" or "timeout"))
            return true
        end
    end

    w.ticker = C_Timer.NewTicker(TICK, function()
        if w.busy then return end
        local elapsed = GetTime() - w.started
        local function finish(ackRaised, sigRaised)
            w.busy = false
            if step(elapsed, ackRaised, sigRaised) then w.ticker:Cancel() end
        end
        if not (opts.useSignals and T.signals) or w.sigSeen then return finish(false, false) end
        w.busy = true
        local function checkSig(ackRaised)
            Check(SignalPath("sig", n), function(sigRaised) finish(ackRaised, sigRaised) end)
        end
        if w.acked then return checkSig(true) end
        Check(SignalPath("ack", n), checkSig)
    end)
    return w
end
