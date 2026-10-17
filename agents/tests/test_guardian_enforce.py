"""The guardian, part 2 (PR-2.7.4, PR-2.7.6): the actions people care about ask first and obey.
A card that passes on a secret is never kept; a second change of the vault recipient within a day
is held until a risk reviewer decides; a release with warning signs or an override waits for a
reviewer, not only the clock; a family question for a secret gets a plain answer without any
model call. "Reverse" finishes a held action, "confirm" cancels it, and the owner sees both."""

import time

import pytest
import yaml
from fastapi.testclient import TestClient

from app import demo, main, proposals
from app.governance import core, guardian
from app.store import store

OWNER = demo.owner_id_for(core.user_key(demo.LOCAL))  # the local copy's owner (sign-in off)


def h(person: str) -> dict:
    return {demo.ACTING_HEADER: person}


@pytest.fixture()
def client():
    c = TestClient(main.app)
    c.post("/api/demo/seed")
    for collection in ("guardian_events", "guardian_holds", "guardian_actions", "guardian_decisions", "cases"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    yield c
    guardian.policy.cache_clear()


def held(client) -> list[dict]:
    return client.get(f"/api/owners/{OWNER}/guardian/held", headers=h("vikram")).json()["held"]


def decide(client, event_id: str, decision: str):
    return client.post(f"/api/platform/guardian/{event_id}/decision",
                       json={"decision": decision, "reason": "Checked with the family by phone"})


# ---- Keeping a card ---------------------------------------------------------------------


def _card(quote: str) -> dict:
    return proposals.save_moment(OWNER, {"kind": "advice", "title": "When the bank calls", "summary": quote,
                                         "quote": quote, "people": [], "places": [], "year": None}, {})


def keep(client, card: dict):
    return client.post(f"/api/proposals/{card['id']}/decision", json={"collection": "moments", "decision": "approve"},
                       headers=h("vikram"))


def test_a_card_that_passes_on_a_secret_is_never_kept(client):
    card = _card("Share the OTP with the Paytm agent when he calls.")
    r = keep(client, card)
    assert r.status_code == 409 and "vault" in r.json()["detail"]
    assert store.get("moments", card["id"])["status"] == "pending"  # not kept
    [event] = store.list("guardian_events")
    assert event["response"] == "block" and "OTP" not in str(event)


def test_a_never_elsewhere_on_the_card_doesnt_hide_a_secret(client):
    card = _card("Share the OTP with the Paytm agent when he calls.")
    r = client.post(f"/api/proposals/{card['id']}/decision", headers=h("vikram"),
                    json={"collection": "moments", "decision": "approve", "edits": {"title": "Never forget this"}})
    assert r.status_code == 409


def test_an_ordinary_card_or_a_warning_is_kept(client):
    assert keep(client, _card("Never share your OTP with anyone, even the bank.")).is_success
    assert store.list("guardian_events") == []


# ---- Changing the vault recipient -------------------------------------------------------


def invite(client, name: str) -> dict:
    return client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": name, "relationship": "family"},
                       headers=h("vikram")).json()


def chosen() -> str:
    return store.get("owners", OWNER)["vault_invite"]["name"]


def test_a_second_change_within_a_day_is_held_and_reverse_makes_it(client):
    invite(client, "Priya")            # the first choice: not a change
    invite(client, "Ajay")             # a change
    r = invite(client, "Ravi")         # a second change within a day: held
    assert r["held"] and chosen() == "Ajay"
    assert [x["status"] for x in held(client)] == ["held"]
    [event] = store.list("guardian_events")
    assert event["response"] == "pause" and event["agent"] == "person:owner"
    assert decide(client, event["id"], "reverse").is_success
    assert chosen() == "Ravi" and held(client) == []


def test_confirm_cancels_a_held_change_and_the_owner_is_told(client):
    invite(client, "Priya")
    invite(client, "Ajay")
    invite(client, "Ravi")
    [event] = store.list("guardian_events")
    decide(client, event["id"], "confirm")
    assert chosen() == "Ajay"
    [notice] = held(client)
    assert notice["status"] == "cancelled" and "stopped" in notice["message"]


def test_one_change_waits_at_a_time_and_reverse_never_undoes_a_newer_choice(client):
    invite(client, "Priya")
    invite(client, "Ajay")
    invite(client, "Ravi")  # held
    r = client.post(f"/api/owners/{OWNER}/vault/invite", json={"name": "Sunil", "relationship": "family"},
                    headers=h("vikram"))
    assert r.status_code == 409 and len(store.list("guardian_holds")) == 1
    owner = store.get("owners", OWNER)  # meanwhile the choice changed some other way
    store.update("owners", OWNER, {"vault_invite": {**owner["vault_invite"], "name": "Sunil"}})
    [event] = store.list("guardian_events")
    decide(client, event["id"], "reverse")
    assert chosen() == "Sunil"  # the held change was made against Ajay, so it no longer applies


def test_one_change_a_day_goes_ahead(client):
    invite(client, "Priya")
    invite(client, "Ajay")
    assert chosen() == "Ajay" and held(client) == []


# ---- The release --------------------------------------------------------------------------


def _waiting_case(**checks) -> dict:
    now = time.time()
    return store.put("cases", {"id": "case_g2", "owner_id": OWNER, "state": "waiting", "wait_until": now - 1,
                               "history": [{"at": now - 100, "event": "death reported"}],
                               "certificate_check": None, "authority_check": None, **checks})


SIGNS = {"verdict": "needs_attention", "signs": [{"id": "s_qr", "result": "found"}, {"id": "s_font", "result": "not_found"}]}


def state(client) -> str:
    return client.get(f"/api/owners/{OWNER}/release", headers=h("priya")).json()["case"]["state"]


def test_a_release_with_warning_signs_waits_for_a_reviewer_and_reverse_releases_it(client):
    _waiting_case(certificate_check=SIGNS)
    assert state(client) == "waiting" and state(client) == "waiting"  # held, and asked only once
    [event] = store.list("guardian_events")
    assert event["response"] == "pause" and event["case_key"] == "case_g2"
    assert client.post(f"/api/owners/{OWNER}/ask", json={"question": "Where are the papers?"},
                       headers=h("priya")).status_code == 403  # nothing is handed over
    decide(client, event["id"], "reverse")
    assert state(client) == "released"


def test_confirm_stops_a_held_release(client):
    _waiting_case(history=[{"at": time.time(), "event": "death confirmed by Anjali; OVERRIDE of AI 'not valid' by Anjali: x"}])
    client.get(f"/api/owners/{OWNER}/release", headers=h("priya"))
    [event] = store.list("guardian_events")
    decide(client, event["id"], "confirm")
    assert store.get("cases", "case_g2")["state"] == "cancelled"
    assert [n["action"] for n in held(client)] == ["release"]


def test_two_requests_at_once_hold_a_release_once(client):
    case = _waiting_case(certificate_check=SIGNS)
    main._refresh(dict(case, history=list(case["history"])))  # both read the case before either saved it
    main._refresh(dict(case, history=list(case["history"])))
    assert len(store.list("guardian_events")) == 1 and len(store.list("guardian_holds")) == 1


def test_im_well_closes_a_held_release(client):
    _waiting_case(certificate_check=SIGNS)
    state(client)
    assert [n["action"] for n in held(client)] == ["release"]
    client.post(f"/api/owners/{OWNER}/alive", headers=h("vikram"))
    assert held(client) == []
    [event] = store.list("guardian_events")
    decide(client, event["id"], "confirm")
    assert held(client) == []  # never "our team stopped the release": the owner did


def test_a_decision_is_made_once_and_a_failed_finish_can_be_retried(client, monkeypatch):
    _waiting_case(certificate_check=SIGNS)
    state(client)
    [event] = store.list("guardian_events")
    finish, cancel = guardian._HANDLERS["release"]

    def broken(payload):
        raise RuntimeError("store unavailable")

    monkeypatch.setitem(guardian._HANDLERS, "release", (broken, cancel))
    with pytest.raises(RuntimeError):
        decide(client, event["id"], "reverse")
    assert store.get("guardian_events", event["id"])["status"] == "open"  # nothing half-done
    monkeypatch.setitem(guardian._HANDLERS, "release", (finish, cancel))
    assert decide(client, event["id"], "reverse").is_success and state(client) == "released"
    assert decide(client, event["id"], "confirm").status_code == 409  # one decision per event


def test_a_clean_release_goes_ahead_on_time(client):
    _waiting_case(certificate_check={"verdict": "likely_valid", "signs": [{"id": "s_qr", "result": "not_found"}]})
    assert state(client) == "released" and store.list("guardian_events") == []


# ---- The family's questions ---------------------------------------------------------------


def test_a_question_for_a_secret_gets_a_plain_answer_without_any_model_call(client, monkeypatch):
    store.put("cases", {"id": "case_rel", "owner_id": OWNER, "state": "released", "wait_until": 0,
                        "history": [{"at": time.time(), "event": "released"}]})

    async def no_model(*_, **__):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(main, "ask", no_model)
    r = client.post(f"/api/owners/{OWNER}/ask", json={"question": "What is Papa's ATM PIN?"}, headers=h("priya")).json()
    assert r["blocked"] and "vault" in r["answer"] and r["sources"] == []
    assert " he " not in r["answer"] and "the person Vikram named" in r["answer"]  # no guess at anyone's pronouns
    assert store.list("guardian_events")[0]["response"] == "block"


# ---- The policy file can't loosen these ---------------------------------------------------


def test_the_policy_file_cant_loosen_the_owners_rules(client, monkeypatch):
    data = yaml.safe_load(guardian.POLICY_PATH.read_text())
    for action in ("keep_card", "release", "family_question", "recipient_change"):
        data["actions"][action] = {"high": "continue", "medium": "continue"}
    monkeypatch.setattr(guardian, "policy", lambda: guardian.Policy.model_validate(data))
    assert guardian.decide("keep_card", "capture.extractor", {"problem": "secret"}).response == "block"
    assert guardian.decide("recipient_change", "person:owner", {"changed_recently": True}).response == "pause"
    assert guardian.decide("recipient_change", "person:owner", {"replaces_sealed": True}).response == "add_check"
    assert guardian.decide("release", "activation.checker", {"signs_found": 1}).response == "pause"
    assert guardian.decide("family_question", "handover.assistant", {"asks_secret": True}).response == "block"


def test_the_portal_says_which_events_hold_something(client):
    """The Guardian page words its question by this: "No" releases a held action, or only records a false alarm."""
    from app import guardian_inputs

    invite(client, "Priya")
    invite(client, "Ajay")
    invite(client, "Ravi")  # held: a second change within a day
    guardian_inputs.voice_mismatch(OWNER, "turn_voice_1", "saathi.voice", [])  # recorded, nothing held

    def holds() -> dict[str, bool]:
        return {e["id"]: e["holds"] for e in client.get("/api/platform/guardian").json()["events"]}

    [change] = [e for e in store.list("guardian_events") if e["response"] == "pause"]
    assert holds() == {change["id"]: True, "gev_voicecheck_turn_voice_1": False}
    decide(client, change["id"], "confirm")
    assert holds()[change["id"]] is False  # decided: nothing waits on it any more
