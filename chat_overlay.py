"""Renders a scrolling chat log onto the EDMCModernOverlay canonical canvas."""

import logging
import os
import sys
import textwrap
import threading
import time
from collections import deque
from itertools import count
from typing import Optional

logger = logging.getLogger("EDMC.EDChatOverlay")

CHANNEL_LOCAL = "local"
CHANNEL_WING = "wing"
CHANNEL_FRIEND = "friend"
CHANNEL_SQUADRON = "squadron"
CHANNEL_SYSTEM = "starsystem"
CHANNEL_NPC = "npc"
CHANNEL_VOICE = "voicechat"

CHANNEL_LABELS = {
    CHANNEL_LOCAL: "Local",
    CHANNEL_WING: "Wing",
    CHANNEL_FRIEND: "Direct",
    CHANNEL_SQUADRON: "Squadron",
    CHANNEL_SYSTEM: "System",
    CHANNEL_NPC: "NPC",
    CHANNEL_VOICE: "Voice",
}

CHANNEL_COLORS = {
    CHANNEL_LOCAL: "#FFFFFF",
    CHANNEL_WING: "#66CCFF",
    CHANNEL_FRIEND: "#66FF66",
    CHANNEL_SQUADRON: "#FFA500",
    CHANNEL_SYSTEM: "#CCCCCC",
    CHANNEL_NPC: "#AAAAAA",
    CHANNEL_VOICE: "#CC99FF",
}
TRANSLATION_COLOR = "#FFD27F"

_KNOWN_CHANNEL_KEYS = {
    "local": CHANNEL_LOCAL,
    "wing": CHANNEL_WING,
    "friend": CHANNEL_FRIEND,
    "player": CHANNEL_FRIEND,
    "squadron": CHANNEL_SQUADRON,
    "squadronleaders": CHANNEL_SQUADRON,
    "starsystem": CHANNEL_SYSTEM,
    "system": CHANNEL_SYSTEM,
    "npc": CHANNEL_NPC,
    "voicechat": CHANNEL_VOICE,
}


def canonical_channel(raw: Optional[str]) -> str:
    """Map a journal ReceiveText "Channel" value onto a CHANNEL_* constant.

    Unrecognized values fall back to "local" rather than being dropped,
    since local is the most common channel by far.
    """
    if not raw:
        return CHANNEL_LOCAL
    return _KNOWN_CHANNEL_KEYS.get(raw.strip().lower(), CHANNEL_LOCAL)


def _try_register_plugin_group(msgid_prefix: str):
    """Best-effort registration with EDMCModernOverlay's Overlay Controller,
    so the chat panel shows up there and the user can drag/reposition and
    restyle it. This uses an internal (undocumented-stable) module rather
    than the public `edmcoverlay` shim, so failure here is non-fatal --
    send_message() still works and messages still render either way, just
    without Controller support to reposition them.
    """
    try:
        from overlay_plugin import overlay_api
        overlay_api.define_plugin_group(
            plugin_name="EDChatOverlay",
            plugin_matching_prefixes=[msgid_prefix],
            plugin_group_name="Chat Log",
            plugin_group_prefixes=[msgid_prefix],
            plugin_group_anchor="nw",
        )
    except Exception as exc:  # pragma: no cover - defensive, cosmetic only
        logger.info(f"Could not register with EDMCModernOverlay's Overlay Controller: {exc}")


def _import_overlay_client():
    """`edmcoverlay` ships inside the EDMCModernOverlay plugin folder. If that
    plugin's directory isn't already on sys.path (e.g. load order put ours
    first), locate it as a sibling plugin folder and add it."""
    try:
        import edmcoverlay
        return edmcoverlay
    except ImportError:
        pass
    try:
        plugins_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate = os.path.join(plugins_root, "EDMCModernOverlay")
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)
        import edmcoverlay
        return edmcoverlay
    except ImportError:
        logger.warning("EDMCModernOverlay not found; chat overlay will not be displayed.")
        return None


class ChatMessage:
    _ids = count()

    def __init__(self, channel, who, text):
        self.id = next(ChatMessage._ids)
        self.channel = channel
        self.who = who
        self.text = text
        self.translated = None  # filled in later, asynchronously
        self.created_at = time.monotonic()


class ChatOverlay:
    """Owns the on-screen scrolling chat panel and its EDMCModernOverlay slots."""

    ID_PREFIX = "edchat-"
    TOP_X = 40
    TOP_Y = 60
    LINE_HEIGHT = 28
    FONT_SIZE = "large"
    # send_message() has no built-in wrapping, so long lines are split here
    # before being sent -- in characters, tuned for FONT_SIZE "large" in the
    # 1280-wide canonical canvas. Narrower if FONT_SIZE is changed to huge.
    WRAP_WIDTH = 60
    # Real-world plugins in this ecosystem (EDMC-Massacres, fcoc) all use a
    # finite TTL refreshed periodically rather than ttl<=0 "persistent" --
    # that path is rarely exercised, so a heartbeat is the proven-safe
    # approach: messages live for TTL_SEC and get re-sent well before that.
    TTL_SEC = 30
    # Also governs fade-out granularity: a message can linger up to this long
    # past its configured fade_seconds before being pruned.
    REFRESH_INTERVAL_SEC = 5

    def __init__(self, max_messages=8, fade_seconds=0):
        self.max_messages = max_messages
        self.fade_seconds = fade_seconds
        self._messages = deque()
        self._lock = threading.RLock()
        self._overlay_mod = _import_overlay_client()
        self._overlay = self._overlay_mod.Overlay() if self._overlay_mod else None
        self._active_slots = 0
        if self._overlay is not None:
            _try_register_plugin_group(self.ID_PREFIX)
        self._stop_event = threading.Event()
        self._heartbeat_thread = None
        if self._overlay is not None:
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self.REFRESH_INTERVAL_SEC):
            with self._lock:
                self._prune_expired()
                self._redraw()

    @property
    def available(self) -> bool:
        return self._overlay is not None

    def set_max_messages(self, n: int):
        with self._lock:
            self.max_messages = max(1, n)
            while len(self._messages) > self.max_messages:
                self._messages.popleft()
            self._redraw()

    def set_fade_seconds(self, seconds: int):
        with self._lock:
            self.fade_seconds = max(0, seconds)
            self._prune_expired()
            self._redraw()

    def _prune_expired(self):
        if self.fade_seconds <= 0:
            return
        cutoff = time.monotonic() - self.fade_seconds
        # _messages is oldest-first (push() appends), so the front is always
        # the next candidate to expire.
        while self._messages and self._messages[0].created_at < cutoff:
            self._messages.popleft()

    def push(self, channel, who, text) -> "ChatMessage":
        msg = ChatMessage(channel, who, text)
        with self._lock:
            self._messages.append(msg)
            while len(self._messages) > self.max_messages:
                self._messages.popleft()
            self._prune_expired()
            self._redraw()
        return msg

    def set_translation(self, msg_id: int, translated: str):
        with self._lock:
            for msg in self._messages:
                if msg.id == msg_id:
                    msg.translated = translated
                    self._redraw()
                    return

    def clear(self):
        with self._lock:
            self._messages.clear()
            self._redraw()

    def shutdown(self):
        self._stop_event.set()
        with self._lock:
            self._messages.clear()
            self._blank_slots(self._active_slots)
            self._active_slots = 0

    # ------------------------------------------------------------ render --
    def _redraw(self):
        if not self._overlay:
            return
        lines = []
        for msg in reversed(self._messages):  # newest first
            label = CHANNEL_LABELS.get(msg.channel, msg.channel.title())
            color = CHANNEL_COLORS.get(msg.channel, "#FFFFFF")
            for wrapped in self._wrap(f"[{label}] {msg.who}: {msg.text}"):
                lines.append((wrapped, color))
            if msg.translated:
                for wrapped in self._wrap(f"    -> {msg.translated}"):
                    lines.append((wrapped, TRANSLATION_COLOR))

        for i, (text, color) in enumerate(lines):
            self._overlay.send_message(
                f"{self.ID_PREFIX}{i}", text, color,
                self.TOP_X, self.TOP_Y + i * self.LINE_HEIGHT,
                ttl=self.TTL_SEC, size=self.FONT_SIZE,
            )
        if len(lines) < self._active_slots:
            self._blank_slots(self._active_slots, start=len(lines))
        self._active_slots = len(lines)

    def _blank_slots(self, count_to: int, start: int = 0):
        for i in range(start, count_to):
            self._overlay.send_message(f"{self.ID_PREFIX}{i}", "", "#FFFFFF", self.TOP_X, self.TOP_Y, ttl=self.TTL_SEC)

    @classmethod
    def _wrap(cls, text: str):
        return textwrap.wrap(
            text, width=cls.WRAP_WIDTH,
            subsequent_indent="    ", break_long_words=False, break_on_hyphens=False,
        ) or [""]
