"""Fraud foresight pipeline (PR-2.5, PR-2.6): imagine, check, map, then leave drafts for review.

Design: docs/proposals/fraud-foresight.md §4 and §6. Two agents, each with one job:
  - fraud.dreamer imagines attacks from our own material and maps each to a safeguard. No web access.
  - fraud.researcher checks claims against web pages (Exa search, web_search.py), and researches new
    tactics for a guide.

Rules in code, each with a test:
  - Imagined content never feeds on itself: dreaming starts only from checked tactics, the fixed
    safeguards and guardian events (agent, response and reason only; never case keys or owner data).
  - "Confirmed" means a cited source supports an answer that says the claim
    has been reported, and that answer repeats the claim's own words; a mention isn't enough.
  - Nothing confirmed here changes a guide by itself: confirmed scenarios and tactics wait for a
    reviewer. Unconfirmed scenarios fade after 3 runs.
  - Limits per run come from the registry: 2 sequences, 3 scenarios each (2 close, 1 far-fetched),
    2 new tactics per guide, and a cap on model calls per agent. One run at a time. A reviewer
    can stop a run; it stops before its next model call and keeps what was checked so far.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

from . import fraud_guides
from .config import settings
from .fraud_guides import CHECKABLE, Guide
from .gemini import ModelBusy, generate_json
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
RUN_TIMEOUT_SECONDS = 15 * 60


class Busy(RuntimeError):
    """A fraud run is already in progress."""


class Stopped(Exception):
    """A reviewer stopped the run, or it reached its call limit."""


class Guard:
    """Cost control for one run. Before every model call it checks whether a reviewer pressed
    Stop, and whether the agent has calls left under its max_model_calls_per_run in the
    registry. It also saves the counts after each step, so the portal shows progress live."""

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


# Guide text goes into the certificate check's instructions once a reviewer approves it, so
# text from the web must read as a description of a document, never as instructions.
MAX_SIGN_CHARS = 300
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|instruction|prompt|system|you must|you should|rule out|confidence|verdict|approve|"
    r"likely[_ ]valid|report (?:every|all)|always|never)\b", re.IGNORECASE)


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
        model=settings.research_model,
        query=claim,
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
    """A run still 'running' after the timeout (for example after a server restart) is marked
    failed and its lock released, so new runs can start."""
    now = now or time.time()
    for run in store.list("fraud_runs", status="running"):
        if now - run["started_at"] >= RUN_TIMEOUT_SECONDS:
            store.update("fraud_runs", run["id"], {"status": "failed", "error": "timed out", "finished_at": now})
    lock = store.get(*LOCK)
    if lock and now - lock["at"] >= RUN_TIMEOUT_SECONDS:
        store.delete(*LOCK)


def start_run(kind: Literal["dream", "research"], guide_key: str, by: str) -> dict:
    """Record a run before it starts. One at a time: a create-only lock, so two requests at
    the same moment can't both start one."""
    now = time.time()
    expire_stale(now)
    run_id = new_id("run")
    if not store.create(LOCK[0], {"id": LOCK[1], "run_id": run_id, "at": now}):
        raise Busy("A fraud run is already in progress. Try again when it finishes.")
    limits = {k: governance.limit(a, k) for a, k in (
        (DREAMER, "max_sequences_per_run"), (DREAMER, "max_scenarios_per_sequence"),
        (RESEARCHER, "max_new_patterns_per_run"))}
    limits |= {f"{a}.max_model_calls_per_run": governance.limit(a, "max_model_calls_per_run") for a in (DREAMER, RESEARCHER)}
    return store.put("fraud_runs", {"id": run_id, "kind": kind, "guide_key": guide_key, "by": by,
                                    "status": "running", "started_at": now, "limits": limits, "counts": {},
                                    "calls": {}, "stop_requested": False})


def request_stop(run_id: str) -> dict:
    """Ask a running run to stop. It finishes the scenario in hand, then stops."""
    run = store.get("fraud_runs", run_id)
    if not run or run["status"] != "running":
        raise KeyError(run_id)
    return store.update("fraud_runs", run_id, {"stop_requested": True})


# Our own errors carry a plain message a reviewer can act on; anything else shows only its type,
# so no page text or model output reaches the portal.
_PLAIN = (ModelBusy, NotSetUp, governance.Refused)


def _reason(e: Exception) -> str:
    return str(e) if isinstance(e, _PLAIN) else type(e).__name__


def _finish(run: dict, counts: dict, error: str | None = None, stopped: str | None = None) -> dict:
    status = "stopped" if stopped else "failed" if error else "done"
    lock = store.get(*LOCK)
    if lock and lock.get("run_id") == run["id"]:
        store.delete(*LOCK)
    return store.update("fraud_runs", run["id"], {
        "status": status, "finished_at": time.time(), "counts": counts, "error": error, "stopped_reason": stopped})


# ---- Dreaming (PR-2.6) -----------------------------------------------------------------


def _seeds(guide: Guide) -> list:
    """Rule 1: only checked tactics seed new scenarios, least-dreamed first."""
    tactics = [t for t in guide.tactics if t.label in CHECKABLE]
    dreamed = store.list("fraud_scenarios", guide_key=guide.key)
    tactics.sort(key=lambda t: sum(s.get("sequence_id") == t.id for s in dreamed))
    return tactics[: int(governance.limit(DREAMER, "max_sequences_per_run", 2))]


def _recent_events(n: int = 10) -> list[dict]:
    """What the guardian has seen lately: agent, response and reason only. Never case keys."""
    events = sorted(store.list("guardian_events"), key=lambda e: -e.get("at", 0))[:n]
    return [{"agent": e.get("agent"), "response": e.get("response"), "reason": e.get("reason")} for e in events]


def _ruled_out(guide_key: str, n: int = 20) -> list[str]:
    """Rule 6: rejected scenarios go back to the dreamer as 'already ruled out'."""
    rejected = store.list("fraud_scenarios", guide_key=guide_key, label="invalidated")
    return [s["scenario"] for s in sorted(rejected, key=lambda s: -s["at"])[:n]]


def _pick(scenarios: list[ImaginedScenario], existing: list[str]) -> list[ImaginedScenario]:
    """At most 2 close variants and 1 far-fetched one, aimed at a real safeguard, and new."""
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
            if any(_similar(s.title, t) for t in [*existing, *(p.title for p in picked)]):
                continue
            picked.append(s)
    return picked


async def _imagine(guide: Guide, tactic, events: list[dict], ruled_out: list[str]) -> list[ImaginedScenario]:
    sequence = "\n".join(f"{i}. {s.text} (caught by: {s.caught_by})" for i, s in enumerate(tactic.steps, 1))
    safeguards = "\n".join(f"- {name}: {how}" for name, how in SAFEGUARDS.items())
    prompt = (
        f"Document: {guide.title}.\nKnown fraud sequence: {tactic.name}\n{sequence}\n\n"
        f"Our safeguards:\n{safeguards}\n\n"
        f"Recent guardian events: {events or 'none'}\n"
        f"Already ruled out (don't repeat): {ruled_out or 'none'}\n\n"
        "Imagine new variants of this sequence designed to get past the safeguards and avoid the known warning "
        "signs: two close variants that change one or two steps, and one deliberately far-fetched variant."
    )
    imagined = await generate_json(
        agent=DREAMER, model=settings.research_model, schema=Imagined, prompt=prompt,
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
        agent=DREAMER, model=settings.research_model, schema=Coverage,
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
    for s in store.list("fraud_scenarios", guide_key=guide_key, review="unconfirmed"):
        seen = s.get("runs_seen", 0) + 1
        fields = {"runs_seen": seen}
        if seen >= FADE_AFTER_RUNS:
            fields["review"] = "faded"
            faded += 1
        store.update("fraud_scenarios", s["id"], fields)
    return faded


async def dream(run: dict) -> dict:
    """One dreaming run for a guide. Never raises: a failure is recorded on the run."""
    counts = {"sequences": 0, "imagined": 0, "kept": 0, "confirmed": 0, "unconfirmed": 0, "faded": 0}
    try:
        guide = fraud_guides.current(run["guide_key"])
        if guide is None:
            return _finish(run, counts, "guide not found")
        counts["faded"] = _fade(guide.key)
        events, ruled_out = _recent_events(), _ruled_out(guide.key)
        existing = [s["scenario"] for s in store.list("fraud_scenarios", guide_key=guide.key)]
        guard = Guard(run)
        for tactic in _seeds(guide):
            counts["sequences"] += 1
            guard.call(DREAMER)
            guard.say("imagine", f"Imagining new ways around the safeguards, starting from “{tactic.name}”")
            imagined = await _imagine(guide, tactic, events, ruled_out)
            counts["imagined"] += len(imagined)
            guard.progress(counts)
            picked = _pick(imagined, existing)
            guard.say("imagine", f"Imagined {len(imagined)}; keeping {len(picked)} new ones "
                                 "(2 close variants, 1 far-fetched)")
            for scenario in picked:
                guard.call(RESEARCHER)
                guard.say("check", f"Searching the web for evidence: “{scenario.claim}”", title=scenario.title)
                evidence = await check_claim(scenario.claim)
                guard.say("check", f"Confirmed by {len(evidence.sources)} cited source(s)" if evidence.confirmed
                          else "No source confirms it yet", title=scenario.title, confirmed=evidence.confirmed)
                guard.call(DREAMER, finishing=True)
                guard.say("map", f"Which safeguard would catch “{scenario.title}”?", title=scenario.title)
                caught_by, coverage, reason = await _map(scenario)
                store.put("fraud_scenarios", {
                    "id": new_id("scn"), "at": time.time(), "run_id": run["id"], "guide_key": guide.key,
                    "sequence_id": tactic.id, "scenario": scenario.title, "steps": scenario.steps,
                    "variant": scenario.variant, "claim": scenario.claim, "safeguard": scenario.targets,
                    "caught_by": caught_by, "coverage": coverage, "coverage_reason": reason,
                    "document_sign": scenario.document_sign.model_dump() if scenario.document_sign else None,
                    "label": "simulated", "status": "verified" if evidence.confirmed else "dreamt",
                    "sources": evidence.sources, "evidence": len(evidence.sources), "source_note": evidence.summary,
                    "review": "pending" if evidence.confirmed else "unconfirmed", "runs_seen": 0,
                })
                existing.append(scenario.title)
                counts["kept"] += 1
                counts["confirmed" if evidence.confirmed else "unconfirmed"] += 1
                guard.progress(counts)
                guard.say("save", (f"Caught by {caught_by}" if caught_by else "Gap: no safeguard catches it")
                          + (" (only sometimes)" if coverage == "partly" else "")
                          + ("; waiting for a reviewer" if evidence.confirmed else "; added as unconfirmed"),
                          title=scenario.title, coverage=coverage)
        return _finish(run, counts)
    except Stopped as e:  # what was checked so far is kept
        return _finish(run, counts, stopped=str(e))
    except Exception as e:  # recorded on the run, shown in the portal; never the content
        log.exception("fraud dreaming run %s failed", run["id"])
        return _finish(run, counts, _reason(e))


# ---- Research (PR-2.5) -----------------------------------------------------------------


def _source_id(url: str) -> str:
    return "src_" + hashlib.sha256(url.encode()).hexdigest()[:10]


async def research(run: dict) -> dict:
    """Look for new tactics for one guide. Confirmed ones become drafts for a reviewer; if
    nothing is confirmed, nothing changes."""
    counts = {"found": 0, "confirmed": 0, "unconfirmed": 0}
    try:
        guide = fraud_guides.current(run["guide_key"])
        if guide is None:
            return _finish(run, counts, "guide not found")
        known = [t.name for t in guide.tactics] + [s.look_for for s in guide.signs]
        guard = Guard(run)
        guard.call(RESEARCHER)
        guard.say("research", f"Searching the web for new tactics: {guide.document}, {guide.country}")
        found = await answer_from_web(
            agent=RESEARCHER, model=settings.research_model,
            query=f"{guide.document} {guide.country} forgery fraud tactics",
            system=("You research document fraud for a fraud-prevention team. Report only what the pages "
                    "describe. Never give operational detail such as tools, templates or where to buy them."),
            question=(f"Document: {guide.document}, {guide.country}.\nWe already know: {known}\n\n"
                      "Which forgery or impersonation tactics involving this document do the pages report that "
                      "aren't in that list? Put a short title as the first line. Describe at most two, each in a "
                      "short paragraph: its name, how it works step by step, and what would show on the document."),
        )
        if not found.text.strip():
            return _finish(run, counts)
        guard.call(RESEARCHER)
        structured = await generate_json(
            agent=RESEARCHER, model=settings.research_model, schema=NewTactics,
            system="You turn a research note into structured data. Use only what the note says.",
            prompt=(f"Note:\n{found.text}\n\nWarning sign ids: {[s.id for s in guide.signs]}\n"
                    f"Safeguards: {list(SAFEGUARDS)}"),
        )
        cap = int(governance.limit(RESEARCHER, "max_new_patterns_per_run", 2))
        for tactic in structured.tactics[:cap]:
            if any(_similar(tactic.name, k) for k in known):
                guard.say("skip", f"Already in the guide: “{tactic.name}”")
                continue
            counts["found"] += 1
            guard.call(RESEARCHER)
            guard.say("check", f"Checking “{tactic.name}” against cited sources", title=tactic.name)
            evidence = await check_claim(tactic.claim)
            if not evidence.confirmed:
                counts["unconfirmed"] += 1
                guard.say("skip", f"No source confirms “{tactic.name}”; not kept", title=tactic.name)
                continue
            if tactic.sign and not _safe_sign(tactic.sign):
                tactic.sign = None  # the tactic stays; its warning sign read like instructions
                counts["signs_dropped"] = counts.get("signs_dropped", 0) + 1
            guard.say("save", f"New tactic waiting for a reviewer: “{tactic.name}”", title=tactic.name)
            counts["confirmed"] += 1
            store.put("fraud_drafts", {
                "id": new_id("drf"), "at": time.time(), "run_id": run["id"], "guide_key": guide.key,
                "kind": "tactic", "tactic": tactic.name,
                "steps": [{"step": s.text, "caught_by": s.caught_by} for s in tactic.steps],
                "sign": tactic.sign.model_dump() if tactic.sign else None, "claim": tactic.claim,
                "label": "derived", "sources": evidence.sources, "source_note": evidence.summary, "review": "pending",
            })
            guard.progress(counts)
        return _finish(run, counts)
    except Stopped as e:
        return _finish(run, counts, stopped=str(e))
    except Exception as e:
        log.exception("fraud research run %s failed", run["id"])
        return _finish(run, counts, _reason(e))
    finally:
        if store.get("fraud_guides", run["guide_key"]):
            store.update("fraud_guides", run["guide_key"], {"status": "current"})


# ---- What a reviewer's decision does ---------------------------------------------------


def approve_draft(draft: dict) -> Guide:
    """A reviewer approved a researched tactic: add it, and its warning sign, as a new guide version.
    Approved items count as human-reviewed."""
    sources = [fraud_guides.Source(id=_source_id(s["url"]), title=s["title"] or s["url"], url=s["url"])
               for s in draft["sources"]]
    ids = [s.id for s in sources]
    # Unique by construction (the draft's id), so an approved tactic is never skipped as a duplicate.
    slug = (re.sub(r"[^a-z0-9]+", "_", draft["tactic"].lower()).strip("_")[:40] or "tactic") + "_" + draft["id"][-6:]
    tactic = fraud_guides.Tactic(
        id=slug, name=draft["tactic"], label="derived", trust="human-reviewed", sources=ids,
        steps=[fraud_guides.Step(text=s["step"], caught_by=s["caught_by"]) for s in draft["steps"]])
    signs = []
    if draft.get("sign") and _safe_sign(DocumentSign(**draft["sign"])):
        signs.append(fraud_guides.Sign(id=f"{slug}_sign", look_for=draft["sign"]["look_for"],
                                       genuine=draft["sign"]["genuine"], label="derived", trust="human-reviewed",
                                       sources=ids))
    return fraud_guides.add_items(draft["guide_key"], tactics=[tactic], signs=signs, sources=sources)


def approve_scenario(scenario: dict) -> Guide:
    """A reviewer approved a confirmed scenario: it becomes `validated`. If it would leave a mark
    on the certificate, that mark becomes a projection sign for the check (at most a third of
    the signs; see fraud_guides.signs_for_check)."""
    sources = [fraud_guides.Source(id=_source_id(s["url"]), title=s["title"] or s["url"], url=s["url"])
               for s in scenario.get("sources", [])]
    ids = [s.id for s in sources]
    signs = []
    if scenario.get("document_sign") and _safe_sign(DocumentSign(**scenario["document_sign"])):
        signs.append(fraud_guides.Sign(id=scenario["id"], look_for=scenario["document_sign"]["look_for"],
                                       genuine=scenario["document_sign"]["genuine"], label="validated",
                                       trust="human-reviewed", sources=ids, projection=True))
    entry = {"id": scenario["id"], "title": scenario["scenario"], "safeguard": scenario["safeguard"],
             "caught_by": scenario["caught_by"], "coverage": scenario["coverage"], "label": "validated",
             "sources": ids}
    return fraud_guides.add_items(scenario["guide_key"], signs=signs, scenarios=[entry], sources=sources)
