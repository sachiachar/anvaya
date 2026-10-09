"""The hourly fraud cycle (Cloud Scheduler): runs happen inside the scheduler's request, so Cloud Run
keeps the CPU on. Only the scheduler's own account may call it. "Run now" on Google Cloud queues the
run and asks the scheduler to run at once; locally it runs in the background as before."""

import asyncio
import json
import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from app import fraud_guides, fraud_pipeline, main, scheduler
from app.config import settings
from app.store import store

SA = "fraud-scheduler@demo-project.iam.gserviceaccount.com"
KEY = next(iter(fraud_guides.BUILT_IN))


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for collection in ("fraud_runs", "fraud_run_lock"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    ran = []

    async def fake(run):  # a run that finishes at once, noting what ran
        ran.append((run["kind"], run["guide_key"], run["by"]))
        return fraud_pipeline._finish(run, {"stub": 1})

    monkeypatch.setattr(fraud_pipeline, "research", fake)
    monkeypatch.setattr(fraud_pipeline, "dream", fake)
    return ran


def test_a_cycle_researches_then_dreams_each_guide(fresh):
    out = asyncio.run(fraud_pipeline.cycle())
    assert fresh == [("research", KEY, fraud_pipeline.SCHEDULER), ("dream", KEY, fraud_pipeline.SCHEDULER)]
    assert set(out.values()) == {"done"} and store.get(*fraud_pipeline.LOCK) is None


def test_queued_runs_go_first_and_alone(fresh):
    queued = fraud_pipeline.start_run("dream", KEY, "u_reviewer", queued=True)
    assert queued["status"] == "queued"
    out = asyncio.run(fraud_pipeline.cycle())
    assert fresh == [("dream", KEY, "u_reviewer")] and out == {queued["id"]: "done"}


def test_a_guide_with_a_run_going_is_skipped_until_next_hour(fresh):
    store.create(fraud_pipeline.LOCK[0], {"id": fraud_pipeline.LOCK[1], "run_id": "run_other", "at": 9e12})
    out = asyncio.run(fraud_pipeline.cycle())
    assert fresh == [] and all(v.startswith("skipped") for v in out.values())


def test_a_queued_run_can_be_stopped_before_it_begins(fresh):
    run = fraud_pipeline.start_run("dream", KEY, "u_reviewer", queued=True)
    assert fraud_pipeline.request_stop(run["id"])["status"] == "stopped"
    assert store.get(*fraud_pipeline.LOCK) is None
    asyncio.run(fraud_pipeline.cycle())
    assert ("dream", KEY, "u_reviewer") not in fresh  # the cycle doesn't run a stopped run


def test_a_queued_run_waits_through_an_hour_and_times_out_only_after():
    run = fraud_pipeline.start_run("research", KEY, "u_reviewer", queued=True)
    fraud_pipeline.expire_stale(now=run["started_at"] + 3600 + 60)  # the next hourly cycle hasn't come yet
    assert store.get("fraud_runs", run["id"])["status"] == "queued"
    fraud_pipeline.expire_stale(now=run["started_at"] + fraud_pipeline.QUEUED_TIMEOUT_SECONDS + 1)
    assert store.get("fraud_runs", run["id"])["status"] == "failed" and store.get(*fraud_pipeline.LOCK) is None


def test_a_running_run_is_never_timed_out_while_the_scheduler_still_waits_for_it():
    assert fraud_pipeline.RUN_TIMEOUT_SECONDS > 1800  # the job's attempt deadline


def test_a_queued_runs_time_limit_counts_from_when_it_really_starts(monkeypatch, fresh):
    run = fraud_pipeline.start_run("dream", KEY, "u_reviewer", queued=True)
    store.update(*fraud_pipeline.LOCK, {"at": 1.0})  # queued long ago
    seen = {}

    async def watch(r):
        seen["lock_at"] = store.get(*fraud_pipeline.LOCK)["at"]
        return fraud_pipeline._finish(r, {})

    monkeypatch.setattr(fraud_pipeline, "dream", watch)
    asyncio.run(fraud_pipeline.execute(run))
    assert seen["lock_at"] > 1.0


def test_the_hour_is_skipped_once_the_fraud_agents_used_their_share(fresh):
    ceiling = __import__("app.governance.core", fromlist=["core"]).service_ceiling()
    share = int(ceiling["tokens_per_day"] * ceiling["scheduled_fraud_share"])
    day = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    store.put("usage_days", {"id": day, "day": day, "agents": {fraud_pipeline.DREAMER: {"tokens_in": share, "tokens_out": 0}}})
    try:
        out = asyncio.run(fraud_pipeline.cycle())
        assert fresh == [] and "skipped" in out
    finally:
        store.delete("usage_days", day)


# ---- Only the scheduler may call the cycle ---------------------------------------------------


def post(client, token=None):
    return client.post("/internal/fraud/cycle", headers={"Authorization": f"Bearer {token}"} if token else {})


@pytest.fixture()
def tokens(monkeypatch):
    claims = {"good": {"email": SA, "email_verified": True},
              "someone": {"email": "someone@example.com", "email_verified": True}}

    def verify(token, request, audience=None):
        assert audience == scheduler.AUDIENCE
        if token not in claims:
            raise ValueError("bad token")
        return claims[token]

    monkeypatch.setattr(scheduler.id_token, "verify_oauth2_token", verify)
    monkeypatch.setattr(settings, "fraud_scheduler_sa", SA)


def test_the_cycle_endpoint_is_off_unless_set_up(monkeypatch):
    monkeypatch.setattr(settings, "fraud_scheduler_sa", "")
    assert post(TestClient(main.app), "anything").status_code == 404


def test_only_the_schedulers_account_may_start_a_cycle(tokens, fresh):
    client = TestClient(main.app)
    assert post(client).status_code == 401
    assert post(client, "forged").status_code == 401
    assert post(client, "someone").status_code == 401  # a real Google token, but not the scheduler's
    assert fresh == []
    r = post(client, "good")
    assert r.status_code == 200 and len(fresh) == 2


# ---- "Run now" in the portal --------------------------------------------------------------


def test_run_now_on_google_cloud_queues_the_run_and_asks_the_scheduler(monkeypatch, fresh):
    asked = []
    monkeypatch.setattr(settings, "fraud_scheduler_job", "projects/p/locations/r/jobs/fraud-foresight-hourly")
    monkeypatch.setattr(scheduler, "run_now", lambda: asked.append(True))
    r = TestClient(main.app).post(f"/api/platform/fraud/guides/{KEY}/dream")
    assert r.status_code == 200 and r.json()["run"]["status"] == "queued"
    assert asked == [True] and fresh == []  # nothing runs here: the scheduler's request runs it


def test_run_now_records_a_scheduler_failure_and_frees_the_lock(monkeypatch):
    def broken():
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(settings, "fraud_scheduler_job", "projects/p/locations/r/jobs/fraud-foresight-hourly")
    monkeypatch.setattr(scheduler, "run_now", broken)
    r = TestClient(main.app).post(f"/api/platform/fraud/guides/{KEY}/research")
    assert r.status_code == 502
    [run] = store.list("fraud_runs")
    assert run["status"] == "failed" and store.get(*fraud_pipeline.LOCK) is None
    assert store.get("fraud_guides", KEY)["status"] == "current"  # not left "research requested"


def test_run_now_locally_runs_in_the_background(monkeypatch, fresh):
    monkeypatch.setattr(settings, "fraud_scheduler_job", "")
    r = TestClient(main.app).post(f"/api/platform/fraud/guides/{KEY}/research")
    assert r.status_code == 200 and fresh == [("research", KEY, r.json()["run"]["by"])]


# ---- The release keeps the job and checks the access it needs ---------------------------------


def test_the_release_keeps_the_hourly_job_and_tells_the_api():
    repo = pathlib.Path(__file__).resolve().parents[2]
    steps = yaml.safe_load((repo / ".github" / "workflows" / "deploy.yml").read_text())["jobs"]["api"]["steps"]
    job = next(s for s in steps if s.get("name") == "The hourly fraud cycle (Cloud Scheduler)")["run"]
    assert '--schedule "0 * * * *"' in job and "/internal/fraud/cycle" in job
    assert f"--oidc-token-audience {scheduler.AUDIENCE}" in job and "fraud-scheduler@" in job
    deploy_env = next(s for s in steps if s.get("name") == "Deploy API to Cloud Run")["env"]
    assert deploy_env["FRAUD_SCHEDULER_JOB"].endswith("/jobs/fraud-foresight-hourly")
    access = json.loads((repo / "infra" / "deployer-access.json").read_text())
    assert {"cloudscheduler.jobs.create", "cloudscheduler.jobs.update", "cloudscheduler.jobs.get"} <= set(access["project"])
    assert "fraud-scheduler@{project}.iam.gserviceaccount.com" in access["service_accounts"]
    setup = (repo / "infra" / "setup-github-actions.sh").read_text()
    assert "roles/cloudscheduler.admin" in setup and "roles/cloudscheduler.jobRunner" in setup
