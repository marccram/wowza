# WoWZA: World of Warcraft Zone Advisor (WoW Forever)

Ask an AI about your zone, quests, class, talents, rotation and leveling, and read the answer inside WoW.
Each question is sent with a snapshot of your character: level, class, talents, gear, skills, zone,
coordinates and quest log.

## Using it

1. Run the companion app (**WoWZA.exe**, or `python companion/app.py`).
2. In WoW, click the **WoWZA minimap button**. You can also type `/za` (or `/wowza`), use the addon menu,
   or set a key binding under Options → Keybindings → AddOns.
3. Type a question and press **Enter**. The box fills with a code that's already selected. You can also
   ask straight from chat: `/za where do I find Crisp Spider Meat?`
4. Press **Ctrl+C**. The window shows "WoWZA is thinking" once the companion has it.
5. The answer appears in the WoW window by itself (usually 5-15 seconds), with the whisper sound. If the
   window is closed, a chat line tells you it's ready.

You never have to reload. After installing or updating with new files, fully restart WoW once.
`/za status` shows whether automatic delivery is working. If it isn't, answers still work by
pressing **Ctrl+V** in the box. Minimap button: drag to move, right-click to hide, `/za minimap` to show it again.

The window can be moved (drag the title) and resized (drag the bottom-right corner), and it remembers both.
Like the world map, it fades while you move, but stays solid while your mouse is over it or you're typing.
`/za fade 70` sets the opacity while moving (100 turns fading off, default 50), and `/za reset` restores
the default size and position.

### How the answer gets back without a reload

WoW addons can't use the network or open files. Two things still cross the boundary:

- **Out, the clipboard:** your Ctrl+C. It's the same mechanism WeakAuras and Plater use for import strings.
- **In, files the game reads on first use:** WoW reads a file from disk the first time it's used, if the file
  existed when the game started. The companion creates 100 small load-on-demand "answer slot" addons
  (`WoWZA_S001`–`S100`, shown in your AddOns list; leave them enabled) and writes each answer into
  them, and the addon loads a fresh one. To know when to load one, it checks empty sound files that the
  companion fills in when your question arrives and when the answer is ready. That's why a full restart is
  needed after install, and why a session has 100 answers before a `/reload` frees the slots.

Nothing reads the game's memory or screen, and nothing presses keys for you. The slot technique was measured
on the Forever client by [wow-ai](https://github.com/chelinho139/wow-ai) (MIT).

## Setup (first run)

The companion opens Settings automatically:

1. Check the **WoW game folder** (usually auto-detected) and click **Install / update addon**.
2. Pick an **AI provider** and follow the steps shown under it. **Test connection** should say Working.

| Option | Cost | What you need |
|---|---|---|
| Claude subscription (default) | Included in Claude Pro or Max | Claude Code, or the Claude desktop app's Code tab, signed in (not available on the free plan) |
| Claude API | About 1-3¢ per question | An Anthropic API key with credit |
| Google Gemini | Free, with daily limits | A free Google AI Studio key (free-tier prompts may be used by Google) |
| Local (Ollama) | Free, private | A decent GPU; weaker WoW knowledge |
| Other (OpenAI-compatible) | Varies | OpenRouter, Groq, LM Studio, etc. |

Settings and keys are saved only on your PC, in `%APPDATA%\WoWZA`. Nothing secret lives in the addon
or this folder, so it is safe to share.

## Quest data (QuestieDB)

With quest data downloaded (the companion offers it on first run, or Settings → **Download quest data**,
about 7 MB), each question is checked against [QuestieDB](https://github.com/Questie/QuestieDB)'s Forever
data: the quests, items, NPCs and objects you name. Claude then gets the real facts: quest givers and
objectives, which mobs drop an item and how often, vendors, mob levels and rare spawns, and spawn
coordinates with the spot nearest to you. It doesn't cover skinning, gathering or crafting.

The data is downloaded on each PC from QuestieDB's GitHub and isn't bundled with the app; QuestieDB
publishes no license file. Questie and QuestieDB are by the Questie team.

## Upgrading from Claude Advisor

WoWZA was called Claude Advisor (`/claude`). The first time the new companion starts, it moves its settings,
history and quest data to `%APPDATA%\WoWZA`, replaces the old shortcuts, swaps the `ClaudeAdvisor` addon and
its answer slots for `WoWZA` ones (your in-game history and window layout come along), and asks you to fully
restart WoW. A key binding set for the old name needs setting again.

## Development

- `companion/`: the desktop app (tkinter). `pip install -r requirements.txt`, then `python app.py`.
- `WoWZA/`: the addon. `Protocol.lua` is the clipboard format, `Transport.lua` the slot and signal
  delivery, `Format.lua` the answer formatting, `Core.lua` the window, context and minimap button.
- `tools/test_addon.py` runs the addon against a mock WoW API (needs `pip install lupa`).
- `tools/test_questdata.py` checks the QuestieDB lookup against known Forever facts (needs the data downloaded).
- `companion/questdata.py`: downloads QuestieDB, builds `%APPDATA%\WoWZA\questiedb\index.pickle`, matches names
  in questions and writes the game-data block Claude receives.
- `tools/make_icon.py` regenerates `icon.tga`.
- `companion\build.ps1` builds a single `WoWZA.exe` with the addon bundled, for sharing.
- `## Interface: 16001` in the `.toc` matches client 1.60.1. If WoW says the addon is out of date, run
  `/dump (select(4, GetBuildInfo()))` in game and use that number.
