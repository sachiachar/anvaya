"""Who is acting, for tests: the switcher's header (PR-1.3). Vikram, the owner, is the default."""

from app.demo import ACTING_HEADER


def acting(person: str) -> dict:
    return {ACTING_HEADER: person}


PRIYA, ANJALI, RAHUL, VIKRAM = acting("priya"), acting("anjali"), acting("rahul"), acting("vikram")
