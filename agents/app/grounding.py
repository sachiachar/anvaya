"""Cards say only what was said (AGENTS.md rule 8: rules belong in code).

The model fills each card from a conversation. Sometimes it fills a field with something nobody
said, for example the example in its own instructions ("blue folder, bedroom cupboard"). Before a
card is saved, code checks it against what was actually said:

- A card's quote must be the owner's own words from this turn. If it isn't, the card is dropped.
- A card with the title of a kept moment adds to that moment. The owner's words or Saathi's last
  message must name it: a name in its title ("LIC"), or else a word of it. If not, the card is
  dropped: "the papers are in the cupboard" never becomes a detail of a kept "LIC policy details"
  the owner didn't mention. A card waiting for Keep isn't checked, so "it" can follow it up.
- A fact the family relies on later (where the papers are, the nominee, who it's for, the last 4
  digits, a place, a new person's name) must be supported by the owner's words, Saathi's last
  message (so "Yes" can confirm a question) or what is already kept. If it isn't, the field is
  cleared, so it stays a gap that Saathi asks about later. The rest of the card is kept.
- A value in another script than the owner's words (an English value from Hindi speech) can't be
  checked word by word, so it is kept and counted.

Only counts are logged, never words (AGENTS.md rule 4).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher

from . import tasks

log = logging.getLogger(__name__)

QUOTE_MATCH = 0.85     # how close a quote must be to the owner's words; transcripts differ a little
FIELD_SUPPORT = 2 / 3  # the share of a value's words that must appear in what was said
# What a model writes in a field when nobody said anything ("N/A", "-", ".."). They mean empty.
PLACEHOLDERS = frozenset({"", "n a", "na", "nil", "none", "null", "unknown", "not known", "not applicable",
                          "not available", "tbd"})
# Fields of a card that a placeholder may fill; each becomes empty ("" or None) instead.
_PLACEHOLDER_FIELDS = ("institution", "product", "account_hint", "holder_name", "nominee_or_beneficiary",
                       "approximate_value", "institution_contact", "papers_at", "intended_for")


def placeholder(value: str | None) -> bool:
    """Whether a value says nothing, like "N/A", "NA", "nil" or "-". Letters in any script count, so
    a value in Hindi is never mistaken for an empty one."""
    return " ".join(re.sub(r"[^\w']+", " ", (value or "").lower()).split()) in PLACEHOLDERS


def _blank_placeholders(item):
    """A copy of an inventory card with every placeholder value emptied, and an account hint with no
    digits ("..") emptied too, so "N/A" never reaches a card, the book or a letter."""
    fields = {name: ("" if isinstance(getattr(item, name), str) and name in ("institution", "product", "account_hint")
                     else None)
              for name in _PLACEHOLDER_FIELDS
              if isinstance(getattr(item, name, None), str) and placeholder(getattr(item, name))}
    if item.account_hint and not re.search(r"\d", item.account_hint):
        fields["account_hint"] = ""
    return item.model_copy(update=fields) if fields else item
PREFIX = 5             # words that share their first 5 letters count as the same ("bedroom", "bedrooms")

_STOP = frozenset(
    "the and for with from that this these those his her hers their our ours my mine your yours its "
    "are was were has have had will shall can kept keep keeps papers paper them they she him who what "
    "where which there here all any some not".split()
)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT_GAPS = re.compile(r"(?<=\d)[\s\-–.]+(?=\d)")  # "1 2 3 4" and "12-34" are "1234"
# A word, with its vowel signs and other combining marks: Python's \w alone splits "प्रिया" into "प", "र", "य".
_WORD = re.compile(r"(?:[^\W_]|[\u0300-\u036F\u0900-\u0963\u0966-\u097F])+")


def _words(text: str | None) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFC", text or "").casefold())


def _digits(text: str | None) -> str:
    text = "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text or "")
    return _DIGIT_GAPS.sub("", text)


def _script(text: str) -> str | None:
    devanagari, latin = len(_DEVANAGARI.findall(text)), len(_LATIN.findall(text))
    if devanagari + latin == 0:
        return None
    return "Devanagari" if devanagari > latin else "Latin"


def said(quote: str | None, words: str | None) -> bool:
    """True if the quote is in what the owner said: word for word, or nearly (a transcript and the
    model's copy of it can differ by a word or punctuation)."""
    q, w = _words(quote), _words(words)
    if not q or not w:
        return False
    if f" {' '.join(q)} " in f" {' '.join(w)} ":
        return True
    n = len(q)
    windows = [w[i:i + n] for i in range(max(1, len(w) - n + 1))]
    return any(SequenceMatcher(None, q, window, autojunk=False).ratio() >= QUOTE_MATCH for window in windows)


class _Support:
    """What a value may rest on: the owner's words, Saathi's last message and what is kept."""

    def __init__(self, text: str, owner_words: str):
        self.text = text
        self.words = set(_words(text))
        self.prefixes = {w[:PREFIX] for w in self.words if len(w) >= PREFIX}
        self.digits = set(re.findall(r"\d{4,}", _digits(text)))
        self.script = _script(owner_words or "")  # the owner's language, not that of what's kept

    def has_word(self, word: str) -> bool:
        return word in self.words or (len(word) >= PREFIX and word[:PREFIX] in self.prefixes)

    def checkable(self, value: str) -> bool:
        mine = _script(value)
        return not (mine and self.script and mine != self.script)

    def supports(self, value: str | None) -> bool:
        specific = _specific(value)
        if not specific:
            return True  # nothing specific to check
        return sum(self.has_word(w) for w in specific) / len(specific) >= FIELD_SUPPORT

    def supports_digits(self, hint: str | None) -> bool:
        last4 = re.sub(r"\D", "", _digits(hint))[-4:]
        return not last4 or any(d.endswith(last4) or last4 in d for d in self.digits)


def _specific(text: str | None) -> list[str]:
    """The words of a value that say something: not short, not a stop word, not a number."""
    return [w for w in _words(text) if len(w) >= 3 and w not in _STOP and not w.isdigit()]


def _names_kept(title: str | None, turn: _Support, kept_titles: set[str], counts: "_Counts") -> bool:
    """Whether a card reusing a kept moment's title is about that moment: a name in the title ("LIC",
    "Bangalore"), or if it has none a specific word of it, is in what was said this turn."""
    if " ".join(_words(title)) not in kept_titles:
        return True
    if not turn.checkable(title or ""):
        counts.unchecked += 1
        return True
    words = sorted(tasks.names(title or "")) or _specific(title)
    return not words or any(turn.has_word(w) for w in words)


class _Counts:
    def __init__(self):
        self.dropped = self.cleared = self.unchecked = 0


def _check_text(value: str | None, support: _Support, counts: _Counts) -> bool:
    """Whether to keep a text value."""
    if not (value or "").strip():
        return True
    if not support.checkable(value):
        counts.unchecked += 1
        return True
    if support.supports(value):
        return True
    counts.cleared += 1
    return False


def _keep_list(values: list[str], support: _Support, counts: _Counts) -> list[str]:
    return [v for v in values if _check_text(v, support, counts)]


def ground(result, *, owner_words: str, saathi_words: str = "", known: dict | None = None,
           from_document: bool = False):
    """Return a copy of an extraction result (a ConversationTurn, Extraction or CaptureResult) with
    every card checked against what was said. `owner_words` is what the owner said or typed in this
    turn; `saathi_words` is Saathi's message they answered; `known` is what is already kept or waiting.
    With `from_document`, the item's account details and nominee come from the shared document, so
    only what the owner says about it (where the papers are, who it's for) is checked."""
    kept = known or {}
    support = _Support(" \n".join([
        owner_words or "", saathi_words or "",
        json.dumps({k: kept.get(k) for k in ("family", "inventory")}, ensure_ascii=False),
    ]), owner_words)
    turn = _Support(" \n".join([owner_words or "", saathi_words or ""]), owner_words)  # without what's kept
    kept_titles = set() if from_document else {  # a shared document names its own subject
        " ".join(_words(m.get("title"))) for m in kept.get("moments") or []
        if isinstance(m, dict) and not m.get("waiting_for_keep")}
    counts = _Counts()
    update: dict = {}

    if hasattr(result, "moments"):
        moments = []
        for m in result.moments:
            if not said(m.quote, owner_words) or not _names_kept(m.title, turn, kept_titles, counts):
                counts.dropped += 1
                continue
            moments.append(m.model_copy(update={
                "places": _keep_list(m.places, support, counts),
                "intended_for": _keep_list(m.intended_for, support, counts),
            }))
        update["moments"] = moments

    if hasattr(result, "inventory"):
        items = []
        for item in result.inventory:
            item = _blank_placeholders(item)
            fields: dict = {}
            for name in ("papers_at", "intended_for") + (() if from_document else ("nominee_or_beneficiary",)):
                if not _check_text(getattr(item, name), support, counts):
                    fields[name] = None
            if not from_document and not support.supports_digits(item.account_hint):
                counts.cleared += 1
                fields["account_hint"] = ""
            items.append(item.model_copy(update=fields) if fields else item)
        update["inventory"] = items

    if hasattr(result, "people"):
        # A person named on a shared document (its nominee or holder) may get a card from it.
        named = [getattr(i, f) or "" for i in getattr(result, "inventory", []) if from_document
                 for f in ("nominee_or_beneficiary", "holder_name")]
        people_support = _Support(support.text + " \n" + " ".join(named), owner_words) if any(named) else support
        update["people"] = [p for p in result.people if _check_text(p.name, people_support, counts)]

    if counts.dropped or counts.cleared or counts.unchecked:
        log.info("grounding: dropped %d cards with a quote the owner didn't say or a kept title nobody named, "
                 "cleared %d values nobody said, %d values in another script not checked",
                 counts.dropped, counts.cleared, counts.unchecked)
    return result.model_copy(update=update)
