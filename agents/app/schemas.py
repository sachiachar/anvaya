"""Structured outputs the agents produce. These double as the API contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---- Capture loop ---------------------------------------------------------


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


class Extraction(BaseModel):
    """What is worth keeping from one spoken exchange in a live call."""

    moments: list[Moment] = []
    inventory: list[InventoryItem] = []
    people: list[Person] = []


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
