"""Actual spend from Google Cloud billing (PR-10.9).

Every dollar figure in the platform admin portal comes from the Cloud Billing export to
BigQuery, never from our own price table. Google loads the export several times a day,
about a day behind, so the latest day is usually still filling up. The result is cached in
the store for an hour, so the portal doesn't run a query on every page load.

BILLING_EXPORT_TABLE names either the export table (project.dataset.table) or just its
dataset (project.dataset); for a dataset, the detailed usage cost table Google creates there
(gcp_billing_export_resource_v1_<billing account>) is found by its prefix.
"""

from __future__ import annotations

import calendar
import logging
import time
from datetime import date, datetime, timedelta, timezone

from ..config import settings
from ..store import store

log = logging.getLogger(__name__)

CACHE_SECONDS = 3600
FRESH_SECONDS = 60  # the Refresh button reads billing again at most this often
RETRY_SECONDS = 600  # after a failure, try again sooner
DAYS = 35
DETAILED_PREFIX = "gcp_billing_export_resource_v1_"

NOT_AVAILABLE = {
    "not_configured": "The billing export isn't connected yet (BILLING_EXPORT_TABLE is not set).",
    "no_data": ("The billing export has no data for this project yet. "
                "Google starts loading it within a day of turning the export on."),
    "error": "The billing export couldn't be read. Check the service account's BigQuery access.",
}


def _table(name: str) -> str:
    if len(name.split(".")) == 2:
        return f"{name}.{DETAILED_PREFIX}*"
    return name


def _query(table: str) -> str:
    # Cost net of credits, converted to US dollars (the rate is billing currency per dollar).
    project_filter = "AND project.id = @project" if settings.firebase_project_id else ""
    return f"""
        SELECT DATE(usage_start_time) AS day, service.description AS service, sku.description AS sku,
               SUM((cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0))
                   / IFNULL(NULLIF(currency_conversion_rate, 0), 1)) AS usd
        FROM `{table}`
        WHERE usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {DAYS} DAY)
          {project_filter}
        GROUP BY day, service, sku
    """


def _fetch_rows() -> list[dict]:
    from google.cloud import bigquery  # only needed when the export is connected

    client = bigquery.Client(project=settings.firebase_project_id or None)
    params = [bigquery.ScalarQueryParameter("project", "STRING", settings.firebase_project_id)] \
        if settings.firebase_project_id else []
    job = client.query(_query(_table(settings.billing_export_table)),
                       job_config=bigquery.QueryJobConfig(query_parameters=params))
    return [{"day": r["day"].isoformat(), "service": r["service"], "sku": r["sku"], "usd": float(r["usd"] or 0)}
            for r in job.result(timeout=30)]


def summarise(rows: list[dict], today: date, budget: float) -> dict:
    """Turn day x service x SKU rows into the portal's spend figures."""
    if not rows:
        return {"available": False, "reason": "no_data", "message": NOT_AVAILABLE["no_data"]}
    through = max(r["day"] for r in rows)
    daily: dict[str, float] = {}
    for r in rows:
        daily[r["day"]] = daily.get(r["day"], 0.0) + r["usd"]
    last_30 = sorted(daily)[-30:]
    # Services and items over the same 30 days as the chart (the query reaches back further
    # so the month to date is complete).
    services: dict[str, float] = {}
    skus: dict[tuple[str, str], float] = {}
    for r in rows:
        if r["day"] >= last_30[0]:
            services[r["service"]] = services.get(r["service"], 0.0) + r["usd"]
            skus[(r["service"], r["sku"])] = skus.get((r["service"], r["sku"]), 0.0) + r["usd"]

    month = today.strftime("%Y-%m")
    mtd = sum(v for d, v in daily.items() if d.startswith(month))
    # The forecast uses only complete days: the latest loaded day (and today) is usually still
    # filling up, and counting it as a whole day would pull the forecast down.
    complete_through = min(through, (today - timedelta(days=1)).isoformat())
    complete = [d for d in daily if d.startswith(month) and d < complete_through]
    days_counted = int(complete_through[8:]) - 1 if complete_through.startswith(month) else 0
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    forecast = sum(daily[d] for d in complete) / days_counted * days_in_month if days_counted else 0.0
    return {
        "available": True,
        "source": "Google Cloud billing export",
        "through": through,
        "currency": "USD",
        "budget_usd": budget,
        "month_to_date_usd": round(mtd, 2),
        "forecast_usd": round(forecast, 2),
        "days_counted": days_counted,
        "daily": [{"day": d, "cost_usd": round(daily[d], 2)} for d in last_30],
        "by_service": {k: round(v, 2) for k, v in sorted(services.items(), key=lambda kv: -kv[1]) if round(v, 2)},
        "top_skus": [{"service": s, "sku": k, "cost_usd": round(v, 2)}
                     for (s, k), v in sorted(skus.items(), key=lambda kv: -kv[1])[:10] if round(v, 2)],
    }


def spend(now: float | None = None, *, fresh: bool = False) -> dict:
    """Actual spend from the billing export, cached for an hour; "not available" with a reason otherwise.

    fresh=True skips the cache when it's more than a minute old, so repeated clicks on Refresh
    can't run up BigQuery queries.
    """
    if not settings.billing_export_table:
        return {"available": False, "reason": "not_configured", "message": NOT_AVAILABLE["not_configured"]}
    now = now or time.time()
    cached = store.get("billing_cache", "spend")
    if cached and cached.get("table") == settings.billing_export_table and now < cached.get("expires_at", 0):
        recent = now - cached["result"].get("fetched_at", 0) < FRESH_SECONDS
        if not fresh or recent:
            return cached["result"]
    try:
        rows = _fetch_rows()
        result = summarise(rows, datetime.fromtimestamp(now, timezone.utc).date(), settings.monthly_budget_usd)
        ttl = CACHE_SECONDS
    except Exception:
        log.exception("Reading the billing export failed")
        result = {"available": False, "reason": "error", "message": NOT_AVAILABLE["error"]}
        ttl = RETRY_SECONDS
    result["fetched_at"] = now
    store.put("billing_cache", {"id": "spend", "table": settings.billing_export_table,
                                "expires_at": now + ttl, "result": result})
    return result
