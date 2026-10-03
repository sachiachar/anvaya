"""Fraud reference guides (PR-2.3): what a genuine document looks like, and how forgeries are made.

One guide per kind of document and country. Every item carries a knowledge label saying how
we know it (docs/proposals/fraud-foresight.md §3). Each saved version is kept; a copy built
into the code is used when the store has none or is unavailable, so a check always has its
guide.

Only checked knowledge reaches the certificate check (`signs_for_check`): imagined items
never do, and projections (validated scenarios) make up at most a third of the signs.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from pydantic import BaseModel

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


class Step(BaseModel):
    text: str
    caught_by: str           # a Sign id, or a safeguard such as "owner alert"


class Tactic(BaseModel):
    """A known way of forging or misusing the document, step by step."""
    id: str
    name: str
    steps: list[Step]
    label: Label
    trust: Trust
    sources: list[str] = []


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


# ---- Storage: every version kept, the built-in copy as a fallback --------------------


def _latest_stored(key: str) -> Guide | None:
    versions = store.list("fraud_guide_versions", key=key)
    if not versions:
        return None
    latest = max(versions, key=lambda v: v["version"])
    return Guide.model_validate({k: v for k, v in latest.items() if k != "id"})


def current(key: str) -> Guide | None:
    """The latest saved version, or the built-in copy if none is saved or the store fails."""
    try:
        stored = _latest_stored(key)
    except Exception:  # Firestore unavailable: the check still runs on the built-in copy
        log.warning("fraud guide store unavailable; using the built-in copy of %s", key)
        stored = None
    return stored or BUILT_IN.get(key)


def save_version(guide: Guide) -> Guide:
    """Save the guide as a new version, keeping every earlier one, and update the portal's row."""
    latest = _latest_stored(guide.key)
    saved = guide.model_copy(update={"version": (latest.version + 1) if latest else 1, "updated_at": time.time()})
    store.put("fraud_guide_versions", {"id": f"{saved.key}.v{saved.version}", **saved.model_dump()})
    store.put("fraud_guides", summary(saved))
    return saved


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


def _sign(id: str, look_for: str, genuine: str, *sources: str) -> Sign:
    return Sign(id=id, look_for=look_for, genuine=genuine, label="derived", trust="machine-confirmed",
                sources=list(sources))


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
              "crs", "rbd_wiki"),
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
              "model_rules"),
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
