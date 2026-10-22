"""Deciding what the avatar responds to next.

A busy chat produces far more messages than one voice can answer, so
the brain keeps a small pool of candidates and, each time the avatar is
free, picks the most worth answering: a raid/sub first, then cheered
(bits) messages, first-time chatters, questions and mentions of the
avatar -- newest first, and never the same viewer twice in a row.
Messages a moderator deleted (or from a user who was timed out/banned)
leave the pool immediately; so do stale ones -- answering something
from two minutes ago sounds broken on air.

Pure logic, no I/O: the runner feeds it ChatEvents and asks for a Pick.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from stream_agent.twitch_chat import ChatEvent

_LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|ru|net|org|io|gg|tv|me|xyz)\b)", re.IGNORECASE)
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
_SPACES = re.compile(r"\s+")
# Laughter and one-word reactions: chat atmosphere, not something to
# answer out loud.
_NOISE = re.compile(
    r"^(l+o+l+|k+e+k+|l+m+a+o+|x+d+|g+g+|о+р+у+|\++|f|w+|\?+|!+|\.+)$", re.IGNORECASE
)
# "hahahahahhhaha", "ахахаххах", "xaxaxa": only laughter letters.
_LAUGHTER = re.compile(r"^[haаxх]{4,}$", re.IGNORECASE)
_THANKED_NOTICES = {"raid", "sub", "resub", "subgift", "submysterygift", "anongiftpaidupgrade",
                    "giftpaidupgrade"}


@dataclass(frozen=True)
class Pick:
    mode: str  # "answer" | "notice" | "idle"
    event: ChatEvent | None = None


@dataclass
class _Candidate:
    event: ChatEvent
    score: float
    seen_at: float


@dataclass
class ChatBrain:
    bot_names: frozenset[str] = frozenset()
    name_words: tuple[str, ...] = ()
    max_age: float = 90.0
    idle_after: float = 45.0
    user_cooldown: float = 120.0
    # Subs/gifts are thanked together at most this often -- a "gift
    # bomb" is a dozen notices in a second, and thanking each one would
    # crowd every chat question off the air.
    thanks_cooldown: float = 45.0
    clock: Callable[[], float] = time.monotonic
    _pool: dict[str, _Candidate] = field(default_factory=dict)  # by user
    _notices: deque[ChatEvent] = field(default_factory=lambda: deque(maxlen=20))
    _recent: deque[ChatEvent] = field(default_factory=lambda: deque(maxlen=30))
    _answered_at: dict[str, float] = field(default_factory=dict)
    _last_spoke: float | None = None
    _last_thanks: float = -1e9

    # -- input --------------------------------------------------------

    def observe(self, event: ChatEvent) -> None:
        if event.kind == "message":
            self._observe_message(event)
        elif event.kind == "notice" and event.notice_type in _THANKED_NOTICES:
            self._notices.append(event)
        elif event.kind == "delete":
            self._forget(lambda e: e.message_id == event.message_id)
        elif event.kind == "clear_user":
            self._forget(lambda e: e.user == event.user)
            self._notices = deque(
                (n for n in self._notices if n.user != event.user), maxlen=20
            )
        elif event.kind == "clear_chat":
            self._pool.clear()
            self._recent.clear()

    def _forget(self, match: Callable[[ChatEvent], bool]) -> None:
        self._pool = {u: c for u, c in self._pool.items() if not match(c.event)}
        self._recent = deque((e for e in self._recent if not match(e)), maxlen=30)

    def _observe_message(self, event: ChatEvent) -> None:
        if event.user in self.bot_names:
            return
        text = _SPACES.sub(" ", event.text).strip()
        if not text or text.startswith("!"):
            return  # chat commands are for bots
        self._recent.append(event)
        letters = _LETTERS.findall(text)
        squashed = text.replace(" ", "")
        lone_word = len(text.split()) == 1 and "?" not in text
        if (
            _LINK.search(text)
            or len(letters) < 3
            or _NOISE.match(squashed)
            or _LAUGHTER.match(squashed)
            # "Auraaaa", "W", "Pog": reactions, not something to answer --
            # unless it's someone's first message or they cheered.
            or (lone_word and not event.first_message and not event.bits)
        ):
            return  # links, emotes, laughter, one-word reactions: context, not prompts
        normalized = text.lower()
        if any(
            c.event.text.lower() == normalized and c.event.user != event.user
            for c in self._pool.values()
        ):
            return  # copypasta wave: one copy is enough
        self._pool[event.user] = _Candidate(event, self._score(event, text), self.clock())

    def _score(self, event: ChatEvent, text: str) -> float:
        score = 1.0
        if event.bits:
            score += 3 + min(event.bits, 1000) / 250
        if event.first_message:
            score += 2
        if "?" in text:
            score += 1.5
        lowered = text.lower()
        if any(word in lowered for word in self.name_words):
            score += 2
        if event.badges & {"subscriber", "vip"}:
            score += 0.5
        return score

    # -- output -------------------------------------------------------

    def spoke(self) -> None:
        """Call when the avatar finishes speaking."""
        self._last_spoke = self.clock()

    def next_pick(self) -> Pick | None:
        now = self.clock()
        raid = next((n for n in self._notices if n.notice_type == "raid"), None)
        if raid is not None:
            # A raid brings a crowd right now: welcome them right away.
            self._notices.remove(raid)
            return Pick("notice", raid)
        if self._notices and now - self._last_thanks >= self.thanks_cooldown:
            self._last_thanks = now
            return Pick("notice", self._merge_thanks())
        self._pool = {
            u: c for u, c in self._pool.items() if now - c.seen_at <= self.max_age
        }
        eligible = [
            c
            for c in self._pool.values()
            if now - self._answered_at.get(c.event.user, -1e9) >= self.user_cooldown
        ]
        if eligible:
            # Newer messages win ties: a 30-second-old question is worth
            # less than one just asked.
            best = max(eligible, key=lambda c: c.score - (now - c.seen_at) / self.max_age)
            del self._pool[best.event.user]
            self._answered_at[best.event.user] = now
            return Pick("answer", best.event)
        quiet_since = self._last_spoke if self._last_spoke is not None else -1e9
        if now - quiet_since >= self.idle_after:
            return Pick("idle")
        return None

    def _merge_thanks(self) -> ChatEvent:
        """All pending subs/gifts as one notice: "A, B и ещё 3"."""
        notices, self._notices = list(self._notices), deque(maxlen=20)
        names: list[str] = []
        texts: list[str] = []
        for notice in notices:
            name = notice.display_name or notice.user
            if name not in names:  # one gifter of ten subs is one name
                names.append(name)
                if notice.text:
                    texts.append(notice.text)
        shown = ", ".join(names[:5]) + (f" и ещё {len(names) - 5}" if len(names) > 5 else "")
        first = notices[0]
        return ChatEvent(
            kind="notice",
            channel=first.channel,
            user=first.user,
            display_name=shown,
            notice_type="sub" if len(names) > 1 else first.notice_type,
            text="; ".join(texts)[:450],
        )

    def recent_chat(self, limit: int = 20) -> list[ChatEvent]:
        return list(self._recent)[-limit:]

    @property
    def pending(self) -> int:
        return len(self._pool) + len(self._notices)
