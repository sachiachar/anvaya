"""Paging, search and filters for the admin portal's lists: one page at a time.

Every list route scopes its rows to the viewer first (a demo admin sees only their own copy), then
filters, searches and pages them here. So a page, its total and its counts never include anyone
else's rows. Search covers only the fixed words and hashed keys the portal already shows: the portal
never holds conversation text, so search can't reach it.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fastapi import HTTPException, Query

DEFAULT_SIZE = 20
MAX_SIZE = 100       # nobody can pull a whole list in one request
MAX_QUERY = 100      # characters in a search
MAX_PAGE = 10_000


@dataclass(frozen=True)
class PageQuery:
    page: int
    size: int
    q: str
    since: float | None   # Unix seconds, from the start of the "from" day (UTC)
    until: float | None   # Unix seconds, to the end of the "to" day (UTC)


def _day(value: str | None, end: bool) -> float | None:
    if not value:
        return None
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"Use a date like 2026-10-17, not {value[:20]!r}.") from None
    start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC).timestamp()
    return start + 86400 if end else start


def page_query(page: int = Query(1, ge=1, le=MAX_PAGE), size: int = Query(DEFAULT_SIZE, ge=1, le=MAX_SIZE),
               q: str = Query("", max_length=MAX_QUERY),
               date_from: str | None = Query(None, alias="from", max_length=10),
               date_to: str | None = Query(None, alias="to", max_length=10)) -> PageQuery:
    """The paging, search and date parameters every list route takes (a FastAPI dependency)."""
    since, until = _day(date_from, end=False), _day(date_to, end=True)
    if since is not None and until is not None and since >= until:
        raise HTTPException(422, "The 'from' date must be on or before the 'to' date.")
    return PageQuery(page=page, size=size, q=q.strip(), since=since, until=until)


def _words(value) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _words(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _words(v)
    else:
        yield str(value)


def search(rows: list[dict], q: str, fields: Iterable[str]) -> list[dict]:
    """Rows where every word of the search appears in one of `fields` (any case)."""
    terms = q.lower().split()
    if not terms:
        return rows
    fields = tuple(fields)

    def hit(row: dict) -> bool:
        text = " ".join(w for f in fields for w in _words(row.get(f))).lower()
        return all(t in text for t in terms)
    return [r for r in rows if hit(r)]


def dated(rows: list[dict], pq: PageQuery, key: str = "at") -> list[dict]:
    """Rows from the 'from' day to the 'to' day; a row with no time is kept only with no dates set."""
    if pq.since is None and pq.until is None:
        return rows
    out = []
    for r in rows:
        at = r.get(key)
        if not isinstance(at, int | float):
            continue
        if (pq.since is None or at >= pq.since) and (pq.until is None or at < pq.until):
            out.append(r)
    return out


def where(rows: list[dict], **filters) -> list[dict]:
    """Rows whose field equals each given value; a filter set to None isn't applied. A callable
    filter gets the row and returns whether to keep it."""
    for key, want in filters.items():
        if want is None:
            continue
        if callable(want):
            rows = [r for r in rows if want(r)]
        else:
            rows = [r for r in rows if r.get(key) == want]
    return rows


def counts(rows: list[dict], key: str | Callable[[dict], object]) -> dict[str, int]:
    """How many rows have each value of `key`, most first: the choices for a filter."""
    get = key if callable(key) else (lambda r: r.get(key))
    out: dict[str, int] = {}
    for r in rows:
        value = get(r)
        if value is None or value == "":
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def page(rows: list[dict], pq: PageQuery) -> dict:
    """One page of rows already in order, and where it sits. A page past the end shows the last one."""
    total = len(rows)
    pages = max(1, math.ceil(total / pq.size))
    number = min(pq.page, pages)
    start = (number - 1) * pq.size
    return {"items": rows[start:start + pq.size],
            "page": {"page": number, "size": pq.size, "total": total, "pages": pages}}
