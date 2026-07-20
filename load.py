"""EDMC plugin entry point: EDChatOverlay.

Streams Elite Dangerous chat (ReceiveText/SendText journal events) onto the
EDMCModernOverlay overlay, with optional DeepL auto-translation of incoming
messages from other commanders.
"""

import logging
import threading
import tkinter as tk
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import config
import myNotebook as nb

import chat_overlay
import deepl_client

PLUGIN_NAME = "EDChatOverlay"
logger = logging.getLogger(f"EDMC.{PLUGIN_NAME}")

# EDMC replays the current session's journal on startup to rebuild plugin
# state, which would otherwise dump hours of old chat into the overlay (and
# spam DeepL) all at once. Anything whose journal timestamp is older than
# this many seconds is treated as backlog and skipped.
_BACKLOG_THRESHOLD_SEC = 30

_CHANNEL_PREF_KEYS = {
    chat_overlay.CHANNEL_LOCAL: "edchat_show_local",
    chat_overlay.CHANNEL_WING: "edchat_show_wing",
    chat_overlay.CHANNEL_FRIEND: "edchat_show_friend",
    chat_overlay.CHANNEL_SQUADRON: "edchat_show_squadron",
    chat_overlay.CHANNEL_SYSTEM: "edchat_show_system",
    chat_overlay.CHANNEL_NPC: "edchat_show_npc",
}
_CHANNEL_PREF_DEFAULTS = {
    chat_overlay.CHANNEL_LOCAL: True,
    chat_overlay.CHANNEL_WING: True,
    chat_overlay.CHANNEL_FRIEND: True,
    chat_overlay.CHANNEL_SQUADRON: True,
    chat_overlay.CHANNEL_SYSTEM: True,
    chat_overlay.CHANNEL_NPC: False,
}

_overlay: Optional["chat_overlay.ChatOverlay"] = None
_prefs_vars: Dict[str, Any] = {}


def _bool_setting(key: str, default: bool) -> bool:
    val = config.get_bool(key)
    return default if val is None else bool(val)


def _config_defaults():
    # String/int settings: initialise once on first run, same pattern other
    # installed plugins on this system (e.g. fcoc) use for config.get_list.
    if not config.get_str("edchat_target_lang"):
        config.set("edchat_target_lang", "EN-US")
    if not config.get_int("edchat_max_lines"):
        config.set("edchat_max_lines", 8)
    # EDMC's config.get_bool() returns False (not None) for a never-set key,
    # so it can't distinguish "unset" from "explicitly turned off" -- a
    # dedicated one-time sentinel is needed to seed real defaults exactly
    # once, rather than re-deriving a default on every read.
    if not config.get_bool("edchat_channels_initialized"):
        for channel, key in _CHANNEL_PREF_KEYS.items():
            config.set(key, _CHANNEL_PREF_DEFAULTS[channel])
        config.set("edchat_channels_initialized", True)


def _channel_enabled(channel: str) -> bool:
    key = _CHANNEL_PREF_KEYS.get(channel)
    if key is None:
        return True
    return _bool_setting(key, _CHANNEL_PREF_DEFAULTS.get(channel, True))


def _is_backlog(entry: Dict[str, Any]) -> bool:
    ts = entry.get("timestamp")
    if not ts:
        return False
    try:
        event_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - event_time).total_seconds()
    return age > _BACKLOG_THRESHOLD_SEC


def _maybe_translate(msg_id: int, text: str):
    api_key = config.get_str("edchat_deepl_api_key")
    if not api_key:
        return
    target_lang = config.get_str("edchat_target_lang") or "EN-US"
    try:
        translated = deepl_client.translate(api_key, text, target_lang)
    except deepl_client.DeepLError as exc:
        logger.warning(f"DeepL translation failed: {exc}")
        return
    if translated.strip().lower() == text.strip().lower():
        return  # already in the target language
    if _overlay:
        _overlay.set_translation(msg_id, translated)


def journal_entry(cmdr, is_beta, system, station, entry, state):
    if entry.get("event") != "ReceiveText":
        return
    if _overlay is None or not _overlay.available:
        return
    if _is_backlog(entry):
        return

    channel = chat_overlay.canonical_channel(entry.get("Channel"))
    if not _channel_enabled(channel):
        return
    who = entry.get("From_Localised") or entry.get("From") or "?"
    text = entry.get("Message_Localised") or entry.get("Message") or ""
    if not text:
        return
    msg = _overlay.push(channel, who, text)
    if _bool_setting("edchat_translate_enabled", False) and channel != chat_overlay.CHANNEL_NPC:
        threading.Thread(target=_maybe_translate, args=(msg.id, text), daemon=True).start()


def plugin_start3(plugin_dir: str) -> str:
    global _overlay
    _config_defaults()
    _overlay = chat_overlay.ChatOverlay(
        max_messages=config.get_int("edchat_max_lines") or 8,
        fade_seconds=config.get_int("edchat_fade_seconds") or 0,
    )
    if not _overlay.available:
        logger.warning("EDMCModernOverlay plugin not found/loaded; install it for the chat overlay to appear.")
    return PLUGIN_NAME


def plugin_stop() -> None:
    if _overlay:
        _overlay.shutdown()


def plugin_app(parent: tk.Frame):
    frame = nb.Frame(parent)
    status = "overlay ready" if (_overlay and _overlay.available) else "EDMCModernOverlay not found"
    nb.Label(frame, text=f"{PLUGIN_NAME}: {status}").grid(row=0, column=0, sticky="w")
    return frame


def plugin_prefs(parent, cmdr, is_beta):
    # EDMC's Settings dialog requires plugin_prefs to return an nb.Frame
    # (myNotebook's themed widgets), not a plain ttk.Frame -- otherwise it
    # raises TypeError and silently drops the tab.
    frame = nb.Frame(parent)
    frame.columnconfigure(1, weight=1)
    _config_defaults()
    _prefs_vars.clear()

    row = 0
    nb.Label(frame, text="DeepL API key:").grid(row=row, column=0, sticky="w", padx=6, pady=4)
    api_key_var = tk.StringVar(value=config.get_str("edchat_deepl_api_key") or "")
    nb.EntryMenu(frame, textvariable=api_key_var, show="*", width=40).grid(row=row, column=1, sticky="we", padx=6, pady=4)
    _prefs_vars["deepl_api_key"] = api_key_var
    row += 1

    nb.Label(frame, text="Target language:").grid(row=row, column=0, sticky="w", padx=6, pady=4)
    lang_codes = (
        "EN-US", "EN-GB", "DE", "FR", "ES", "IT", "PT-BR", "PT-PT", "RU", "JA", "ZH",
        "NL", "PL", "TR", "KO", "UK", "SV", "DA", "FI", "NB", "CS", "EL", "RO", "HU",
    )
    lang_var = tk.StringVar(value=config.get_str("edchat_target_lang") or "EN-US")
    other_codes = [c for c in lang_codes if c != lang_var.get()]
    nb.OptionMenu(frame, lang_var, lang_var.get(), *other_codes).grid(row=row, column=1, sticky="w", padx=6, pady=4)
    _prefs_vars["target_lang"] = lang_var
    row += 1

    translate_var = tk.BooleanVar(value=_bool_setting("edchat_translate_enabled", False))
    nb.Checkbutton(frame, text="Auto-translate incoming messages", variable=translate_var).grid(
        row=row, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 10)
    )
    _prefs_vars["translate_enabled"] = translate_var
    row += 1

    nb.Label(frame, text="Lines to show:").grid(row=row, column=0, sticky="w", padx=6, pady=4)
    line_choices = (3, 4, 5, 6, 8, 10, 12, 15, 20)
    lines_var = tk.IntVar(value=config.get_int("edchat_max_lines") or 8)
    other_line_choices = [n for n in line_choices if n != lines_var.get()]
    nb.OptionMenu(frame, lines_var, lines_var.get(), *other_line_choices).grid(row=row, column=1, sticky="w", padx=6, pady=4)
    _prefs_vars["max_lines"] = lines_var
    row += 1

    nb.Label(frame, text="Fade out after:").grid(row=row, column=0, sticky="w", padx=6, pady=4)
    fade_choices = (0, 15, 30, 60, 120, 300)
    fade_var = tk.IntVar(value=config.get_int("edchat_fade_seconds") or 0)
    other_fade_choices = [n for n in fade_choices if n != fade_var.get()]
    nb.OptionMenu(frame, fade_var, fade_var.get(), *other_fade_choices).grid(row=row, column=1, sticky="w", padx=6, pady=4)
    _prefs_vars["fade_seconds"] = fade_var
    row += 1
    nb.Label(frame, text="Seconds; 0 = never (messages stay until pushed off by new ones).").grid(
        row=row, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 10)
    )
    row += 1

    nb.Label(frame, text="Channels shown:").grid(row=row, column=0, sticky="nw", padx=6, pady=4)
    channels_frame = nb.Frame(frame)
    channels_frame.grid(row=row, column=1, sticky="w", padx=6, pady=4)
    channel_labels = [
        ("Local", chat_overlay.CHANNEL_LOCAL), ("Wing", chat_overlay.CHANNEL_WING),
        ("Direct/Friend", chat_overlay.CHANNEL_FRIEND), ("Squadron", chat_overlay.CHANNEL_SQUADRON),
        ("System", chat_overlay.CHANNEL_SYSTEM), ("NPC", chat_overlay.CHANNEL_NPC),
    ]
    for i, (label, channel) in enumerate(channel_labels):
        key = _CHANNEL_PREF_KEYS[channel]
        var = tk.BooleanVar(value=_bool_setting(key, _CHANNEL_PREF_DEFAULTS[channel]))
        nb.Checkbutton(channels_frame, text=label, variable=var).grid(row=i // 3, column=i % 3, sticky="w", padx=4)
        _prefs_vars[key] = var
    row += 1

    result_var = tk.StringVar(value="")
    nb.Label(frame, textvariable=result_var).grid(row=row, column=0, columnspan=2, sticky="w", padx=6, pady=4)

    def _test_key():
        key = api_key_var.get().strip()
        if not key:
            result_var.set("Enter an API key first.")
            return
        result_var.set("Testing...")

        def _run():
            try:
                translated = deepl_client.translate(key, "Hello, Commander.", lang_var.get())
                result_var.set(f'OK: "{translated}"')
            except deepl_client.DeepLError as exc:
                result_var.set(f"Failed: {exc}")

        threading.Thread(target=_run, daemon=True).start()

    row += 1
    nb.Button(frame, text="Test DeepL Key", command=_test_key).grid(row=row, column=0, sticky="w", padx=6, pady=(4, 8))

    return frame


def prefs_changed(cmdr, is_beta) -> None:
    if not _prefs_vars:
        return
    config.set("edchat_deepl_api_key", _prefs_vars["deepl_api_key"].get().strip())
    config.set("edchat_target_lang", _prefs_vars["target_lang"].get())
    config.set("edchat_translate_enabled", _prefs_vars["translate_enabled"].get())
    max_lines = max(1, _prefs_vars["max_lines"].get())
    config.set("edchat_max_lines", max_lines)
    fade_seconds = max(0, _prefs_vars["fade_seconds"].get())
    config.set("edchat_fade_seconds", fade_seconds)
    for channel, key in _CHANNEL_PREF_KEYS.items():
        if key in _prefs_vars:
            config.set(key, _prefs_vars[key].get())
    if _overlay:
        _overlay.set_max_messages(max_lines)
        _overlay.set_fade_seconds(fade_seconds)
