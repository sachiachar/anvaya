import pytest

from app import release


def make_case():
    return release.new_case("case_1", "owner_1", {"is_expected_type": True})


def test_happy_path_needs_people_and_waiting_period():
    case = make_case()
    release.confirm_death(case, "reviewer-a")
    release.attach_authority_check(case, {"is_expected_type": True})
    release.confirm_authority(case, "reviewer-b", now=1000.0)
    assert case["state"] == "waiting"

    release.refresh(case, now=1000.0 + release.settings.release_wait_seconds - 1)
    assert not release.is_released(case)

    release.refresh(case, now=1000.0 + release.settings.release_wait_seconds)
    assert release.is_released(case)


def test_cannot_skip_death_confirmation():
    case = make_case()
    with pytest.raises(release.ReleaseError):
        release.attach_authority_check(case, {})
    with pytest.raises(release.ReleaseError):
        release.confirm_authority(case, "reviewer")


def test_authority_needs_a_submitted_proof():
    case = make_case()
    release.confirm_death(case, "reviewer")
    with pytest.raises(release.ReleaseError):
        release.confirm_authority(case, "reviewer")


def test_owner_response_cancels_during_waiting_period():
    case = make_case()
    release.confirm_death(case, "r1")
    release.attach_authority_check(case, {})
    release.confirm_authority(case, "r2", now=0.0)
    release.owner_responded(case)
    release.refresh(case, now=10**9)
    assert case["state"] == "cancelled"
    assert not release.is_released(case)


def test_same_reviewer_cannot_confirm_both_steps():
    case = make_case()
    release.confirm_death(case, "asha")
    release.attach_authority_check(case, {"verdict": "likely_valid"})
    with pytest.raises(release.ReleaseError):
        release.confirm_authority(case, "asha")
    release.confirm_authority(case, "ravi")
    assert case["state"] == "waiting"


def test_overriding_a_not_valid_verdict_needs_a_reason():
    case = release.new_case("case_2", "owner_1", {"verdict": "not_valid"})
    with pytest.raises(release.ReleaseError):
        release.confirm_death(case, "asha")
    with pytest.raises(release.ReleaseError):
        release.confirm_death(case, "asha", "ok")  # too short to be a reason
    release.confirm_death(case, "asha", "Original certificate seen in person at the branch")
    assert "OVERRIDE" in case["history"][-1]["event"]
