"""The Fraud page's graph view: the guides, what they hold and the research records as nodes and links.
All data here is synthetic."""

import time

import pytest

from app import fraud_graph, fraud_guides
from app.fraud_bundle import records
from app.fraud_guides import BUILT_IN
from app.store import store
from tests.test_governance import client, h  # noqa: F401  (the portal fixture, signed-in roles)

KEY = "IN.death_certificate"
INDIA = BUILT_IN[KEY]


@pytest.fixture(autouse=True)
def clean():
    for collection in ("fraud_guides", "fraud_guide_versions"):
        for doc in store.list(collection):
            store.delete(collection, doc["id"])
    fraud_guides.clear_saved()


def by_id(graph):
    return {n["id"]: n for n in graph["nodes"]}


def scenario(id, title, **kw):
    records.put("fraud_scenarios", {"id": id, "at": time.time(), "guide_key": KEY, "scenario": title, "claim": "",
                                    "coverage": "covered", "caught_by": "owner alert", "review": "unconfirmed", **kw})


def test_the_guide_and_what_it_holds_are_nodes_with_links():
    graph = fraud_graph.build(KEY)
    nodes = by_id(graph)
    assert nodes[f"guide:{KEY}"]["group"] == "guide"
    assert {n["group"] for n in graph["nodes"]} >= {"guide", "sign", "tactic", "source"}
    assert sum(n["group"] == "sign" for n in graph["nodes"]) == len(INDIA.signs)
    assert all(link["source"] in nodes and link["target"] in nodes for link in graph["links"])
    tactic = INDIA.tactics[0]
    caught = {link["target"] for link in graph["links"]
              if link["source"] == f"item:{KEY}/tactics/{tactic.id}" and link["kind"] == "caught by"}
    assert caught and all(nodes[c]["group"] in ("sign", "safeguard") for c in caught)


def test_research_records_join_the_graph_by_status():
    tactic = INDIA.tactics[0].id
    scenario("scn_gap", "Synthetic gap scenario", coverage="gap", sequence_id=tactic)
    scenario("scn_faded", "Synthetic faded scenario", review="faded")
    scenario("scn_out", "Synthetic ruled out scenario", review="removed")
    scenario("scn_cue", "Synthetic cue scenario", cue={"owners": 20, "means": "no nominee", "pattern": "x"})
    records.put("fraud_drafts", {"id": "drf_1", "at": time.time(), "guide_key": KEY, "kind": "tactic",
                                 "tactic": "Synthetic pending tactic", "review": "pending"})
    graph = fraud_graph.build(KEY)
    nodes = by_id(graph)
    assert nodes["record:fraud_scenarios:scn_gap"]["group"] == "gap"
    assert "record:fraud_scenarios:scn_faded" not in nodes
    assert nodes["record:fraud_scenarios:scn_out"]["group"] == "ruled_out"
    assert nodes["record:fraud_scenarios:scn_cue"]["cue"] == {"owners": 20}  # a count only
    assert nodes["record:fraud_drafts:drf_1"]["group"] == "draft"
    assert {"source": "record:fraud_scenarios:scn_gap", "target": f"item:{KEY}/tactics/{tactic}",
            "kind": "imagined from"} in graph["links"]


def test_a_scenario_added_to_the_guide_is_one_node():
    scenario("scn_ok", "Synthetic validated scenario", review="added")
    fraud_guides.add_items(KEY, scenarios=[{"id": "scn_ok", "title": "Synthetic validated scenario",
                                            "caught_by": "owner alert", "coverage": "covered", "sources": []}])
    nodes = by_id(fraud_graph.build(KEY))
    assert nodes[f"item:{KEY}/scenarios/scn_ok"]["group"] == "scenario"
    assert "record:fraud_scenarios:scn_ok" not in nodes


def test_the_portal_serves_the_graph_and_each_nodes_history(client):  # noqa: F811
    fraud_guides.save_version(INDIA)
    graph = client.get("/api/platform/fraud/graph", headers=h("judge")).json()
    sign = next(n for n in graph["nodes"] if n["group"] == "sign")
    events = client.get("/api/platform/fraud/history", params=sign["history"], headers=h("judge")).json()["events"]
    assert [e["action"] for e in events] == ["Creation"]
    scenario("scn_h", "Synthetic scenario with history")
    events = client.get("/api/platform/fraud/history", params={"collection": "fraud_scenarios", "item": "scn_h"},
                        headers=h("judge")).json()["events"]
    assert [e["action"] for e in events] == ["Creation"]
    assert client.get("/api/platform/fraud/history", params={"collection": "owners", "item": "x"},
                      headers=h("judge")).status_code == 400
