"""Fraud reference guides (PR-2.3): what a genuine document looks like, and how forgeries are made.

One guide per kind of document and country. Every item carries a knowledge label saying how
we know it (docs/proposals/fraud-foresight.md §3). Guides are saved in the platform's OKF bundle
(fraud_bundle.py), with every change to an item kept as an event; a copy built into the code is
used when the bundle has none or is unavailable, so a check always has its guide.

Only checked knowledge reaches the certificate check (`signs_for_check`): imagined items
never do, and projections (validated scenarios) make up at most a third of the signs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator

from . import fraud_bundle
from .step_types import CHANNELS, FLAGS, STEP_TYPES
from .store import store

log = logging.getLogger(__name__)

Label = Literal["observed", "derived", "simulated", "validated", "invalidated"]
Trust = Literal["human-reviewed", "machine-confirmed", "unverified"]

# Labels the certificate check may use. `simulated` and `invalidated` never reach it.
CHECKABLE: frozenset[str] = frozenset({"observed", "derived", "validated"})


class Source(BaseModel):
    id: str
    title: str
    url: str


class Sign(BaseModel):
    """A warning sign the check looks for, sign by sign."""
    id: str
    look_for: str            # what a forgery shows
    genuine: str             # what a genuine document shows instead
    label: Label
    trust: Trust
    sources: list[str] = []  # Source ids
    projection: bool = False  # True for a validated scenario: a projection, not a known tactic
    status: Literal["active", "retired"] = "active"
    auto: bool = False  # added by the fraud pipeline on its own (2 independent sources); a person can retire it
    # The sign applies only to documents registered on or after this date (YYYY-MM-DD). Code rules it
    # out for an older document, whatever the model says (loops._named_signs): the model was found to
    # ignore a date written only in look_for (PR-15.5).
    applies_from: str | None = None
    # A check code also runs for this sign, from what the model read; it can only add the sign, never
    # rule it out (loops.CODE_CHECKS). "registration_year": the year in the registration number matches
    # the year of registration or of the death (PR-15.5 found the model missed one that didn't).
    code_check: Literal["registration_year"] | None = None

    @field_validator("applies_from")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        """Checked when a guide loads, so a bad date can never break a check later."""
        if value is not None:
            date.fromisoformat(value)
        return value


class Step(BaseModel):
    text: str
    caught_by: str           # a Sign id, or a safeguard such as "owner alert"


class Exploits(BaseModel):
    """Which next steps a tactic exploits (PR-13.3): step types, channels and flags from the closed
    lists in step_guard.py. A step matches when every list given here has its value in it."""
    types: list[str] = []
    channels: list[str] = []
    flags: list[str] = []

    @field_validator("types", "channels", "flags")
    @classmethod
    def _known(cls, values: list[str], info) -> list[str]:
        """A typo would make an approved tactic block nothing, so an unknown tag is refused."""
        allowed = {"types": STEP_TYPES, "channels": CHANNELS, "flags": FLAGS}[info.field_name]
        if unknown := [v for v in values if v not in allowed]:
            raise ValueError(f"unknown {info.field_name}: {', '.join(unknown)}")
        return values

    def empty(self) -> bool:
        return not (self.types or self.channels or self.flags)

    def matches(self, step_type: str, channel: str, flags: set[str]) -> bool:
        return (not self.empty() and (not self.types or step_type in self.types)
                and (not self.channels or channel in self.channels)
                and (not self.flags or bool(flags & set(self.flags))))


class Tactic(BaseModel):
    """A known way of forging or misusing the document, step by step."""
    id: str
    name: str
    steps: list[Step]
    label: Label
    trust: Trust
    sources: list[str] = []
    exploits: Exploits | None = None  # set by a reviewer (PR-13.6); Saathi never pursues a matching step
    status: Literal["active", "retired"] = "active"  # a retired tactic seeds no dreaming
    auto: bool = False  # added by the fraud pipeline on its own (2 independent sources); a person can retire it


class Guide(BaseModel):
    key: str                 # e.g. "IN.death_certificate"
    title: str
    country: str
    document: str
    version: int = 1
    updated_at: float = 0
    signs: list[Sign]
    tactics: list[Tactic] = []
    scenarios: list[dict] = []  # filled by the fraud pipeline (PR-2.5, PR-2.6)
    sources: list[Source]


def signs_for_check(guide: Guide) -> list[Sign]:
    """The signs the certificate check receives: active, checked knowledge only, and
    projections at most a third of the total (rules 2 and 4 in fraud-foresight.md)."""
    usable = [s for s in guide.signs if s.status == "active" and s.label in CHECKABLE]
    known = [s for s in usable if not s.projection]
    projections = [s for s in usable if s.projection][: len(known) // 2]
    return known + projections


# ---- Storage: the platform's OKF bundle (fraud_bundle.py), the built-in copy as a fallback ----
# Guides saved before PR-2.14.1 sit in Firestore (`fraud_guide_versions`); each is copied into the
# bundle once, version by version, the first time it is read.

_migrated: set[str] = set()  # guide keys this process has already looked for in the old collection


def _migrate(key: str) -> None:
    if key in _migrated:
        return
    versions = sorted(store.list("fraud_guide_versions", key=key), key=lambda v: v["version"])
    built_on = None
    for v in versions:
        try:
            fraud_bundle.save_guide({k: val for k, val in v.items() if k != "id"}, built_on=built_on,
                                    at=v.get("updated_at") or None, action="Copied from Firestore")
        except fraud_bundle.Stale:  # another instance is copying it, or has copied it
            break
        built_on = v["version"]
    _migrated.add(key)


def _latest_stored(key: str, *, fresh: bool = False) -> Guide | None:
    data = fraud_bundle.load_guide(key, fresh=fresh)
    if data is None and key not in _migrated:
        _migrate(key)
        data = fraud_bundle.load_guide(key, fresh=True)
    return _with_built_in_dates(Guide.model_validate(data)) if data else None


def current_all() -> list[Guide]:
    """Every current guide, read strictly: if the store can't be read this raises instead of
    falling back to the built-in copy, so the next-step check (PR-13.3) fails closed rather than
    forgetting the tactics reviewers approved."""
    if "*" not in _migrated:
        for k in {v["key"] for v in store.list("fraud_guide_versions")}:
            _migrate(k)
        _migrated.add("*")
    saved = set(fraud_bundle.guide_keys())
    return [g for k in sorted(set(BUILT_IN) | saved) if (g := _latest_stored(k) or BUILT_IN.get(k))]


def current(key: str) -> Guide | None:
    """The latest saved version, or the built-in copy if none is saved or the store fails."""
    try:
        stored = _latest_stored(key)
    except Exception:  # storage unavailable: the check still runs on the built-in copy
        log.warning("fraud guide store unavailable; using the built-in copy of %s", key)
        stored = None
    return stored or BUILT_IN.get(key)


def clear_saved() -> None:
    """Forget every saved guide (tests): the bundle, and which old Firestore versions were copied."""
    fraud_bundle.clear()
    _migrated.clear()


def history(key: str, item_id: str | None = None) -> list[dict]:
    """The changes to a guide, or to one of its items (its bundle id, e.g. "IN.death_certificate/signs/seal"), oldest first."""
    return fraud_bundle.history(key, item_id)


CODE_FIELDS = ("applies_from", "code_check")  # sign fields that code enforces, added after guides were saved


def _with_built_in_dates(guide: Guide) -> Guide:
    """A version saved before signs had `applies_from` or `code_check` gets them from the built-in sign
    with the same id. Only when a field is absent: a version that clears it on purpose (None) keeps it."""
    base = {s.id: s for s in (BUILT_IN[guide.key].signs if guide.key in BUILT_IN else [])}

    def filled(s: Sign) -> Sign:
        add = {f: getattr(base[s.id], f) for f in CODE_FIELDS
               if s.id in base and f not in s.model_fields_set and getattr(base[s.id], f)}
        return s.model_copy(update=add) if add else s

    return guide.model_copy(update={"signs": [filled(s) for s in guide.signs]})


def _save_next(key: str, build: Callable[[Guide | None], Guide]) -> Guide:
    """Write the next version, built from the latest one. A version is written only on top of the one
    it was built from; if someone else saved first, the next version is built again from theirs, so no
    change is lost."""
    for _ in range(5):
        latest = _latest_stored(key, fresh=True)
        guide = build(latest)
        saved = guide.model_copy(update={"version": (latest.version + 1) if latest else 1, "updated_at": time.time()})
        try:
            fraud_bundle.save_guide(saved.model_dump(), built_on=latest.version if latest else None, at=saved.updated_at)
        except fraud_bundle.Stale:
            continue
        store.put("fraud_guides", summary(saved))  # the portal's row: status and summary, not the knowledge
        return saved
    raise RuntimeError(f"Couldn't save a new version of {key}")


def save_version(guide: Guide) -> Guide:
    """Save the guide as a new version, keeping every earlier one, and update the portal's row."""
    return _save_next(guide.key, lambda _latest: guide)


def ensure_saved(key: str) -> bool:
    """Before a guide's first run: a built-in guide is saved as version 1. False if there's no such guide."""
    if _latest_stored(key, fresh=True):
        return True
    if key not in BUILT_IN:
        return False
    save_version(BUILT_IN[key])
    return True


def add_items(key: str, *, signs: list[Sign] = (), tactics: list[Tactic] = (), scenarios: list[dict] = (),
              sources: list[Source] = ()) -> Guide:
    """A new version of the guide with these items added. Nothing is removed here: retiring a
    warning sign is a separate decision for a person. Items already in the guide aren't added twice."""
    if current(key) is None:
        raise KeyError(key)

    def build(latest: Guide | None) -> Guide:
        guide = latest or BUILT_IN[key]
        known = {s.id for s in guide.signs} | {t.id for t in guide.tactics}
        listed = {s.get("id") for s in guide.scenarios}
        cited = {s.id for s in guide.sources}
        return guide.model_copy(update={
            "signs": [*guide.signs, *(s for s in signs if s.id not in known)],
            "tactics": [*guide.tactics, *(t for t in tactics if t.id not in known)],
            "scenarios": [*guide.scenarios, *(s for s in scenarios if s.get("id") not in listed)],
            "sources": [*guide.sources, *{s.id: s for s in sources if s.id not in cited}.values()],
        })

    return _save_next(key, build)


def retire_items(key: str, ids: set[str]) -> Guide:
    """A new version with these signs, tactics or scenarios retired: a person's decision (for
    example, removing something the pipeline added on its own). Nothing is deleted: earlier versions
    keep it, and a retired item reaches no check and seeds no dreaming."""
    def build(latest: Guide | None) -> Guide:
        guide = latest or BUILT_IN[key]
        return guide.model_copy(update={
            "signs": [s.model_copy(update={"status": "retired"}) if s.id in ids else s for s in guide.signs],
            "tactics": [t.model_copy(update={"status": "retired"}) if t.id in ids else t for t in guide.tactics],
            "scenarios": [{**s, "status": "retired"} if s.get("id") in ids else s for s in guide.scenarios],
        })

    return _save_next(key, build)


def summary(guide: Guide) -> dict:
    """The row the platform admin portal shows. Trust is the weakest trust among the signs the
    check uses. A built-in guide not yet saved has no research date."""
    order = ["unverified", "machine-confirmed", "human-reviewed"]
    used = signs_for_check(guide)
    weakest = min((s.trust for s in used), key=order.index, default="unverified")
    return {"id": guide.key, "title": guide.title, "version": guide.version, "trust_tier": weakest,
            "sources": len(guide.sources), "signals": len(used),
            "researched_at": guide.updated_at or None, "status": "current"}


def summaries() -> list[dict]:
    """Saved guides, plus built-in guides that haven't been saved yet."""
    rows = {g["id"]: g for g in store.list("fraud_guides")}
    for key, guide in BUILT_IN.items():
        rows.setdefault(key, summary(guide))
    return sorted(rows.values(), key=lambda g: g["title"])


COUNTRY_CODES = {"india": "IN", "singapore": "SG"}


def key_for(expected: str, country: str = "India") -> str | None:
    """Which guide a check uses. Owners store the country's name ("India") or its code ("IN").
    Documents without a guide yet are checked without one."""
    code = COUNTRY_CODES.get(country.strip().lower(), country.strip().upper())
    if "death certificate" in expected.lower():
        return f"{code}.death_certificate"
    return None


# ---- Built-in guides ----------------------------------------------------------------
#
# Written from the cited sources and labelled `derived`, machine-confirmed: a reviewer
# confirms them in the portal. More countries and documents follow (PR-2.3.1).

_IN_SOURCES = [
    Source(id="crs", title="Civil Registration System, Office of the Registrar General of India",
           url="https://dc.crsorgi.gov.in/crs/about"),
    Source(id="rbd_act", title="Registration of Births and Deaths Act, 1969 (India Code)",
           url="https://www.indiacode.nic.in/handle/123456789/1682?locale=en"),
    Source(id="rbd_wiki", title="Registration of Births and Deaths Act, 1969 (Wikipedia, incl. the 2023 amendment)",
           url="https://en.wikipedia.org/wiki/Registration_of_Births_and_Deaths_Act,_1969"),
    Source(id="model_rules", title="Model Registration of Births and Deaths Rules, 1999",
           url="https://dc.crsorgi.gov.in/assets/download/Births_and_Deaths_Rules_1999.pdf"),
    Source(id="gurugram", title="Gurugram: registrars' IDs hacked, fake certificates issued (The Tribune)",
           url="https://www.tribuneindia.com/news/haryana/gurugram-birth-death-registrars-id-hacked-14-fake-certificates-issued-273528"),
    Source(id="bhopal", title="Fake birth and death certificates for ₹20 (Free Press Journal)",
           url="https://www.freepressjournal.in/bhopal/fake-birth-death-certificates-for-just-20-suspected-network-surfaces-in-bhopal"),
    Source(id="faked_twice", title="How a woman faked death twice to claim an insurance payout (Business Standard)",
           url="https://www.business-standard.com/finance/personal-finance/insurance-fraud-decoded-how-a-woman-faked-death-twice-to-claim-rs-1-1-cr-124070500805_1.html"),
]


def _sign(id: str, look_for: str, genuine: str, *sources: str, applies_from: str | None = None,
          code_check: str | None = None) -> Sign:
    return Sign(id=id, look_for=look_for, genuine=genuine, label="derived", trust="machine-confirmed",
                sources=list(sources), applies_from=applies_from, code_check=code_check)


_IN_DEATH = Guide(
    key="IN.death_certificate",
    title="India · certificate of passing (Civil Registration System)",
    country="IN",
    document="death certificate",
    signs=[
        _sign("qr_missing",
              "No QR code or digital signature on a certificate registered from 1 October 2023 onwards",
              "Certificates registered on the Civil Registration System since 1 October 2023 are digitally "
              "signed and carry a QR code that can be verified on the CRS portal",
              "crs", "rbd_wiki", applies_from="2023-10-01"),
        _sign("qr_mismatch",
              "The QR code's details (name, registration number, date) differ from the printed ones, or the "
              "same QR code appears on another certificate",
              "The QR code resolves to the same person, registration number and dates as printed",
              "gurugram"),
        _sign("qr_wrong_site",
              "The QR code or a printed link leads to a site other than the CRS portal or a state government "
              "portal, for example a look-alike address",
              "Verification happens on crsorgi.gov.in or the state's own government portal",
              "crs"),
        _sign("signature_replica",
              "The registrar's signature or seal is a pasted image: blurred edges, identical to other "
              "certificates, or not tied to a digital signature",
              "The signature is a verifiable digital signature, or a clear stamped seal of the issuing office",
              "bhopal", "gurugram"),
        _sign("dates_impossible",
              "The date of registration is before the date of passing, or the date of issue is before "
              "registration; or a registration more than a year late has no order allowing late registration",
              "Dates follow in order: passing, then registration, then issue. Late registrations cite the "
              "order that allowed them",
              "rbd_act", "model_rules"),
        _sign("registration_details",
              "The registration number is missing, or its year doesn't match the date of registration",
              "A registration number and date of registration as set out in the state's rules",
              "model_rules", code_check="registration_year"),
        _sign("wrong_office",
              "The issuing registrar's office is not in the area where the passing took place",
              "The registrar of the area where the passing took place registers it and issues the certificate",
              "rbd_act"),
        _sign("altered_text",
              "Fonts, spacing or alignment change around the name, address or dates",
              "Printed fields use one consistent font and layout",
              "gurugram"),
    ],
    tactics=[
        Tactic(id="copied_qr", name="A real QR code with changed details", label="derived", trust="machine-confirmed",
               sources=["gurugram"], steps=[
                   Step(text="Take the QR code from a genuine certificate issued earlier", caught_by="qr_mismatch"),
                   Step(text="Change the name and address on the printed certificate", caught_by="altered_text"),
                   Step(text="Present it to an institution that only glances at the QR code", caught_by="qr_mismatch"),
               ]),
        Tactic(id="registrar_login", name="A registrar's login misused", label="derived", trust="machine-confirmed",
               sources=["gurugram"], steps=[
                   Step(text="Gain access to a registrar's account on the portal", caught_by="wrong_office"),
                   Step(text="Issue a certificate that verifies as genuine for someone still alive",
                        caught_by="owner alert and cancel link"),
                   Step(text="Report the passing and file claims", caught_by="waiting period"),
               ]),
        Tactic(id="cheap_template", name="A cheap template with a replica signature", label="derived",
               trust="machine-confirmed", sources=["bhopal"], steps=[
                   Step(text="Buy a certificate made with software that copies the official format",
                        caught_by="qr_wrong_site"),
                   Step(text="The registrar's signature is a copied image", caught_by="signature_replica"),
               ]),
        Tactic(id="staged_passing", name="A staged passing for an insurance claim", label="derived",
               trust="machine-confirmed", sources=["faked_twice"], steps=[
                   Step(text="Obtain death documents for a person who is alive", caught_by="owner alert and cancel link"),
                   Step(text="Claim quickly, before anyone checks", caught_by="waiting period"),
               ]),
    ],
    sources=_IN_SOURCES,
)

BUILT_IN: dict[str, Guide] = {_IN_DEATH.key: _IN_DEATH}
