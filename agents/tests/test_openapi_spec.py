"""docs/openapi.yaml matches the code, and every route says its group and how it signs in."""

from app import main, openapi_spec


def test_the_published_spec_matches_the_code():
    written = openapi_spec.SPEC_FILE.read_text()
    assert written == openapi_spec.dump(openapi_spec.build(main.app)), (
        "docs/openapi.yaml is out of date: run `cd agents && uv run python -m app.openapi_spec`")


def test_every_route_has_a_group_and_a_sign_in():
    spec = openapi_spec.build(main.app)
    assert spec["security"] == [{"firebase": []}]
    for path, operations in spec["paths"].items():
        for op in operations.values():
            assert len(op["tags"]) == 1
            if path == "/api/healthz":
                assert op["security"] == []
            elif path.startswith("/internal/"):
                assert op["security"] == [{"scheduler": []}]
            else:
                assert "security" not in op, f"{path} would skip the Firebase sign-in"
    assert {t["name"] for t in spec["tags"]} >= {"Owner", "Handover", "Admin portal: guardian"}


def test_the_live_call_is_described():
    description = openapi_spec.build(main.app)["info"]["description"]
    assert "/api/owners/{owner_id}/live" in description and '{"type": "end"}' in description
