# EDChatOverlay

An [EDMC](https://github.com/EDCD/EDMarketConnector) plugin that shows live
in-game chat (Local, Wing, Direct/Friend, Squadron, System) as a scrolling
overlay on top of Elite Dangerous, with optional automatic translation of
incoming messages via [DeepL](https://www.deepl.com/).

Rendering is handled by
[EDMCModernOverlay](https://github.com/SweetJonnySauce/EDMCModernOverlay),
which must be installed as well — this plugin only reads the journal and
feeds it lines to draw; it doesn't draw anything itself.

## How it works

- `journal_entry()` is called by EDMC for every new `ReceiveText` (and,
  optionally, `SendText`) journal event as it happens — no polling, no
  separate journal reader.
- Each new message pushes onto a fixed-size scrolling log (newest at top,
  configurable line count) rendered via `edmcoverlay.Overlay().send_message(...)`.
  Long lines are word-wrapped rather than running off-screen.
- Messages are kept alive with a periodic heartbeat rather than a single
  persistent send (matches how other plugins in this ecosystem use the
  overlay API). An optional fade-out setting prunes messages older than a
  configurable number of seconds; 0 means they stay until pushed off by
  newer ones.
- The plugin registers itself with EDMCModernOverlay's Overlay Controller
  (as "EDChatOverlay") so its position can be adjusted there without code
  changes. If that registration fails for any reason, rendering still works
  exactly the same — it just won't show up in the Controller.
- On startup, EDMC replays the current session's journal to rebuild plugin
  state. Anything with a journal timestamp more than 30 seconds old is
  treated as backlog and silently skipped, so you don't get flooded with
  hours of old chat (and DeepL doesn't get hit translating all of it) the
  moment EDMC starts.
- When translation is enabled, each incoming message is translated in a
  background thread (so it never blocks journal processing) and the
  translation is appended as a second line under the original once it comes
  back. Messages already in the target language are left alone.

## Setup

1. Make sure [EDMCModernOverlay](https://github.com/SweetJonnySauce/EDMCModernOverlay)
   is installed and enabled in EDMC.
2. Download the latest `EDChatOverlay.zip` from the
   [Releases page](https://github.com/techno314/EDChatOverlay/releases) and
   extract it into EDMC's plugins directory (**File > Open Plugins Folder**
   in EDMC) — it extracts to an `EDChatOverlay` folder, which is exactly what
   EDMC expects to find there. 
3. Restart EDMC (or start it, if it wasn't running) to load the plugin.
4. Optional — translation: open EDMC's **File > Settings > EDChatOverlay**
   tab, paste in a [DeepL API key](https://www.deepl.com/your-account/keys)
   (a free-tier key works, it ends in `:fx`), pick a target language, and
   check **Auto-translate incoming messages**. Use **Test DeepL Key** to
   confirm it works before relying on it in-game.
5. Also in that Settings tab: choose which channels to show (Local, Wing,
   Direct/Friend, Squadron, System; NPC chatter is off by default), whether
   to show messages you send yourself (off by default, shown in gray), how
   many lines of history to keep on screen, and how long messages stay up
   before fading out (0 = never).

## Notes

- Messages you send are never translated, even if you enable "Show messages
  you send" — translation only applies to what other commanders send you.
- If EDMCModernOverlay isn't installed/loaded, the plugin still loads
  cleanly (EDMC's Settings will show "EDMCModernOverlay not found") — it
  just has nothing to draw to.
- DeepL's free tier is 500,000 characters/month, which is generous for chat
  text; a translation failure (bad key, quota, network) just logs a warning
  and leaves the original-language line on screen.
