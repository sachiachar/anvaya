"""The owner is alive and well: what Saathi says to them never talks about their death.

The handover exists for "when the time comes", but the owner is talking about their life now.
Questions and next steps talk about the family knowing things ("so Sunita knows where the papers
are"), never about the owner dying ("your final wishes", "after you're gone"). The prompts ask for
this; this module checks it in code, because a model follows a prompt only most of the time.

Saathi may still talk about other people who have left us: the owner's late father is part of
their story. So the chat check looks only for words about the owner's own death, while next
steps (always about the owner) are held to the stricter check.
"""

from __future__ import annotations

import re

ALIVE_RULES = (
    "The person is alive and well, and talking about their life now. Never mention their death "
    "or dying, in any words: no 'final wishes', 'last wishes', 'after you're gone', 'when you "
    "pass', 'if something happens to you' or 'your funeral'. Talk about the family knowing things "
    "and having them in order: 'so Sunita knows where the papers are', 'who in the family should "
    "look after the flat', 'how you'd like your email and phone handled'."
)

# About the owner's own death, in English, Hinglish and Hindi.
_ABOUT_YOUR_DEATH = re.compile(
    r"\b(final|last) wish(es)?\b"
    r"|\byour (own )?(death|demise|passing|funeral|last rites)\b"
    r"|\b(when|after|if|once|before|in case) you(?:'re| are)? (die|pass away|gone|no longer)\b"
    r"|\b(when|after|if|once|before|in case) you(?:'ve| have)? (died|passed away|left us)\b"
    r"|\bafter you'?re gone\b"
    r"|\b(if|in case) (any|some)thing (were to |should )?happen(s|ed)? to you\b"
    r"|\b(in case|in the event) of (your )?(death|demise)\b"
    r"|\b(aap|aapke|aapki) (jaane ke baad|maut|mrityu|dehant)\b|\bantim ichh?a\b"
    r"|आपके जाने के बाद|आपकी (मृत्यु|मौत)|आपके देहांत|अंतिम इच्छा",
    re.IGNORECASE,
)

# Any death words at all: next steps and suggested topics are always about the owner.
_DEATH_WORDS = re.compile(
    r"\b(death|deaths|die|dies|died|dying|dead|deceased|demise|passed away|pass away|funeral|"
    r"last rites|no longer (here|around|with us))\b"
    r"|मृत्यु|मौत|देहांत",
    re.IGNORECASE,
)


def about_their_death(text: str | None) -> bool:
    """Whether a reply to the owner talks about the owner's own death."""
    return bool(text and _ABOUT_YOUR_DEATH.search(text))


def step_problems(step: dict) -> list[str]:
    """What's wrong with a next step's words (title, why, question), if anything."""
    return [f"{field} talks about death: {step[field]!r}" for field in ("title", "why_it_matters", "question_for_owner")
            if step.get(field) and (_ABOUT_YOUR_DEATH.search(step[field]) or _DEATH_WORDS.search(step[field]))]
