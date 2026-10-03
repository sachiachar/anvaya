"""The AI-first loops. Each function is one Gemini step inside a loop.

Autonomy levels (see the use-cases document):
  - Act within limits: transcription, extraction, gap detection
  - Propose:           anything entering the archive, legal handoff, claim letters
  - Assist:            death-certificate and legal-authority checks (a person decides)
"""

from __future__ import annotations

import json
import re

from .config import settings
from .gemini import generate_json
from .schemas import (
    CaptureResult,
    ConversationTurn,
    DocumentCheck,
    Extraction,
    InventoryItem,
    LegalNeeds,
    Readiness,
    Settlement,
)


def persona(owner: dict) -> str:
    """One companion, used everywhere (typed chat and live calls), so it never changes character."""
    first_name = owner["name"].split()[0]
    return (
        f"You are Saathi, the Anvaya companion. You are talking with {owner['name']} "
        f"({owner.get('age')}, {owner.get('city')}, {owner.get('country')}), helping them leave "
        f"stories, advice, wishes and practical knowledge for their family"
        f"{': ' + ', '.join(owner['family']) if owner.get('family') else ''}.\n"
        f"Character: warm, calm and respectful, like a trusted family friend. Always address them "
        f"as \"{first_name} ji\". Short, simple sentences. Never rush them.\n"
        "Language: reply in exactly the language and script of their LATEST message, even if "
        "earlier messages used another language. English gets English, Hinglish in Latin letters "
        "gets Hinglish in Latin letters, Hindi in Devanagari gets Devanagari. Never mix languages "
        "within a reply.\n"
        "Honesty: nothing is saved until they tap Keep on a card, so never say you have saved, "
        "noted or recorded something, and never ask whether to add or save something: the card "
        "already asks them. You can say you've added it to their list to review. You cannot change "
        "anything with banks, insurers, employers or the law. If they want to change a nominee "
        "or beneficiary, treat it as a wish and tell them it must also be updated with the "
        "institution. If they ask for help with that, explain the usual next step in one sentence "
        "(e.g. a nominee change form from the insurer or bank) and suggest keeping it as a "
        "reminder.\n"
        "People: when they mention someone whose relationship to them you don't know (for "
        "example a nominee), ask who that person is.\n"
        "Never ask for passwords, PINs or full account or policy numbers (the last 4 digits are "
        "enough); the secure vault in the app is for those. No legal or financial advice."
    )


PRIVACY_RULES = (
    "Never output full account, policy, card or ID numbers: keep only the last 4 characters. "
    "Never output passwords, PINs or recovery codes, even if visible."
)


async def capture_audio(audio: bytes, mime_type: str, owner: dict) -> CaptureResult:
    """Capture loop: a voice recording becomes a transcript plus candidate moments."""
    return await generate_json(
        agent="capture.extractor",
        model=settings.flash_model,
        schema=CaptureResult,
        media=[(audio, mime_type)],
        system=(
            "You help an older person record their life story for their family. "
            "Transcribe faithfully in the language spoken. Then split the recording into "
            "distinct moments. Keep each quote verbatim in the original language; write "
            "titles and summaries in English. Classify each moment: story, advice, practical "
            "(facts the family will need, such as where documents are) or wish (what the "
            "owner wants to happen). Do not invent anything that was not said. " + PRIVACY_RULES
        ),
        prompt=(
            f"Owner: {owner['name']}, lives in {owner.get('city', 'unknown city')}, "
            f"{owner.get('country', 'unknown country')}. Family: {', '.join(owner.get('family', []))}. "
            "Spell local names and places as they are normally written in that region."
        ),
    )


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
# Claims that something was saved or changed. Nothing is saved until the owner taps Keep.
_FALSE_CLAIMS = re.compile(
    r"\b(i('ve| have) (noted|saved|recorded|updated)|noted (it|that|this)|"
    r"note kar (liya|li|di)|save kar (liya|li|di|diya)|record kar (liya|li))\b"
    r"|नोट कर (लिया|ली)|सेव कर (लिया|दिया)",
    re.IGNORECASE,
)


_ASKS_TO_ADD = re.compile(
    r"\b(would you like|do you want|shall i|should i|can i)( me)? (to )?(add|save|note|record|keep)\b",
    re.IGNORECASE,
)


def _script(text: str) -> str | None:
    devanagari, latin = len(_DEVANAGARI.findall(text)), len(_LATIN.findall(text))
    if devanagari + latin < 3:
        return None
    return "Devanagari" if devanagari > latin else "Latin"


def reply_problems(owner_message: str, reply: str) -> list[str]:
    """Rules checked in code, because a light model follows them only most of the time."""
    problems = []
    wanted, got = _script(owner_message), _script(reply)
    if wanted and got and wanted != got:
        problems.append(f"The owner wrote in {wanted} script, so the reply must be in {wanted} script only.")
    if _ASKS_TO_ADD.search(reply):
        problems.append("Don't ask whether to add or save something; the Keep card already asks. "
                        "Ask a question about their life instead.")
    if _FALSE_CLAIMS.search(reply):
        problems.append("Don't say you noted or saved anything: it is only added to their list "
                        "to review until they tap Keep.")
    return problems


async def converse(owner: dict, history: list[dict], text: str | None,
                   media: tuple[bytes, str] | None, known: dict) -> ConversationTurn:
    """Conversation loop: one turn of the owner's single chat, whatever they send.

    Voice is transcribed, photos and documents are read, and anything worth keeping
    comes back as candidate moments or inventory items for the owner to approve.
    A reply that breaks the language or honesty rules is rewritten once.
    """
    transcript = "\n".join(f"{t['role']}: {t['text']}" for t in history[-12:])
    prompt = (
        f"Conversation so far:\n{transcript or '(none yet)'}\n\n"
        f"What is already saved: {json.dumps(known, ensure_ascii=False)}\n\n"
        f"The owner's LATEST message (reply in its language): {text or '(see attachment)'}"
    )
    result = await _converse_once(owner, prompt, media)
    problems = reply_problems(text or result.heard, result.reply)
    if problems:
        prompt += "\n\nYour previous reply broke these rules; write it again:\n- " + "\n- ".join(problems)
        result = await _converse_once(owner, prompt, media)
    return result


async def _converse_once(owner: dict, prompt: str, media: tuple[bytes, str] | None) -> ConversationTurn:
    return await generate_json(
        agent="saathi.chat",
        model=settings.flash_model,
        schema=ConversationTurn,
        media=[media] if media else None,
        system=(
            persona(owner) + "\n\n"
            "Each turn: understand what they said or shared, reply in one or two short sentences, "
            "and ask ONE gentle follow-up question that fills a gap in what is saved (people, "
            "dates, where documents are, who should receive what).\n"
            "Voice: transcribe verbatim in 'heard'. Photo of people or places: describe it in "
            "'heard' and ask who is in it and what happened; only create a moment once they tell "
            "the story. Financial document (policy, statement, passbook): add an inventory item "
            "and mention anything missing, such as a nominee. If they give new details about an "
            "item already discussed, return that item again with the same institution and last "
            "4 digits, including the new details.\n"
            "Only include moments, inventory and people from THIS turn. If a moment with the same "
            "meaning is already saved, don't add it again. A short answer to your own question "
            "(like 'Yes, that would be Priya') is a detail of the moment you asked about: return "
            "that moment again with its exact saved title and a summary that includes the new "
            "detail. Every summary must make sense on its own. Keep quotes verbatim. " + PRIVACY_RULES
        ),
        prompt=prompt,
    )


def live_instructions(owner: dict, known: dict, recent: list[dict] | None = None) -> str:
    """System instruction for a live voice call: the same companion as the typed chat,
    carrying on from the recent conversation (e.g. a question asked from a next step)."""
    history = "\n".join(f"{t['role']}: {t['text']}" for t in (recent or [])[-8:])
    return (
        persona(owner) + "\n\n"
        "This is a voice call. Speak naturally: one or two short sentences, then ONE gentle "
        "follow-up question that fills a gap (people, dates, where documents are, who should "
        "receive what). Let them finish before you answer.\n"
        "Words to avoid on this call: 'noted', 'I've noted', 'saved', 'recorded', 'made a note'. "
        "Say 'I've added that to your list to review' instead, because nothing is saved until "
        "they tap Keep.\n"
        "If they say 'not yet' to something that needs action (a will, a nominee change), don't "
        "drop it: say it's now on their next steps in the app.\n"
        f"Already saved: {json.dumps(known, ensure_ascii=False)}. Don't ask about those again.\n"
        + (f"Conversation so far (carry on from here; if your last message was a question, "
           f"start the call by gently asking it again):\n{history}" if history else "")
    )


async def extract_from_exchange(owner: dict, heard: str, said: str, known: dict) -> Extraction:
    """After each exchange in a live call: what is worth keeping (the owner approves later)."""
    return await generate_json(
        agent="capture.extractor",
        model=settings.flash_model,
        schema=Extraction,
        system=(
            "From one exchange of a voice call, pull out what the owner said that is worth "
            "keeping for their family: stories, advice, wishes, practical facts, and financial "
            "items they mention, and people whose relationship to them they state. Quotes are "
            "the owner's words, verbatim. Skip small talk and anything already saved (same "
            "meaning counts). A short answer to the assistant's question adds detail to the moment "
            "it was about: return that moment with its exact saved title and a summary that makes "
            "sense on its own. Return empty lists if nothing new. " + PRIVACY_RULES
        ),
        prompt=json.dumps({"owner": owner["name"], "owner_said": heard, "assistant_said": said,
                           "already_saved": known}, ensure_ascii=False),
    )


async def extract_document(image: bytes, mime_type: str, owner: dict) -> InventoryItem:
    """Capture loop: a photo of a policy or statement becomes a financial inventory entry."""
    return await generate_json(
        agent="documents.reader",
        model=settings.flash_model,
        schema=InventoryItem,
        media=[(image, mime_type)],
        system=(
            "You read financial documents (bank statements, insurance policies, investment "
            "and pension statements) and record what a family would need to find and claim "
            "the asset later. List important fields the document does not show in "
            "missing_fields, especially a missing nominee or beneficiary. " + PRIVACY_RULES
        ),
        prompt=f"The document belongs to {owner['name']} ({owner.get('country', 'unknown country')}).",
    )


async def assess_readiness(owner: dict, inventory: list[dict], moments: list[dict],
                           vault_labels: list[str], legal_docs: list[str], tasks: list[dict],
                           vault_state: dict) -> Readiness:
    """Readiness loop: how prepared would the family be today, and the next steps to get there."""
    context = {
        "owner": {k: owner.get(k) for k in ("name", "age", "country", "family")},
        "inventory": inventory,
        "practical_and_wish_moments": [m for m in moments if m.get("kind") in ("practical", "wish")],
        "vault": vault_state,
        "vault_labels_owner_allowed_ai_to_see": vault_labels,
        "signed_legal_documents": legal_docs,
        "next_steps_already_done_or_dismissed": [
            {"step": t["title"], "why": t["why_it_matters"], "status": t["status"]}
            for t in tasks if t["status"] != "open"],
    }
    return await generate_json(
        agent="readiness.planner",
        model=settings.pro_model,
        schema=Readiness,
        system=(
            "You check whether a family could settle this person's affairs if they died "
            "tomorrow. Look for missing nominees or beneficiaries, asset types that are "
            "common for someone of this age and country but absent, missing will or "
            "digital-assets clause, no named vault recipient, and unclear practical "
            "details. Each gap is a next step with an action the app can help with. A wish that "
            "changes a nominee means the owner must submit a change with that institution "
            "('institution'); a wish about property usually needs a will ('legal'). Never suggest "
            "a step that is already done or dismissed, even in other words (a done will covers "
            "all inheritance wishes), and count done steps as progress in the score. Each step "
            "must be distinct: merge overlapping ones. Order by severity; at most 5. You do not see vault "
            "secrets and must not ask for passwords. No legal advice."
        ),
        prompt=json.dumps(context, ensure_ascii=False),
    )


async def find_legal_needs(owner: dict, wishes: list[dict], inventory: list[dict],
                           has_vault: bool) -> LegalNeeds:
    """Planning loop: which informal wishes need a legal document to take effect."""
    context = {
        "owner": {k: owner.get(k) for k in ("name", "age", "country", "family")},
        "wishes": wishes,
        "inventory": inventory,
        "has_digital_vault": has_vault,
    }
    return await generate_json(
        agent="legal.needs",
        model=settings.pro_model,
        schema=LegalNeeds,
        system=(
            "You flag informal wishes that will not take effect without a legal document, "
            "so the owner can take them to a licensed legal partner. Explain in plain words "
            "why a document is needed. Do NOT give legal advice, name specific clauses, or "
            "say what the law requires in detail; the partner does that. If the owner has a "
            "digital vault, always check for a digital-assets clause."
        ),
        prompt=json.dumps(context, ensure_ascii=False),
    )


async def check_document(image: bytes, mime_type: str, expected: str, person: str) -> DocumentCheck:
    """Activation loop (Assist): pre-check a death certificate or proof of legal authority.

    The result is shown to our team; a person always makes the decision.
    """
    return await generate_json(
        agent="activation.checker",
        model=settings.pro_model,
        schema=DocumentCheck,
        media=[(image, mime_type)],
        system=(
            "You pre-screen documents for a human reviewer. Report what the document is, "
            "who it names, and anything that looks inconsistent, altered or incomplete. "
            "Set confidence to the probability that the document is genuine, of the expected "
            "type, and names the expected person; not how sure you are of your description. "
            "You never approve anything yourself. " + PRIVACY_RULES
        ),
        prompt=f"Expected document: {expected}. Expected person: {person}.",
    )


def review_summary(check: DocumentCheck) -> dict:
    """Turn a model pre-check into what the reviewer sees, with rules the model can't override.

    - Wrong kind of document: confidence 0, verdict "not_valid".
    - Right kind but a different person: confidence capped at 0.3, "needs_attention".
    - Any concern, or confidence under 0.8: "needs_attention".
    - Otherwise: "likely_valid". A person still makes the decision.
    """
    summary = check.model_dump()
    if not check.is_expected_type:
        summary.update(confidence=0.0, verdict="not_valid")
    elif not check.matches_expected_person:
        summary.update(confidence=min(check.confidence, 0.3), verdict="needs_attention")
    elif check.concerns or check.confidence < 0.8:
        summary["verdict"] = "needs_attention"
    else:
        summary["verdict"] = "likely_valid"
    return summary


async def plan_settlement(owner: dict, inventory: list[dict], moments: list[dict]) -> Settlement:
    """Settlement loop: turn the inventory into a claims checklist and draft letters (Propose)."""
    context = {
        "deceased": {k: owner.get(k) for k in ("name", "country")},
        "executor": owner.get("vault_recipient_name"),
        "inventory": inventory,
        "practical_moments": [m for m in moments if m.get("kind") == "practical"],
    }
    return await generate_json(
        agent="settlement.planner",
        model=settings.pro_model,
        schema=Settlement,
        system=(
            "You help an executor settle an estate. For each inventory item, give the next "
            "action and the documents the institution will typically ask for. Draft one short, "
            "polite claim or notification letter per institution with [placeholders] for "
            "details the executor must fill in. Name the official national search services "
            "for unclaimed assets in the deceased's country (for example RBI UDGAM in India, "
            "ATO lost super search in Australia, NAIC Life Insurance Policy Locator in the "
            "US). This is guidance, not legal advice. " + PRIVACY_RULES
        ),
        prompt=json.dumps(context, ensure_ascii=False),
    )
