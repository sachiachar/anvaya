"""Fraud foresight pipeline (PR-2.5, PR-2.6): imagine, check, map, then add what is confirmed.

Design: docs/architecture.md §4.1. Two agents, each with one job:
  - fraud.dreamer imagines attacks from our own material and maps each to a safeguard. No web access.
  - fraud.researcher checks claims against web pages (Exa search, web_search.py), and researches new
    tactics for a guide.

Rules in code, each with a test:
  - Imagined content never feeds on itself: dreaming starts only from checked tactics, the fixed
    safeguards and guardian events (agent, response and reason only; never case keys or owner data).
  - "Confirmed" means a cited source supports an answer that says the claim
    has been reported, and that answer repeats the claim's own words; a mention isn't enough.
  - Adding on its own (PR-2.7.y, the owner's decision): a tactic or scenario at least 2 independent
    websites confirm is added to the guide by itself, add-only; one source makes a draft for a
    reviewer. A person can remove anything added, and 2 removals in a week pause adding for that
    guide. Unconfirmed scenarios fade after 3 runs.
  - Research subjects rotate (gaps from dreaming first), and dreaming takes each tactic in turn.
  - One failed model call costs one item, not the run: the item is logged as not checked and the
    run goes on. A run fails if it tried checks and none worked, if no imagining worked, or for a
    reason that stops every item (no search key, refused by the gateway, stopped).
  - Limits per run come from policies.yaml: 2 sequences, 3 scenarios each (2 close, 1 far-fetched),
    2 new tactics per guide, and a cap on model calls per agent. One run at a time. A reviewer
    can stop a run; it stops before its next model call and keeps what was checked so far.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from . import fraud_cues, fraud_guides
from .fraud_bundle import records
from .fraud_guides import CHECKABLE, Guide
from .gemini import CutOff, ModelBusy, generate_json
from .governance import core as governance
from .store import new_id, store
from .web_search import NotSetUp, answer_from_web

log = logging.getLogger(__name__)

DREAMER = "fraud.dreamer"
RESEARCHER = "fraud.researcher"

# The safeguards scenarios are mapped to, until the guardian core (PR-2.7) records its own steps.
# Described as they work today, gaps included, so the dreamer can find real blind spots.
SAFEGUARDS: dict[str, str] = {
    "two reviewers": "Two different people on our team must each confirm: one the passing, the other the "
                     "legal authority. Overriding a 'not valid' check needs a written reason.",
    "waiting period": "After both confirmations, the release waits for a set period before anything opens.",
    "owner alert and cancel link": "During the waiting period the owner sees an 'I'm OK' banner in the app and "
                                   "can cancel. There is no email or SMS alert yet.",
    "key code": "When the owner chooses a recipient, both phones show a key code computed from the recipient's "
                "key, and the owner confirms they match.",
    "recipient invitation": "Only the person the owner invited can accept the vault; nobody can make themselves "
                            "the recipient.",
    "certificate checks": "The certificate of passing is checked sign by sign against the fraud reference guide.",
}

FADE_AFTER_RUNS = 3
# Longer than the scheduler waits for a cycle (30 minutes), so a run that is still working is never
# marked failed under it. A queued run waits for the next hourly cycle at most, so it expires later.
RUN_TIMEOUT_SECONDS = 35 * 60
QUEUED_TIMEOUT_SECONDS = 2 * 3600


class Busy(RuntimeError):
    """A fraud run is already in progress."""


class Stopped(Exception):
    """A reviewer stopped the run, or it reached its call limit."""


class Guard:
    """Cost control for one run. Before every model call it checks whether a reviewer pressed
    Stop, and whether the agent has calls left under its max_model_calls_per_run in
    policies.yaml. It also saves the counts after each step, so the portal shows progress live."""

    def __init__(self, run: dict):
        self.run = run
        self.calls: dict[str, int] = {}

    def call(self, agent: str, *, finishing: bool = False) -> None:
        """`finishing` marks the last call for a scenario already checked: Stop waits for it,
        so a scenario a source has confirmed (and we've paid for) isn't thrown away."""
        if not finishing and (store.get("fraud_runs", self.run["id"]) or {}).get("stop_requested"):
            raise Stopped("stopped by a reviewer")
        cap = governance.limit(agent, "max_model_calls_per_run")
        if cap is not None and self.calls.get(agent, 0) >= cap:
            raise Stopped(f"call limit reached for {agent}")
        self.calls[agent] = self.calls.get(agent, 0) + 1
        store.update("fraud_runs", self.run["id"], {"calls": self.calls})

    def progress(self, counts: dict) -> None:
        store.update("fraud_runs", self.run["id"], {"counts": counts})

    def say(self, stage: Literal["imagine", "check", "map", "save", "research", "skip"], text: str,
            **detail) -> None:
        """One line in the live view: which stage the run is in and what it's doing. Only guide
        and scenario text, never owner data. The last 80 lines are kept."""
        self.steps = [*getattr(self, "steps", []), {"at": time.time(), "stage": stage, "line": text, **detail}][-80:]
        store.update("fraud_runs", self.run["id"], {"steps": self.steps, "stage": stage})


# ---- What the models return ------------------------------------------------------------


class DocumentSign(BaseModel):
    look_for: str = Field(description="What would show on the document itself")
    genuine: str = Field(description="What a genuine document shows instead")


class ImaginedScenario(BaseModel):
    title: str = Field(description="One sentence naming the attack")
    steps: list[str] = Field(description="The attacker's steps, in order")
    targets: str = Field(description="The safeguard it tries to get around: one of the names given")
    variant: Literal["close", "far"] = Field(description="close: changes one or two steps of the known "
                                                         "sequence; far: deliberately unusual")
    claim: str = Field(description="One sentence a published source would have to report for this to be real")
    cue: str | None = Field(default=None, description="If it exploits one of the common gaps listed, that gap's "
                                                      "pattern name exactly (e.g. policy.nominee_missing); else null")
    document_sign: DocumentSign | None = Field(default=None, description="Only if it would leave a mark on the "
                                                                          "certificate itself")


class Imagined(BaseModel):
    scenarios: list[ImaginedScenario]


class Coverage(BaseModel):
    caught_by: str = Field(description="The name of the safeguard that would catch it, or 'none'")
    partly: bool = Field(description="True if that safeguard would only sometimes catch it")
    reason: str = Field(description="Why, in one sentence")


class NewStep(BaseModel):
    text: str
    caught_by: str = Field(description="A warning sign id or a safeguard name from the lists given, or 'none'")


class NewTactic(BaseModel):
    name: str
    steps: list[NewStep]
    sign: DocumentSign | None = None
    claim: str = Field(description="One sentence the sources report about this tactic")


class NewTactics(BaseModel):
    tactics: list[NewTactic]


# ---- The source check (rule 3) ---------------------------------------------------------

_STOP = {"the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with", "by", "is", "are", "was", "were",
         "be", "that", "this", "it", "as", "at", "from", "their", "they", "who", "which", "someone", "person"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


# ---- Same topic: research and dreaming never add the same fraud twice -------------------
#
# The owner's rule: if research and dreaming end up on a topic already in the lists, it isn't
# added again. One list of what's known for a guide (its tactics and signs, every scenario,
# ruled-out ones included, and every tactic waiting for review or added on its own) is checked by
# both. Two items are the same topic when their titles mostly share the same words, after words
# that name the same thing are folded together ("forged" and "fake", "seal" and "stamp"), or
# their descriptions do.

_SAME_WORD = {
    "fake": ("forged", "forge", "forgery", "forgeries", "counterfeit", "fraudulent", "bogus", "false", "fabricated",
             "doctored", "altered", "tampered", "manipulated", "edited", "falsified"),
    "seal": ("stamp", "stamps", "seals", "stamped", "sealed"),
    "impersonate": ("impersonation", "impersonating", "impostor", "imposter", "posing", "pose", "pretend",
                    "pretending", "masquerading"),
    "nominee": ("beneficiary", "beneficiaries", "heir", "heirs", "claimant", "claimants"),
    "official": ("officer", "officers", "clerk", "clerks", "registrar", "staff", "employee", "insider", "insiders"),
    "certificate": ("certificates", "cert", "document", "documents"),
    "death": ("deceased", "dead", "demise"),
}
_FOLD = {w: key for key, words in _SAME_WORD.items() for w in words}
# Labels and filler, never a topic on their own ("Attack ...", "Tactic ...", "a new variant ...").
_GENERIC = {"fraud", "scam", "scheme", "using", "use", "used", "via", "new", "variant", "method", "way", "ways",
            "attack", "attacks", "tactic", "tactics", "scenario", "scenarios"}


def _stem(word: str) -> str:
    for end, keep in (("ing", 5), ("ed", 4), ("es", 4), ("s", 3)):
        if word.endswith(end) and len(word) > keep:
            return word[: -len(end)]
    return word


def _topic(text: str) -> set[str]:
    return {_stem(_FOLD.get(w, w)) for w in _words(text or "") if w not in _GENERIC}


def same_topic(a: dict, b: dict) -> bool:
    """Whether two items ({"title", "text"}) are about the same fraud."""
    ta, tb = _topic(a.get("title", "")), _topic(b.get("title", ""))
    shared = len(ta & tb)
    if ta and tb and (shared / len(ta | tb) >= 0.5 or (min(len(ta), len(tb)) >= 2 and shared / min(len(ta), len(tb)) >= 0.75)):
        return True
    # Descriptions count only when the titles share something: two different frauds can be
    # described in similar words, but a reworded repeat keeps at least one word of its title.
    ca, cb = _topic(a.get("text", "")), _topic(b.get("text", ""))
    return shared > 0 and bool(ca and cb) and len(ca & cb) / len(ca | cb) >= 0.45


def known_topics(guide: Guide) -> list[dict]:
    """Everything already known for a guide, for research and dreaming alike."""
    known = [{"title": t.name, "text": " ".join(s.text for s in t.steps)} for t in guide.tactics]
    known += [{"title": s.look_for, "text": ""} for s in guide.signs]
    known += [{"title": s.get("scenario", ""), "text": s.get("claim", "")}
              for s in records.list("fraud_scenarios", guide_key=guide.key)]
    known += [{"title": d.get("tactic", ""), "text": d.get("claim", "")}
              for d in records.list("fraud_drafts", guide_key=guide.key) if d.get("kind", "tactic") == "tactic"]
    return [k for k in known if k["title"] or k["text"]]


def already_known(item: dict, known: list[dict]) -> dict | None:
    return next((k for k in known if same_topic(item, k)), None)


# Guide text goes into the certificate check's instructions once a reviewer approves it, so
# text from the web must read as a description of a document, never as instructions.
MAX_SIGN_CHARS = 300
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|instruction|prompt|system|you must|you should|rul(?:e|ed|ing) out|confidence|verdict|"
    r"approve|likely[_ ]valid|report (?:every|all)|always|never|not applicable|does not apply|doesn't apply|"
    r"other signs?|remaining signs?|these signs|listed signs?|treat (?:it |this )?as|skip|is normal|are normal)\b",
    re.IGNORECASE)


def _safe_sign(sign: DocumentSign) -> bool:
    """A warning sign may join a guide only if both its parts are short and read as a
    description, with nothing that looks like an instruction to the checking model."""
    return all(0 < len(t) <= MAX_SIGN_CHARS and not _INSTRUCTION.search(t) for t in (sign.look_for, sign.genuine))


def _web_url(url: str) -> bool:
    return url.startswith(("https://", "http://"))


class Evidence(BaseModel):
    confirmed: bool
    sources: list[dict] = []  # {"title", "url"}
    summary: str = ""


def judge(claim: str, answer_text: str, sources: list[tuple[str, str]],
          supports: list[tuple[str, list[int]]]) -> Evidence:
    """Rule 3, in code. Confirmed only when the answer says YES, and at least one support (a source
    whose quote was found word for word in the page) links a source to a sentence of the answer
    that shares at least a third of the claim's key words. A source that only mentions a word, or a
    bare YES, isn't enough."""
    lines = answer_text.strip().splitlines()
    first = lines[0].strip().upper() if lines else ""
    summary = " ".join(lines[1:]).strip()[:600]
    if not first.startswith("YES"):
        return Evidence(confirmed=False, summary=summary)
    key = _words(claim)
    backing: set[int] = set()
    for segment, indexes in supports:
        words = _words(segment)
        if indexes and len(segment) >= 40 and key and len(words & key) * 3 >= len(key):
            backing.update(i for i in indexes if 0 <= i < len(sources))
    found = [{"title": sources[i][0], "url": sources[i][1]} for i in sorted(backing) if _web_url(sources[i][1])]
    return Evidence(confirmed=bool(found), sources=found, summary=summary)


async def check_claim(claim: str) -> Evidence:
    """Ask fraud.researcher whether a web source reports this claim (web_search.py: our code searches,
    and a source counts only if the model's quote from it really appears in the page)."""
    answer = await answer_from_web(
        agent=RESEARCHER,
        query=claim,
        claim=claim,
        system=("You check claims about fraud for a fraud-prevention team. Say YES only if a page describes "
                "this kind of fraud happening or being attempted."),
        question=(f'Claim: "{claim}"\nIs this reported by a news story, an official notice, a court record or a '
                  "study? Put YES or NO as the first line. Then, in two or three sentences, say what the "
                  "pages report, using the claim's own words where they fit."),
    )
    return judge(claim, answer.text, answer.sources, answer.supports)


# ---- Runs: one at a time, every one logged ---------------------------------------------


LOCK = ("fraud_run_lock", "active")


def expire_stale(now: float | None = None) -> None:
    """A run still 'running' or 'queued' after the timeout (for example after a server restart, or a
    scheduler that never called) is marked failed and its lock released, so new runs can start."""
    now = now or time.time()
    for status, limit in (("running", RUN_TIMEOUT_SECONDS), ("queued", QUEUED_TIMEOUT_SECONDS)):
        for run in store.list("fraud_runs", status=status):
            if now - run["started_at"] >= limit:
                store.update("fraud_runs", run["id"], {"status": "failed", "error": "timed out", "finished_at": now})
    lock = store.get(*LOCK)
    held = store.get("fraud_runs", lock["run_id"]) if lock else None
    limit = QUEUED_TIMEOUT_SECONDS if held and held["status"] == "queued" else RUN_TIMEOUT_SECONDS
    if lock and now - lock["at"] >= limit:
        store.delete(*LOCK)


def start_run(kind: Literal["dream", "research"], guide_key: str, by: str, *, queued: bool = False) -> dict:
    """Record a run before it starts. One at a time: a create-only lock, so two requests at
    the same moment can't both start one. `queued`: the scheduled cycle will run it (a reviewer's
    "Run now" on Google Cloud), so it holds the lock until then."""
    now = time.time()
    expire_stale(now)
    run_id = new_id("run")
    if not store.create(LOCK[0], {"id": LOCK[1], "run_id": run_id, "at": now}):
        raise Busy("A fraud run is already in progress. Try again when it finishes.")
    limits = {k: governance.limit(a, k) for a, k in (
        (DREAMER, "max_sequences_per_run"), (DREAMER, "max_scenarios_per_sequence"),
        (RESEARCHER, "max_new_patterns_per_run"), (RESEARCHER, "max_subjects_per_run"))}
    limits |= {f"{a}.max_model_calls_per_run": governance.limit(a, "max_model_calls_per_run") for a in (DREAMER, RESEARCHER)}
    return store.put("fraud_runs", {"id": run_id, "kind": kind, "guide_key": guide_key, "by": by,
                                    "status": "queued" if queued else "running", "started_at": now, "limits": limits,
                                    "counts": {}, "calls": {}, "stop_requested": False})


# ---- The scheduled cycle (hourly, Cloud Scheduler) ------------------------------------------
#
# Runs happen inside the scheduler's request, so Cloud Run keeps the CPU on until they finish. A run
# started in the background after a reply gets almost no CPU there and stalls until it times out.

SCHEDULER = "service:fraud-scheduler"  # who starts the hourly runs: no person, so the platform key


async def execute(run: dict) -> dict:
    """Run one recorded run to the end, here and now. A queued run that was stopped, or has timed
    out, since it was listed isn't brought back."""
    current = store.get("fraud_runs", run["id"]) or {}
    if current.get("status") not in ("queued", "running"):
        return current or run
    now = time.time()
    lock = store.get(*LOCK)
    if lock and lock.get("run_id") == run["id"]:
        store.update(*LOCK, {"at": now})  # the run's time limit counts from when it really starts
    run = store.update("fraud_runs", run["id"], {"status": "running", "started_at": now})
    return await (dream if run["kind"] == "dream" else research)(run)


def cancel_queued(run: dict, reason: str) -> dict:
    """A queued run that can't start (the scheduler couldn't be asked): finished as failed, its lock
    freed, and a guide waiting for research shown as current again."""
    if store.get("fraud_guides", run["guide_key"]):
        store.update("fraud_guides", run["guide_key"], {"status": "current"})
    return _finish(run, {}, reason)


def _fraud_tokens_today() -> int:
    day = store.get("usage_days", datetime.now(timezone.utc).strftime("%Y-%m-%d")) or {}
    return sum(a.get("tokens_in", 0) + a.get("tokens_out", 0) for name, a in (day.get("agents") or {}).items()
               if name in (DREAMER, RESEARCHER))


async def cycle() -> dict:
    """One scheduled cycle: the runs a reviewer queued with "Run now" if there are any, otherwise
    research and then dreaming for each guide. A guide whose run can't start (one is already going)
    is skipped until the next hour, and so is the whole hour once the fraud agents have used their
    share of the day's tokens (policies.yaml service_ceiling.scheduled_fraud_share)."""
    queued = store.list("fraud_runs", status="queued")  # before expiring, so a waiting request is honoured
    if queued:
        return {run["id"]: (await execute(run))["status"] for run in sorted(queued, key=lambda r: r["started_at"])}
    expire_stale()
    ceiling = governance.service_ceiling()
    if ceiling and _fraud_tokens_today() >= ceiling["tokens_per_day"] * ceiling.get("scheduled_fraud_share", 0.2):
        return {"skipped": "the fraud agents have used their share of today's tokens"}
    out = {}
    for key in fraud_guides.BUILT_IN:
        fraud_guides.ensure_saved(key)
        for kind in ("research", "dream"):
            try:
                run = start_run(kind, key, SCHEDULER)
            except Busy:
                out[f"{kind}:{key}"] = "skipped: a run is in progress"
                continue
            out[f"{kind}:{key}"] = (await execute(run))["status"]
    return out


def request_stop(run_id: str) -> dict:
    """Ask a running run to stop. It finishes the scenario in hand, then stops."""
    run = store.get("fraud_runs", run_id)
    if run and run["status"] == "queued":  # not begun yet: stop it now, and free the lock
        return _finish(run, {}, stopped="stopped by a reviewer before it began")
    if not run or run["status"] != "running":
        raise KeyError(run_id)
    return store.update("fraud_runs", run_id, {"stop_requested": True})


# Our own errors carry a plain message a reviewer can act on; anything else shows only its type,
# so no page text or model output reaches the portal.
_PLAIN = (ModelBusy, CutOff, NotSetUp, governance.Refused)
# Reasons that stop every item, so the run stops; any other failure costs only the item in hand.
_RUN_LEVEL = (Stopped, NotSetUp, governance.Refused)


def _reason(e: Exception) -> str:
    if isinstance(e, _PLAIN):
        return str(e)
    if isinstance(e, genai_errors.APIError):  # the code only: the message can carry model or page text
        return f"Gemini error {e.code}"
    return type(e).__name__


def _finish(run: dict, counts: dict, error: str | None = None, stopped: str | None = None) -> dict:
    status = "stopped" if stopped else "failed" if error else "done"
    lock = store.get(*LOCK)
    if lock and lock.get("run_id") == run["id"]:
        store.delete(*LOCK)
    return store.update("fraud_runs", run["id"], {
        "status": status, "finished_at": time.time(), "counts": counts, "error": error, "stopped_reason": stopped})


# ---- Dreaming (PR-2.6) -----------------------------------------------------------------


def _seeds(guide: Guide) -> list:
    """Rule 1: only checked, active tactics seed new scenarios. Each tactic is dreamed on in turn:
    ones never dreamed on first, in the order they joined the guide (so each newly researched tactic
    gets its turn), then the one dreamed on longest ago."""
    tactics = [t for t in guide.tactics if t.label in CHECKABLE and t.status == "active"]
    dreamed = {d["tactic"]: d.get("at", 0) for d in store.list("fraud_dreamed", guide_key=guide.key)}
    tactics.sort(key=lambda t: (t.id in dreamed, dreamed.get(t.id, 0)))  # stable: guide order among equals
    return tactics[: int(governance.limit(DREAMER, "max_sequences_per_run", 2))]


def _dreamed(guide_key: str, tactic_id: str) -> None:
    store.put("fraud_dreamed", {"id": f"{guide_key}|{tactic_id}", "guide_key": guide_key, "tactic": tactic_id,
                                "at": time.time()})


def _recent_events(n: int = 10) -> list[dict]:
    """What the guardian has seen lately: agent, response and reason only. Never case keys. Not the
    daily-limit refusals (PR-15.6): they're routine, and would push out the signs worth learning from."""
    # Nor its own gap reports: imagined content never feeds on itself (rule 1).
    events = sorted((e for e in store.list("guardian_events")
                     if not e["id"].startswith((*governance.LIMIT_EVENT_IDS, "gev_gap_"))),
                    key=lambda e: -e.get("at", 0))[:n]
    return [{"agent": e.get("agent"), "response": e.get("response"), "reason": e.get("reason")} for e in events]


def _ruled_out(guide_key: str, n: int = 20) -> list[str]:
    """Rule 6: rejected scenarios go back to the dreamer as 'already ruled out'."""
    rejected = records.list("fraud_scenarios", guide_key=guide_key, label="invalidated")
    return [s["scenario"] for s in sorted(rejected, key=lambda s: -s["at"])[:n]]


def _pick(scenarios: list[ImaginedScenario], existing: list[dict]) -> list[ImaginedScenario]:
    """At most 2 close variants and 1 far-fetched one, aimed at a real safeguard, and on a topic not
    already known (known_topics: tactics, drafts and scenarios alike)."""
    cap = int(governance.limit(DREAMER, "max_scenarios_per_sequence", 3))
    picked: list[ImaginedScenario] = []
    for variant, most in (("close", cap - 1), ("far", 1)):
        for s in scenarios:
            if len([p for p in picked if p.variant == variant]) >= most:
                break
            s.targets = s.targets.strip().lower()
            if s.variant != variant or s.targets not in SAFEGUARDS:
                continue
            if s.document_sign and not _safe_sign(s.document_sign):
                s.document_sign = None  # the scenario stays; the mark on the document is dropped
            item = {"title": s.title, "text": s.claim}
            if already_known(item, [*existing, *({"title": p.title, "text": p.claim} for p in picked)]):
                continue
            picked.append(s)
    return picked


async def _imagine(guide: Guide, tactic, events: list[dict], ruled_out: list[str],
                   cues: list[dict] | None = None) -> list[ImaginedScenario]:
    sequence = "\n".join(f"{i}. {s.text} (caught by: {s.caught_by})" for i, s in enumerate(tactic.steps, 1))
    safeguards = "\n".join(f"- {name}: {how}" for name, how in SAFEGUARDS.items())
    prompt = (
        f"Document: {guide.title}.\nKnown fraud sequence: {tactic.name}\n{sequence}\n\n"
        f"Our safeguards:\n{safeguards}\n\n"
        f"Recent guardian events: {events or 'none'}\n"
        f"Common gaps in what owners have recorded (counts only, across all owners):\n{fraud_cues.describe(cues or [])}\n"
        f"Already ruled out (don't repeat): {ruled_out or 'none'}\n\n"
        "Imagine new variants of this sequence designed to get past the safeguards and avoid the known warning "
        "signs: two close variants that change one or two steps, and one deliberately far-fetched variant. "
        "Where it fits, aim a variant at one of the common gaps, and name its pattern in `cue`."
    )
    imagined = await generate_json(
        agent=DREAMER, schema=Imagined, prompt=prompt,
        system=("You think like a fraudster to help a fraud-prevention team find blind spots before real "
                "attackers do. Describe attacks at the level of steps and safeguards, never operational detail "
                "such as tools, templates or where to buy them. Each scenario targets one safeguard by its exact "
                "name from the list."),
    )
    return imagined.scenarios


async def _map(scenario: ImaginedScenario) -> tuple[str | None, str, str]:
    """Which safeguard would catch it. Returns (caught_by, coverage, reason); names outside the
    fixed list count as 'none'."""
    safeguards = "\n".join(f"- {name}: {how}" for name, how in SAFEGUARDS.items())
    result = await generate_json(
        agent=DREAMER, schema=Coverage,
        system="You judge which of a fixed list of safeguards would stop an attack, exactly as each is described.",
        prompt=f"Safeguards:\n{safeguards}\n\nAttack: {scenario.title}\nSteps: {scenario.steps}",
    )
    caught = result.caught_by.strip().lower()
    if caught not in SAFEGUARDS:
        return None, "gap", result.reason
    return caught, "partly" if result.partly else "covered", result.reason


def _fade(guide_key: str) -> int:
    """Rule 5: unconfirmed scenarios are archived after 3 runs."""
    faded = 0
    for s in records.list("fraud_scenarios", guide_key=guide_key, review="unconfirmed"):
        seen = s.get("runs_seen", 0) + 1
        fields = {"runs_seen": seen}
        if seen >= FADE_AFTER_RUNS:
            fields["review"] = "faded"
            faded += 1
        records.update("fraud_scenarios", s["id"], fields)
    return faded


async def dream(run: dict) -> dict:
    """One dreaming run for a guide. Never raises: a failure is recorded on the run."""
    governance.set_current_user(run["by"])  # every step uses the starter's own key first (PR-10.10)
    counts = {"sequences": 0, "imagined": 0, "kept": 0, "confirmed": 0, "unconfirmed": 0, "faded": 0}
    try:
        guide = fraud_guides.current(run["guide_key"])
        if guide is None:
            return _finish(run, counts, "guide not found")
        counts["faded"] = _fade(guide.key)
        events, ruled_out = _recent_events(), _ruled_out(guide.key)
        # Starting points from kept knowledge: anonymous counts only (fraud_cues.py). The dreamer
        # never reads a bundle.
        # Reading every owner's catalog blocks, so it runs off the event loop.
        await asyncio.to_thread(fraud_cues.ensure_synthetic_owners)
        cues = await asyncio.to_thread(fraud_cues.cues)
        by_pattern = {c["pattern"]: c for c in cues}
        existing = known_topics(guide)  # what research found counts too: the same topic is never added twice
        guard = Guard(run)
        guard.say("imagine", f"Starting points from kept knowledge: {len(cues)} common gap(s) across owners "
                          "(counted, never read)" + "".join(f"; {c['owners']} owners: {c['means']}" for c in cues[:3]))
        failed: list[str] = []
        # The run fails if it tried checks and none worked, or if no imagining worked. Imagining
        # that finds nothing new is a real outcome, but it never counts as a check.
        imagined_ok = checks_ok = checks_failed = 0

        def not_checked(what: str, e: Exception, **detail) -> None:
            failed.append(_reason(e))
            counts["not_checked"] = counts.get("not_checked", 0) + 1
            guard.progress(counts)
            guard.say("skip", f"Couldn't {what}: {_reason(e)}. It can come up again in a later run.", **detail)

        for tactic in _seeds(guide):
            counts["sequences"] += 1
            guard.call(DREAMER)
            guard.say("imagine", f"Imagining new ways around the safeguards, starting from “{tactic.name}”")
            try:
                imagined = await _imagine(guide, tactic, events, ruled_out, cues)
            except _RUN_LEVEL:
                raise
            except Exception as e:  # one sequence lost; the next may still work
                log.exception("fraud dreaming run %s: imagining failed", run["id"])
                not_checked(f"imagine from “{tactic.name}”", e)
                continue
            imagined_ok += 1
            _dreamed(guide.key, tactic.id)  # this tactic's turn is taken; the next run takes the next one
            counts["imagined"] += len(imagined)
            guard.progress(counts)
            picked = _pick(imagined, existing)
            counts["already_known"] = counts.get("already_known", 0) + len(imagined) - len(picked)
            guard.say("imagine", f"Imagined {len(imagined)}; keeping {len(picked)} new ones "
                                 "(2 close variants, 1 far-fetched; topics already known aren't kept)")
            for scenario in picked:
                guard.call(RESEARCHER)
                guard.say("check", f"Searching the web for evidence: “{scenario.claim}”", title=scenario.title)
                try:
                    evidence = await check_claim(scenario.claim)
                    guard.say("check", f"Confirmed by {len(evidence.sources)} cited source(s)" if evidence.confirmed
                              else "No source confirms it yet", title=scenario.title, confirmed=evidence.confirmed)
                    guard.call(DREAMER, finishing=True)
                    guard.say("map", f"Which safeguard would catch “{scenario.title}”?", title=scenario.title)
                    caught_by, coverage, reason = await _map(scenario)
                except _RUN_LEVEL:
                    raise
                except Exception as e:  # this scenario isn't saved; the run goes on to the next
                    log.exception("fraud dreaming run %s: checking a scenario failed", run["id"])
                    not_checked(f"check “{scenario.title}”", e, title=scenario.title)
                    checks_failed += 1
                    continue
                checks_ok += 1
                saved = records.put("fraud_scenarios", {
                    "id": new_id("scn"), "at": time.time(), "run_id": run["id"], "guide_key": guide.key,
                    "sequence_id": tactic.id, "scenario": scenario.title, "steps": scenario.steps,
                    "variant": scenario.variant, "claim": scenario.claim, "safeguard": scenario.targets,
                    "caught_by": caught_by, "coverage": coverage, "coverage_reason": reason,
                    "document_sign": scenario.document_sign.model_dump() if scenario.document_sign else None,
                    "label": "simulated", "status": "verified" if evidence.confirmed else "dreamt",
                    "sources": evidence.sources, "evidence": len(evidence.sources), "source_note": evidence.summary,
                    "review": "pending" if evidence.confirmed else "unconfirmed", "runs_seen": 0,
                    # Only a pattern from the closed list, and only one this run's cues had (no free text).
                    "cue": by_pattern[scenario.cue] if scenario.cue in by_pattern else None,
                })
                existing.append({"title": scenario.title, "text": scenario.claim})
                counts["kept"] += 1
                counts["confirmed" if evidence.confirmed else "unconfirmed"] += 1
                added = can_add(guide.key, evidence)
                if added:  # 2 independent websites report it: validated on its own (PR-2.7.y)
                    try:
                        approve_scenario(saved, auto=True)
                        records.update("fraud_scenarios", saved["id"], {"review": "added", "label": "validated"})
                        _record_added(guide.key, "scenario", saved["id"], scenario.title, [saved["id"]],
                                      independent_sources(evidence.sources))
                        counts["added"] = counts.get("added", 0) + 1
                    except Exception as e:  # it stays a confirmed scenario for a reviewer; the run goes on
                        log.exception("fraud dreaming run %s: adding a scenario failed", run["id"])
                        not_checked(f"add “{scenario.title}”", e, title=scenario.title)
                        added = False
                # A gap sources confirm is the sign of something new: the team is told; nothing waits on it.
                # Imagined gaps no source reports stay on the coverage map only.
                if coverage == "gap" and evidence.confirmed:
                    governance.record_intervention(
                        DREAMER, "add_check", f"Dreaming found a gap: no safeguard catches an attack on the {scenario.targets}",
                        user_key=governance.SERVICE, once=f"gev_gap_{saved['id']}")
                guard.progress(counts)
                guard.say("save", (f"Caught by {caught_by}" if caught_by else "Gap: no safeguard catches it")
                          + (" (only sometimes)" if coverage == "partly" else "")
                          + ("; added on its own" if added else "; waiting for a reviewer" if evidence.confirmed
                             else "; added as unconfirmed"),
                          title=scenario.title, coverage=coverage)
        if failed and not checks_ok and (checks_failed or not imagined_ok):
            return _finish(run, counts, f"Nothing could be checked: {failed[-1]}")
        return _finish(run, counts)
    except Stopped as e:  # what was checked so far is kept
        return _finish(run, counts, stopped=str(e))
    except Exception as e:  # recorded on the run, shown in the portal; never the content
        log.exception("fraud dreaming run %s failed", run["id"])
        return _finish(run, counts, _reason(e))


# ---- Research subjects: they rotate, gaps first (PR-2.7.y) ------------------------------------
#
# Each guide has a list of subjects, built from what dreaming found nobody catches (gaps), its
# warning signs, our safeguards, a few fixed open subjects, and its known tactics. Each research run
# takes the next ones: a gap never researched first, then the one researched least recently. A
# subject that finds nothing new 3 times in a row rests for a week. So research covers many angles
# and keeps looking for fraud unlike anything already known.

OPEN_SUBJECTS: dict[str, list[str]] = {  # reviewed like code
    "IN.death_certificate": [
        "new death certificate fraud reported in India in the last 12 months",
        "insurance claim fraud using a fake death certificate in India",
        "bank account takeover after the account holder's death in India",
        "people posing as the legal heir or nominee to claim a dead person's money in India",
        "fake succession certificate or legal heir certificate fraud in India",
    ],
}
SUBJECT_REST_AFTER = 3              # runs in a row that find nothing new
SUBJECT_REST_SECONDS = 7 * 86400


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def subjects(guide: Guide) -> list[dict]:
    """Every research subject for a guide, in priority order: gaps, signs, safeguards, open subjects, tactics."""
    tactics = [{"title": t.name, "text": " ".join(x.text for x in t.steps)} for t in guide.tactics]
    gaps = [s for s in records.list("fraud_scenarios", guide_key=guide.key)
            if s.get("coverage") == "gap" and s.get("label") != "invalidated"
            and s.get("review") not in ("faded", "discarded", "removed")
            and not already_known({"title": s.get("scenario", ""), "text": s.get("claim", "")}, tactics)]
    return [
        *({"id": f"gap:{s['id']}", "kind": "gap", "about": s["claim"]} for s in sorted(gaps, key=lambda s: s["at"])),
        *({"id": f"sign:{s.id}", "kind": "sign", "about": f"how forgers fake or get around this: {s.look_for}"}
          for s in guide.signs if s.status == "active"),
        *({"id": f"safeguard:{_slug(name)}", "kind": "safeguard",
           "about": f"ways criminals get around a safeguard like this in inheritance or death-claim fraud: {how}"}
          for name, how in SAFEGUARDS.items()),
        *({"id": f"open:{_slug(text)}", "kind": "open", "about": text} for text in OPEN_SUBJECTS.get(guide.key, [])),
        *({"id": f"tactic:{t.id}", "kind": "tactic", "about": f"new variants of this fraud: {t.name}"}
          for t in guide.tactics if t.status == "active"),
    ]


def _subject_state(guide_key: str) -> dict[str, dict]:
    return {d["subject"]: d for d in store.list("fraud_subjects", guide_key=guide_key)}


def pick_subjects(guide: Guide, n: int, now: float | None = None) -> list[dict]:
    """The next n subjects: a gap never researched first, then the least recently researched; never a resting one."""
    now = now or time.time()
    state = _subject_state(guide.key)
    ready = [s for s in subjects(guide) if state.get(s["id"], {}).get("rested_until", 0) <= now]
    ready.sort(key=lambda s: state.get(s["id"], {}).get("last_at", 0))  # least recently researched first
    # A new gap goes first, but only one a run, so dreaming's gaps never crowd out the other subjects.
    new_gap = next((s for s in ready if s["kind"] == "gap" and s["id"] not in state), None)
    rest = [s for s in ready if s is not new_gap and not (s["kind"] == "gap" and s["id"] not in state)]
    return ([new_gap] if new_gap else [])[:n] + rest[: n - (1 if new_gap else 0)]


def subject_done(guide_key: str, subject: dict, found_new: bool, now: float | None = None) -> None:
    now = now or time.time()
    doc_id = f"{guide_key}|{subject['id']}"
    before = store.get("fraud_subjects", doc_id) or {}
    streak = 0 if found_new else before.get("empty_streak", 0) + 1
    store.put("fraud_subjects", {
        "id": doc_id, "guide_key": guide_key, "subject": subject["id"], "kind": subject["kind"], "last_at": now,
        "runs": before.get("runs", 0) + 1, "empty_streak": streak,
        "rested_until": now + SUBJECT_REST_SECONDS if streak >= SUBJECT_REST_AFTER else 0})


# ---- Adding on its own (PR-2.7.y): add-only, two independent sources -------------------------
#
# The owner decided the pipeline adds what it finds without a reviewer approving each item. Rules in
# code: it only adds (a tactic, its warning sign, a validated scenario), never removes or weakens
# anything, and never touches a release; it adds only what at least 2 independent websites confirm
# (one source makes a draft, as before); 1 in 10 added items is marked for an optional spot check;
# a person can retire any added item, and 2 removals in a week pause adding for that guide until
# someone resumes it.

AUTO_MIN_SOURCES = 2
SPOT_CHECK_EVERY = 10
PAUSE_AFTER_REMOVALS = 2
REMOVAL_WINDOW_SECONDS = 7 * 86400


# Second-level suffixes where a site's own name is the third label (tribune.co.in, gov.in sites).
_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu", "nic", "res", "gen", "firm", "ind", "ltd", "mil"}
# Hosts where anyone can publish under their own subdomain or page: they never count as independent.
_OPEN_HOSTS = {"blogspot.com", "medium.com", "wordpress.com", "github.io", "substack.com", "wixsite.com",
               "weebly.com", "tumblr.com", "notion.site", "sites.google.com", "google.com", "linkedin.com",
               "facebook.com", "x.com", "twitter.com", "quora.com", "reddit.com", "youtube.com", "instagram.com",
               "telegram.me", "t.me", "scribd.com", "slideshare.net", "pinterest.com"}


def _site(url: str) -> str | None:
    """The website a page belongs to: its registrable domain (a.x.com and b.x.com are one site), or
    None for a host anyone can publish on."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    labels = host.split(".")
    if len(labels) < 2:
        return None
    take = 3 if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2 else 2
    site = ".".join(labels[-take:])
    if site in _OPEN_HOSTS or ".".join(labels[-3:]) in _OPEN_HOSTS:
        return None
    return site


def independent_sources(sources: list[dict]) -> int:
    """How many different websites confirm it. Pages of one site, or of one site's subdomains, count
    once; blogs and social sites anyone can publish on don't count at all."""
    return len({site for s in sources if (site := _site(s.get("url", "")))})


def adding_paused(guide_key: str) -> bool:
    return bool((store.get("fraud_autonomy", guide_key) or {}).get("paused"))


def can_add(guide_key: str, evidence: Evidence) -> bool:
    return evidence.confirmed and independent_sources(evidence.sources) >= AUTO_MIN_SOURCES and not adding_paused(guide_key)


def _record_added(guide_key: str, kind: str, item_id: str, name: str, retires: list[str], sources: int) -> dict:
    """The portal's list of what the pipeline added on its own, each with a way to retire it."""
    store.increment("fraud_auto_counts", guide_key, {"added": 1}, {"guide_key": guide_key})
    count = (store.get("fraud_auto_counts", guide_key) or {}).get("added", 1)
    return records.put("fraud_auto", {
        "id": item_id, "at": time.time(), "guide_key": guide_key, "kind": kind, "name": name, "retires": retires,
        "sources": sources, "spot_check": count % SPOT_CHECK_EVERY == 1, "status": "active"})


def remove_added(item_id: str) -> dict:
    """A person retires something the pipeline added. Two removals in a week pause adding for that guide."""
    item = records.get("fraud_auto", item_id)
    if not item or item["status"] != "active":
        raise KeyError(item_id)
    fraud_guides.retire_items(item["guide_key"], set(item["retires"]))
    item = records.update("fraud_auto", item_id, {"status": "removed", "removed_at": time.time()})
    if item["kind"] == "scenario" and records.get("fraud_scenarios", item_id):  # ruled out from now on (rule 6)
        records.update("fraud_scenarios", item_id, {"review": "removed", "label": "invalidated"})
    resumed = (store.get("fraud_autonomy", item["guide_key"]) or {}).get("resumed_at", 0)
    since = max(time.time() - REMOVAL_WINDOW_SECONDS, resumed)  # removals before a resume don't count again
    removed = [a for a in records.list("fraud_auto", guide_key=item["guide_key"], status="removed")
               if a.get("removed_at", 0) > since]
    if len(removed) >= PAUSE_AFTER_REMOVALS:
        store.put("fraud_autonomy", {"id": item["guide_key"], "paused": True, "paused_at": time.time()})
    return item


def resume_adding(guide_key: str) -> None:
    store.put("fraud_autonomy", {"id": guide_key, "paused": False, "resumed_at": time.time()})


# ---- Research (PR-2.5) -----------------------------------------------------------------


def _source_id(url: str) -> str:
    return "src_" + hashlib.sha256(url.encode()).hexdigest()[:10]


async def research(run: dict) -> dict:
    """Look for new tactics for one guide, subject by subject (pick_subjects). A tactic 2 independent
    websites confirm is added to the guide on its own; one source makes a draft for a reviewer; if
    nothing is confirmed, nothing changes."""
    governance.set_current_user(run["by"])  # every step uses the starter's own key first (PR-10.10)
    counts = {"subjects": 0, "found": 0, "confirmed": 0, "added": 0, "drafts": 0, "unconfirmed": 0}
    try:
        guide = fraud_guides.current(run["guide_key"])
        if guide is None:
            return _finish(run, counts, "guide not found")
        known = known_topics(guide)  # what dreaming found counts too: the same topic is never added twice
        guard = Guard(run)
        cap = int(governance.limit(RESEARCHER, "max_new_patterns_per_run", 2))
        failed: list[str] = []
        checked = checks_failed = researched = 0
        for subject in pick_subjects(guide, int(governance.limit(RESEARCHER, "max_subjects_per_run", 2))):
            if counts["found"] >= cap:
                break
            counts["subjects"] += 1
            guard.call(RESEARCHER)
            guard.say("research", f"Searching the web ({subject['kind']}): {subject['about'][:140]}")
            try:
                found = await answer_from_web(
                    agent=RESEARCHER, query=f"{guide.document} {guide.country} fraud: {subject['about'][:200]}",
                    system=("You research document fraud for a fraud-prevention team. Report only what the pages "
                            "describe. Never give operational detail such as tools, templates or where to buy them."),
                    question=(f"Document: {guide.document}, {guide.country}.\nSubject: {subject['about']}\n"
                              f"We already know: {[k['title'] for k in known][:80]}\n\n"
                              "Which forgery, impersonation or claim-fraud tactics about this subject do the pages report "
                              "that aren't in that list? Prefer recent reports. Put a short title as the first line. "
                              "Describe at most two, each in a short paragraph: its name, how it works step by step, and "
                              "what would show on the document."),
                )
                structured = None
                if found.text.strip():
                    guard.call(RESEARCHER)
                    structured = await generate_json(
                        agent=RESEARCHER, schema=NewTactics,
                        system="You turn a research note into structured data. Use only what the note says.",
                        prompt=(f"Note:\n{found.text}\n\nWarning sign ids: {[s.id for s in guide.signs]}\n"
                                f"Safeguards: {list(SAFEGUARDS)}"),
                    )
            except _RUN_LEVEL:
                raise
            except Exception as e:  # this subject isn't researched now; it comes round again
                log.exception("fraud research run %s: a subject failed", run["id"])
                failed.append(_reason(e))
                guard.say("skip", f"Couldn't research this subject: {_reason(e)}. It comes round again.")
                continue
            researched += 1
            new_here = False
            for tactic in (structured.tactics if structured else []):
                if counts["found"] >= cap:
                    break
                if already_known({"title": tactic.name, "text": tactic.claim}, known):
                    counts["already_known"] = counts.get("already_known", 0) + 1
                    guard.say("skip", f"Already known, not added again: “{tactic.name}”")
                    continue
                counts["found"] += 1
                known.append({"title": tactic.name, "text": tactic.claim})  # once per run, whatever subject brings it up
                guard.call(RESEARCHER)
                guard.say("check", f"Checking “{tactic.name}” against cited sources", title=tactic.name)
                try:
                    evidence = await check_claim(tactic.claim)
                except _RUN_LEVEL:
                    raise
                except Exception as e:  # this tactic isn't kept; the run goes on to the next
                    log.exception("fraud research run %s: checking a tactic failed", run["id"])
                    counts["not_checked"] = counts.get("not_checked", 0) + 1
                    guard.progress(counts)
                    guard.say("skip", f"Couldn't check “{tactic.name}”: {_reason(e)}. It can come up again in a later "
                                      "run.", title=tactic.name)
                    failed.append(_reason(e))
                    checks_failed += 1
                    continue
                checked += 1
                if not evidence.confirmed:
                    counts["unconfirmed"] += 1
                    guard.say("skip", f"No source confirms “{tactic.name}”; not kept", title=tactic.name)
                    continue
                if tactic.sign and not _safe_sign(tactic.sign):
                    tactic.sign = None  # the tactic stays; its warning sign read like instructions
                    counts["signs_dropped"] = counts.get("signs_dropped", 0) + 1
                counts["confirmed"] += 1
                new_here = True
                draft = {"id": new_id("drf"), "at": time.time(), "run_id": run["id"], "guide_key": guide.key,
                         "kind": "tactic", "tactic": tactic.name,
                         "steps": [{"step": s.text, "caught_by": s.caught_by} for s in tactic.steps],
                         "sign": tactic.sign.model_dump() if tactic.sign else None, "claim": tactic.claim,
                         "label": "derived", "sources": evidence.sources, "source_note": evidence.summary,
                         "subject": subject["id"]}
                sites = independent_sources(evidence.sources)
                if can_add(guide.key, evidence):
                    # The tactic is added on its own; its warning sign, written from one page's text,
                    # would go into every certificate check, so it always waits for a reviewer.
                    added = _add_tactic({**draft, "sign": None}, trust="machine-confirmed", auto=True)
                    records.put("fraud_drafts", {**draft, "review": "added", "added_as": added})
                    if draft["sign"]:
                        records.put("fraud_drafts", {**draft, "id": new_id("drf"), "kind": "sign",
                                                   "tactic_id": added["tactic"], "review": "pending"})
                    _record_added(guide.key, "tactic", added["tactic"], tactic.name, [added["tactic"]], sites)
                    counts["added"] += 1
                    guard.say("save", f"Added to the guide: “{tactic.name}” ({sites} independent sources)"
                              + ("; its warning sign waits for a reviewer" if draft["sign"] else ""), title=tactic.name)
                else:
                    records.put("fraud_drafts", {**draft, "review": "pending"})
                    counts["drafts"] += 1
                    guard.say("save", f"New tactic waiting for a reviewer: “{tactic.name}” (fewer than "
                                      f"{AUTO_MIN_SOURCES} independent sources)", title=tactic.name)
                guard.progress(counts)
            subject_done(guide.key, subject, new_here)
        # Failed only if no subject could be researched, or checks were tried and none worked.
        if failed and (not researched or (checks_failed and not checked)):
            return _finish(run, counts, f"Nothing could be checked: {failed[-1]}")
        return _finish(run, counts)
    except Stopped as e:
        return _finish(run, counts, stopped=str(e))
    except Exception as e:
        log.exception("fraud research run %s failed", run["id"])
        return _finish(run, counts, _reason(e))
    finally:
        if store.get("fraud_guides", run["guide_key"]):
            store.update("fraud_guides", run["guide_key"], {"status": "current"})


# ---- Adding to a guide: by a reviewer's decision, or on its own ------------------------------


def _sources_of(items: list[dict]) -> list[fraud_guides.Source]:
    return [fraud_guides.Source(id=_source_id(s["url"]), title=s["title"] or s["url"], url=s["url"]) for s in items]


def _add_tactic(draft: dict, *, trust: str, auto: bool = False) -> dict:
    """A researched tactic, and its warning sign, as a new guide version. Returns the ids added."""
    sources = _sources_of(draft["sources"])
    ids = [s.id for s in sources]
    # Unique by construction (the draft's id), so a new tactic is never skipped as a duplicate.
    slug = (_slug(draft["tactic"]) or "tactic") + "_" + draft["id"][-6:]
    tactic = fraud_guides.Tactic(
        id=slug, name=draft["tactic"], label="derived", trust=trust, sources=ids, auto=auto,
        steps=[fraud_guides.Step(text=s["step"], caught_by=s["caught_by"]) for s in draft["steps"]])
    signs = []
    if draft.get("sign") and _safe_sign(DocumentSign(**draft["sign"])):
        signs.append(fraud_guides.Sign(id=f"{slug}_sign", look_for=draft["sign"]["look_for"], genuine=draft["sign"]["genuine"],
                                       label="derived", trust=trust, sources=ids, auto=auto))
    fraud_guides.add_items(draft["guide_key"], tactics=[tactic], signs=signs, sources=sources)
    return {"tactic": slug, "signs": [s.id for s in signs]}


def approve_draft(draft: dict) -> Guide:
    """A reviewer approved a researched tactic: add it, and its warning sign, as a new guide version.
    Approved items count as human-reviewed."""
    if draft.get("kind") == "sign":  # the warning sign of a tactic the pipeline added on its own
        sources = _sources_of(draft["sources"])
        if _safe_sign(DocumentSign(**draft["sign"])):
            fraud_guides.add_items(draft["guide_key"], sources=sources, signs=[fraud_guides.Sign(
                id=f"{draft['tactic_id']}_sign", look_for=draft["sign"]["look_for"], genuine=draft["sign"]["genuine"],
                label="derived", trust="human-reviewed", sources=[x.id for x in sources])])
        return fraud_guides.current(draft["guide_key"])
    _add_tactic(draft, trust="human-reviewed")
    return fraud_guides.current(draft["guide_key"])


def approve_scenario(scenario: dict, *, auto: bool = False) -> Guide:
    """A confirmed scenario becomes `validated`, by a reviewer's decision or, with 2 independent
    sources, on its own (auto). If it would leave a mark on the certificate, that mark becomes a
    projection sign for the check (at most a third of the signs; see fraud_guides.signs_for_check)."""
    sources = _sources_of(scenario.get("sources", []))
    ids = [s.id for s in sources]
    trust = "machine-confirmed" if auto else "human-reviewed"
    signs = []
    if scenario.get("document_sign") and _safe_sign(DocumentSign(**scenario["document_sign"])):
        signs.append(fraud_guides.Sign(id=scenario["id"], look_for=scenario["document_sign"]["look_for"],
                                       genuine=scenario["document_sign"]["genuine"], label="validated",
                                       trust=trust, sources=ids, projection=True, auto=auto))
    entry = {"id": scenario["id"], "title": scenario["scenario"], "safeguard": scenario["safeguard"],
             "caught_by": scenario["caught_by"], "coverage": scenario["coverage"], "label": "validated",
             "sources": ids, "auto": auto}
    return fraud_guides.add_items(scenario["guide_key"], signs=signs, scenarios=[entry], sources=sources)
