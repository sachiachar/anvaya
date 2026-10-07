"""Never pursue a next step that could be part of a fraud (PR-13.3, docs/proposals/listening.md §6).

Every next step is typed from closed lists in code (step_types.py): what it does, the channel it
goes through, and flags. Flags are worked out in code from the step's words as well as taken from
the step, so the check never depends on a model remembering to set them. Before a step reaches
the owner, Saathi or the suggested topic, `check` decides, in order:

1. a step of an unknown type, channel or flag is not pursued: when unsure, the answer is no;
2. a step that involves a secret (an OTP, a password or a PIN) is never pursued. The one exception
   is adding it to the owner's own vault in the app, which is encrypted on their device, and only
   if the step doesn't also tell them to share, give or send it to anyone;
3. a step that matches an approved fraud tactic is not pursued. A tactic lists the step types,
   channels and flags it exploits (`fraud_guides.Exploits`); only tactics the certificate check may
   use (observed, derived, validated) count.

If the approved tactics can't be read, nothing is pursued: it fails closed.

A step that isn't pursued is dropped, and a count is recorded with no words and no names: the
rule or tactic, the copy's hashed key and the time. The portal shows the count.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata

from . import demo, fraud_guides
from .config import settings
from .governance import core as governance
from .step_types import CHANNELS, FLAGS, STEP_TYPES
from .store import store

log = logging.getLogger(__name__)

# Words for each flag, in English, Hinglish and Hindi, matched on normalised text (see _plain).
_SECRET = re.compile(r"\b(otps?|one[- ]time (passwords?|codes?|pins?)|passwords?|passcodes?|pins?|pincodes?|cvvs?|cvcs?|"
                     r"mpins?|verification codes?|security codes?|login details|credentials|"
                     r"codes? (sent|that comes|you get) (to|on) your (phone|mobile))\b"
                     r"|पासवर्ड|पासकोड|ओटीपी|पिन|कोड", re.IGNORECASE)
# A postal "PIN code" is not a secret, but only in a postal context and never with a card or bank.
_POSTAL = re.compile(r"\b(address|area|postal|post office|locality|\d{6})\b|पता", re.IGNORECASE)
_CARD = re.compile(r"\b(card|atm|upi|bank|net ?banking|account)\b|कार्ड|बैंक", re.IGNORECASE)
_PIN_CODE = re.compile(r"\bpin[- ]?codes?\b|पिन ?कोड", re.IGNORECASE)
_PAYMENT = re.compile(r"\b(pay|pays|paid|paying|payment|fees?|charges?|deposit|commission|bribe|"
                      r"(send|transfer|move) (the )?(money|amount|funds|cash))\b|शुल्क|फीस|पैसे भेज", re.IGNORECASE)
_STARTED_BY_OTHER = re.compile(r"\b(who called|who will call|will call you|caller|called you|someone (called|asked|"
                               r"messaged)|asked you to|on the call|sms from|whatsapp(ed)? you|link in the message|"
                               r"middleman|tout|broker)\b", re.IGNORECASE)
_DEADLINE = re.compile(r"\b(today|immediately|urgent(ly)?|right now|within \d+ (hours?|days?)|last date|"
                       r"before it expires|or (it|the account) (will be )?(blocked|frozen))\b|तुरंत|आज ही", re.IGNORECASE)
# The only way a secret may appear in a step: saving it in the owner's own vault, with nobody else in it.
_INTO_VAULT = re.compile(r"\b(add|save|store|keep|put)\b.{0,60}\b(in|into|to) (the |your )?(anvaya )?vault\b",
                         re.IGNORECASE)
_SOMEONE_ELSE = re.compile(r"\b(agent|officer|executive|caller|someone|anyone|link|share|give|send|tell|forward|"
                           r"provide|enter|read out|hand over|show|(by|via|over|on) (e-?mail|whatsapp|sms|phone)|"
                           r"(e-?mail|whatsapp|sms) (it|them|this|that)|ready for|when asked|batao|"
                           r"bata do|de do)\b|बताएं|बताओ|बता दें|भेजें|दे दें|दीजिए|शेयर", re.IGNORECASE)
# Rules that hold without any tagged tactic (listening.md §6): paying because someone else asked,
# changing anything with an institution because someone called, and being rushed by someone else.
_FIXED_RULES = (("rule:pay_on_request", lambda t, f: {"payment", "started_by_other"} <= f),
                ("rule:change_on_request", lambda t, f: t == "record_with_institution" and "started_by_other" in f),
                ("rule:rushed_by_someone", lambda t, f: {"deadline", "started_by_other"} <= f))
_NEGATION = re.compile(r"\b(never|don'?t|do not|not to|no one|nobody|kabhi mat)\b|कभी नहीं|कभी मत", re.IGNORECASE)


def _plain(text: str) -> str:
    """Text as the checks read it: Unicode folded (fullwidth letters, zero-width characters gone) and
    letters spelt out one by one joined up again, so "O T P" and "o.t.p" read as "OTP"."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", text)
    return re.sub(r"\b(?:[A-Za-z][ .\-_]){2,}[A-Za-z]\b", lambda m: re.sub(r"[ .\-_]", "", m.group()), text)


def _secret(words: str) -> bool:
    """Whether these words mention a secret. A postal PIN code alone doesn't count."""
    if not _SECRET.search(words):
        return False
    if _PIN_CODE.search(words) and _POSTAL.search(words) and not _CARD.search(words):
        return bool(_SECRET.search(_PIN_CODE.sub(" ", words).replace("कोड", " ")))
    return True


# Today's next steps (the readiness planner's actions) as typed steps, until PR-13.4 types them itself.
ACTION_TYPES = {"talk": ("talk_more", "anvaya_app"), "family": ("tell_family", "family_named_by_owner"),
                "vault": ("add_to_vault", "anvaya_app"), "legal": ("legal_document", "registered_professional"),
                "institution": ("record_with_institution", "institution_official")}


class TacticsUnavailable(Exception):
    """The approved tactics couldn't be read; nothing may be pursued."""


def _words(step: dict) -> str:
    return _plain(" ".join(str(step.get(f) or "") for f in ("title", "text", "why_it_matters", "question_for_owner"))
                  + " " + " ".join(str(s) for s in step.get("steps") or []))


def flags_of(step: dict) -> set[str]:
    """The step's own flags plus those its words show."""
    words = _words(step)
    found = {f for f, pattern in (("payment", _PAYMENT), ("started_by_other", _STARTED_BY_OTHER),
                                  ("deadline", _DEADLINE)) if pattern.search(words)}
    if _secret(words):
        found.add("secret")
    return set(step.get("flags") or []) | found


def approved_tactics() -> list[tuple[str, fraud_guides.Exploits]]:
    """Approved tactics that say which steps they exploit, from every current guide. Raises
    TacticsUnavailable if the guides can't be read."""
    try:
        guides = fraud_guides.current_all()
    except Exception as e:
        log.warning("fraud guides unavailable; no next step is pursued until they can be read")
        raise TacticsUnavailable from e
    return [(f"{g.key}:{t.id}", t.exploits) for g in guides for t in g.tactics
            if t.label in fraud_guides.CHECKABLE and t.exploits and not t.exploits.empty()]


def check(step: dict, tactics: list[tuple[str, fraud_guides.Exploits]]) -> str | None:
    """Why this step must not be pursued, or None if it may be."""
    if step.get("type") not in STEP_TYPES or step.get("channel") not in CHANNELS:
        return "unknown"
    if not set(step.get("flags") or []) <= set(FLAGS):
        return "unknown"
    flags = flags_of(step)
    words = _words(step)
    own_vault = (step["type"] == "add_to_vault" and step["channel"] == "anvaya_app"
                 and bool(_INTO_VAULT.search(words)) and not _SOMEONE_ELSE.search(words))
    if "secret" in flags and not own_vault:
        return "secret"
    for rule, applies in _FIXED_RULES:
        if applies(step["type"], flags):
            return rule
    for key, exploits in tactics:
        if exploits.matches(step["type"], step["channel"], flags):
            return f"tactic:{key}"
    return None


def reply_problem(reply: str) -> str | None:
    """A reply of Saathi's that would help with a risky route: asking for or passing on a secret,
    or paying because someone else asked. A warning ("never share your OTP") is fine."""
    words = _plain(reply)
    if _NEGATION.search(words):
        return None
    if _secret(words) and _SOMEONE_ELSE.search(words) and not _INTO_VAULT.search(words):
        return "secret"
    if _PAYMENT.search(words) and _STARTED_BY_OTHER.search(words):
        return "rule:pay_on_request"
    return None


def typed(task: dict) -> dict:
    """A readiness next step with its type and channel, from its action."""
    kind, channel = ACTION_TYPES.get(task.get("action", ""), (None, None))
    return {"type": kind, "channel": channel, "flags": [], **task}  # a step that has its own type keeps it


def allowed(steps: list[dict]) -> list[dict]:
    """The steps that may reach the owner, without counting the others (for reads)."""
    try:
        tactics = approved_tactics()
    except TacticsUnavailable:
        return []
    return [s for s in steps if check(typed(s), tactics) is None]


def pursue(owner_id: str, steps: list[dict]) -> list[dict]:
    """The steps that may reach the owner. The others are dropped and counted. Raises
    TacticsUnavailable if the guides can't be read, so a caller never mistakes it for "no steps"."""
    tactics = approved_tactics()
    kept = []
    for step in steps:
        reason = check(typed(step), tactics)
        if reason is None:
            kept.append(step)
        else:
            # The hashed key of whoever's copy this is (what the portal shows), never a name. The
            # same step dropped again on a later run is counted once: its record id is a salted
            # hash of the copy, the rule and the step's title, and holds no words itself.
            key = demo.current_or_none() or governance.user_key(owner_id)
            digest = hashlib.sha256(f"{settings.user_key_salt}:{key}:{reason}:{step.get('title', '')}".encode())
            store.put("steps_not_pursued", {"id": f"snp_{digest.hexdigest()[:16]}", "at": time.time(),
                                            "rule": reason, "user_key": key})
    return kept
