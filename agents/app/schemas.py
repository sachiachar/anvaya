"""Structured outputs the agents produce. These double as the API contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---- Capture loop ---------------------------------------------------------

# Statements (PR-13.1, docs/proposals/listening.md §3): what the owner said, as subject, predicate
# and object. The predicate list is closed and lives here, in code. Anything else is kept as
# "other" with the words, and is never acted on. A wish predicate makes the card a wish.
FACT_PREDICATES = ("owns", "holds_at", "nominee_is", "papers_at", "related_to", "knows_about", "owes", "is_owed")
WISH_PREDICATES = ("wants_to_give", "wants_done", "wants_family_to_know", "wants_to_change", "wants_to_do",
                   "promised_to")
FEELING_PREDICATES = ("worries_about", "proud_of")
PREDICATES = (*FACT_PREDICATES, *WISH_PREDICATES, *FEELING_PREDICATES, "other")


class Statement(BaseModel):
    subject: str = Field(description="Who or what it is about: 'owner' for the owner, or a name, an asset or a place")
    predicate: str = Field(
        description="Exactly one of: " + ", ".join(PREDICATES) + ". Wishes (anything the owner wants, plans or "
        "promised): " + ", ".join(WISH_PREDICATES) + ". Use 'other' if none fits."
    )
    object: str = Field(description="What it is about: an asset, a person, a place or a short phrase")
    recipient: str | None = Field(
        default=None, description="For wants_to_give, promised_to and wants_family_to_know: the person (a name)"
    )
    words: str | None = Field(default=None, description="For 'other': the owner's words for it")

    @model_validator(mode="before")
    @classmethod
    def _closed_list(cls, data):
        """A predicate outside the list becomes 'other', keeping what was said in 'words'."""
        if isinstance(data, dict):
            said = str(data.get("predicate") or "").strip()
            key = said.lower().replace(" ", "_").replace("-", "_")
            if key not in PREDICATES:
                data = {**data, "predicate": "other", "words": data.get("words") or said or None}
            else:
                data = {**data, "predicate": key}
        return data


def is_wish(statements: list[dict] | None) -> bool:
    """Whether what was said includes a wish: decided from the closed list, not by the model."""
    return any((s or {}).get("predicate") in WISH_PREDICATES for s in statements or [])


class Moment(BaseModel):
    kind: Literal["story", "advice", "practical", "wish"]
    title: str = Field(description="Short title, max 8 words. To add detail to a saved moment, reuse its exact title.")
    summary: str = Field(
        description="One or two sentences in English that make sense on their own, without the "
        "conversation (e.g. 'Original papers are in the blue folder in the bedroom steel cupboard; "
        "Priya is responsible for them.' not 'That would be Priya.')"
    )
    quote: str = Field(description="The owner's own words, verbatim, in the original language")
    people: list[str] = []
    places: list[str] = []
    year: str | None = None
    intended_for: list[str] = Field(
        default=[], description="For a wish: the people it is meant for, if the owner said (names only)"
    )
    unsure: bool = Field(
        default=False, description="True if the owner wasn't sure of what they said here ('I think', 'maybe', "
        "'not sure'), so the app can check with them later"
    )
    statements: list[Statement] = Field(
        default=[], description="What the owner said here, as short statements (subject, predicate, object). "
        "Include every wish: anything they want, plan or promised."
    )


class Person(BaseModel):
    name: str
    relationship: str = Field(description="Relationship to the owner, e.g. 'wife', 'son', 'nephew'")


class CaptureResult(BaseModel):
    language: str = Field(description="Language the owner spoke, e.g. Hindi")
    transcript: str
    moments: list[Moment]


# ---- Documents -> financial inventory -------------------------------------


class InventoryItem(BaseModel):
    category: Literal[
        "bank_account", "investment", "retirement", "insurance", "property",
        "loan", "subscription", "digital_account", "other",
    ]
    institution: str
    product: str = Field(description="e.g. 'Term life policy', 'Savings account'")
    account_hint: str = Field(
        description="Last 4 characters only, e.g. '••4821'. Never output a full account or policy number."
    )
    holder_name: str | None = None
    nominee_or_beneficiary: str | None = Field(
        default=None, description="Named nominee/beneficiary, or null if the document shows none"
    )
    approximate_value: str | None = None
    institution_contact: str | None = None
    papers_at: str | None = Field(
        default=None, description="Where the owner keeps this item's papers, if they said, e.g. 'blue folder, bedroom cupboard'"
    )
    intended_for: str | None = Field(
        default=None, description="For property: who the owner wants it to go to, if they said (a name)"
    )
    missing_fields: list[str] = Field(
        default=[], description="Important fields the document does not show, e.g. 'nominee'"
    )
    unsure: bool = Field(
        default=False, description="True if the owner wasn't sure of what they said here ('I think', 'maybe', "
        "'not sure'), so the app can check with them later"
    )


# ---- Conversation (one interface for voice, text, photos, documents) -----


class ConversationTurn(BaseModel):
    heard: str = Field(
        description="Verbatim transcript of what the owner said, in the original language; or a "
        "one-line description of the shared photo or document; or empty if they only typed."
    )
    owner_language: str = Field(
        description="Language and script of the owner's LATEST message only, e.g. 'English', "
        "'Hinglish (Latin script)', 'Hindi (Devanagari)'. The reply must use exactly this."
    )
    reply: str = Field(
        description="Your reply, spoken aloud to the owner: at most 2 short sentences, entirely in "
        "owner_language, usually ending with one gentle follow-up question."
    )
    reply_language: str = Field(description="BCP-47 code of the reply language, e.g. hi-IN, en-IN, ta-IN")
    moments: list[Moment] = Field(default=[], description="Moments worth keeping from this turn only")
    inventory: list[InventoryItem] = Field(
        default=[], description="Financial items found in a shared document or mentioned in this turn"
    )
    people: list[Person] = Field(
        default=[], description="People whose relationship to the owner was stated in this turn "
        "(e.g. 'My wife is Meena', 'Ajay is my nephew'). Not people already in the family list."
    )
    asked_gap: str | None = Field(
        default=None, description="If your reply asks about one of open_gaps, that gap's key, exactly; otherwise null."
    )
    gap_outcome: Literal["answered", "unsure", "not_now", "declined", "none"] = Field(
        default="none", description="How the owner's LATEST message answers last_gap_asked: 'answered'; 'unsure' "
        "(e.g. 'I think it's Meena'); 'not_now' (later, not now); 'declined' (doesn't want to say); or 'none' "
        "if it doesn't address it or there is none."
    )


class Extraction(BaseModel):
    """What is worth keeping from one spoken exchange in a live call."""

    moments: list[Moment] = []
    inventory: list[InventoryItem] = []
    people: list[Person] = []
    asked_gap: str | None = Field(
        default=None,
        description="If what the assistant said asks about one of open_gaps, that gap's key, exactly; otherwise null.",
    )
    gap_outcome: Literal["answered", "unsure", "not_now", "declined", "none"] = Field(
        default="none", description="How what the owner said answers last_gap_asked: 'answered'; 'unsure' "
        "(e.g. 'I think it's Meena'); 'not_now' (later, not now); 'declined' (doesn't want to say); or 'none' "
        "if it doesn't address it or there is none."
    )


class ConversationSummary(BaseModel):
    """A few sentences that sum up one conversation, shown to the owner when it ends."""

    summary: str = Field(description="Two or three short sentences, addressed to the owner as 'you'.")


# ---- Readiness loop -------------------------------------------------------


class Gap(BaseModel):
    title: str = Field(description="The next step as a short action, e.g. 'Make a will for the Mysore flat'")
    why_it_matters: str
    action: Literal["talk", "legal", "vault", "institution", "family"] = Field(
        description="How to close the gap: talk = tell the companion something; legal = needs a will or "
        "other legal document via the legal partner; "
        "institution = something only a bank, insurer or office can do (e.g. submit a nominee change); "
        "vault = add passwords or account access, or choose who receives the vault; "
        "family = confirm who is in the family"
    )
    question_for_owner: str = Field(description="One short, gentle question the companion will ask about this")
    steps: list[str] = Field(
        default=[], description="For 'institution' and 'legal': 2-4 short, concrete steps, no legal advice"
    )
    severity: Literal["high", "medium", "low"]


class Readiness(BaseModel):
    score: int = Field(ge=0, le=100, description="How ready the family would be today, 0-100")
    gaps: list[Gap]


# ---- Planning loop (informal wishes -> legal needs) -----------------------


class LegalNeed(BaseModel):
    wish_excerpt: str
    document_needed: Literal[
        "will", "trust", "power_of_attorney", "healthcare_directive", "guardianship",
        "beneficiary_or_nominee_update", "digital_assets_clause",
    ]
    reason_plain: str = Field(description="Why this needs a legal document, in plain words, no legal advice")
    urgency: Literal["high", "medium", "low"]


class LegalNeeds(BaseModel):
    needs: list[LegalNeed]


# ---- Activation / vault release (Assist level: a person decides) ----------


class SignResult(BaseModel):
    sign_id: str = Field(description="The id of a warning sign from the reference guide")
    result: Literal["found", "ruled_out", "cant_tell"] = Field(
        description="found: the document shows this sign; ruled_out: it clearly doesn't; cant_tell: "
                    "the image doesn't show enough to decide"
    )
    note: str = Field(default="", description="What you saw, in a few words")


class DocumentCheck(BaseModel):
    document_type: str
    is_expected_type: bool = Field(description="True if this is the kind of document requested")
    person_named: str | None = None
    matches_expected_person: bool
    key_date: str | None = Field(default=None, description="Date of death or date of issue")
    issuing_authority: str | None = None
    concerns: list[str] = Field(default=[], description="Anything a human reviewer should look at")
    signs: list[SignResult] = Field(default=[], description="One result for each warning sign in the guide")
    confidence: float = Field(
        ge=0, le=1,
        description=(
            "Probability (0-1) that this is a genuine, unaltered document of the expected type "
            "naming the expected person. This is NOT how sure you are of your description: "
            "a clear photo of the wrong kind of document scores 0."
        ),
    )


# ---- Settlement loop ------------------------------------------------------


class ChecklistItem(BaseModel):
    institution: str
    action: str = Field(description="e.g. 'File death claim', 'Transfer to nominee', 'Close account'")
    documents_needed: list[str]
    priority: Literal["high", "medium", "low"]
    source_id: str = Field(description="Inventory item id this came from, or 'general'")


class ClaimLetter(BaseModel):
    institution: str
    subject: str
    body: str = Field(description="Draft for the executor to review and send; placeholders in [brackets]")


class Settlement(BaseModel):
    checklist: list[ChecklistItem]
    letters: list[ClaimLetter]
    unclaimed_asset_searches: list[str] = Field(
        description="Official national searches for lost assets in the owner's country"
    )
