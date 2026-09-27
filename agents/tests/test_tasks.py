"""Next-step list rules, using the duplicates and rewording seen in a real run."""

import pytest
from fastapi.testclient import TestClient

from app import main, tasks
from app.store import store

OWNER = "owner_demo_vikram"


def gap(title, why, severity="medium", action="talk"):
    return {"title": title, "why_it_matters": why, "action": action, "question_for_owner": "?", "steps": [],
            "severity": severity}


KEYS_1 = gap("Create a plan for the bedroom cupboard keys",
             "Currently, you are the only one with the keys to your important documents.")
KEYS_2 = gap("Secure the bedroom cupboard keys",
             "Your original documents are in a locked cupboard, but you currently hold the only keys.")
KEYS_3 = gap("Secure access to physical keys",
             "Your original documents are in a locked cupboard, but you currently hold the only keys. "
             "If you are incapacitated, your family may struggle to access these.")
WILL = gap("Create a will for the Mysore flat and watch",
           "Without a will, your wishes for the Mysore flat and your watch may not be legally binding.",
           "high", "legal")
WILL_REWORDED = gap("Formalize inheritance plans",
                    "Your wishes for the Mysore flat and watch are not legally binding without a will.",
                    "high", "legal")


@pytest.fixture(autouse=True)
def fresh():
    TestClient(main.app).post("/api/demo/seed")


def test_near_duplicates_are_merged():
    tasks.refresh(OWNER, [KEYS_1, KEYS_2, KEYS_3, WILL])
    titles = [t["title"] for t in tasks.open_tasks(OWNER)]
    assert len(titles) == 2 and titles[0] == WILL["title"]


def test_a_done_step_never_comes_back_reworded():
    tasks.refresh(OWNER, [WILL])
    will = tasks.open_tasks(OWNER)[0]
    store.update("tasks", will["id"], {"status": "done"})
    tasks.refresh(OWNER, [WILL_REWORDED, KEYS_1])
    assert [t["title"] for t in tasks.open_tasks(OWNER)] == [KEYS_1["title"]]


def test_at_most_five_open_steps_and_ids_stay_stable():
    topics = ["passport", "pension", "locker", "car", "shares", "gold", "land", "email"]
    many = [gap(f"{t.title()} details", f"Family needs the {t} information") for t in topics]
    tasks.refresh(OWNER, many)
    first = {t["title"]: t["id"] for t in tasks.open_tasks(OWNER)}
    assert len(first) == 5
    tasks.refresh(OWNER, many)
    assert {t["title"]: t["id"] for t in tasks.open_tasks(OWNER)} == first


def test_different_institutions_stay_separate():
    lic = gap("Update LIC nominee", "The nominee change must be submitted to LIC.", action="institution")
    sbi = gap("Update SBI nominee", "The nominee change must be submitted to SBI.", action="institution")
    tasks.refresh(OWNER, [lic, sbi])
    assert len(tasks.open_tasks(OWNER)) == 2
