from app.loops import review_summary
from app.schemas import DocumentCheck


def check(**overrides):
    base = dict(document_type="Death certificate", is_expected_type=True, person_named="Vikram Rao",
                matches_expected_person=True, concerns=[], confidence=0.95)
    return DocumentCheck(**{**base, **overrides})


def test_wrong_document_type_scores_zero_even_if_model_is_sure():
    # The case from the demo: a screenshot of a shopping app, model said 1.0.
    s = review_summary(check(document_type="Mobile app screenshot", is_expected_type=False, confidence=1.0))
    assert s["confidence"] == 0.0
    assert s["verdict"] == "not_valid"


def test_wrong_person_is_capped_and_flagged():
    s = review_summary(check(matches_expected_person=False, confidence=0.99))
    assert s["confidence"] == 0.3
    assert s["verdict"] == "needs_attention"


def test_concerns_or_low_confidence_need_attention():
    assert review_summary(check(concerns=["Seal is blurred"]))["verdict"] == "needs_attention"
    assert review_summary(check(confidence=0.6))["verdict"] == "needs_attention"


def test_clean_document_is_likely_valid():
    assert review_summary(check())["verdict"] == "likely_valid"


# ---- A sign that applies only from a date (PR-15.5) ----------------------------------------

def _guide():
    from app import fraud_guides
    return fraud_guides.BUILT_IN["IN.death_certificate"]


def _qr(registration_date, result="found", death_date="2022-03-14"):
    from app.schemas import SignResult
    return check(registration_date=registration_date, death_date=death_date,
                 signs=[SignResult(sign_id="qr_missing", result=result, note="No QR code")])


def _row(summary, sign_id="qr_missing"):
    return next(s for s in summary["signs"] if s["id"] == sign_id)


def test_no_qr_code_cant_count_on_a_certificate_registered_before_it_was_required():
    """The first measured-quality run: every genuine certificate from before October 2023 was flagged."""
    summary = review_summary(_qr("2022-03-16"), _guide())
    row = _row(summary)
    assert row["result"] == "ruled_out" and "before 01 October 2023" in row["note"]
    assert "No QR code" in row["note"]  # what the check had said stays visible to the reviewer
    assert summary["verdict"] == "likely_valid"
    assert _row(review_summary(_qr("2022-03-16", result="cant_tell"), _guide()))["result"] == "ruled_out"


def test_no_qr_code_still_counts_from_the_date_it_applies():
    summary = review_summary(_qr("2025-03-16", death_date="2025-03-14"), _guide())
    assert _row(summary)["result"] == "found" and summary["verdict"] == "needs_attention"
    assert _row(review_summary(_qr("2023-10-01", death_date="2023-09-28"), _guide()))["result"] == "found"


def test_a_backdated_registration_doesnt_rule_a_sign_out():
    """A forgery from 2024 that prints a 2022 registration: registered before the death is impossible."""
    assert _row(review_summary(_qr("2022-03-16", death_date="2024-05-02"), _guide()))["result"] == "found"
    assert _row(review_summary(_qr("2022-03-16", death_date=None), _guide()))["result"] == "found"  # no death date


def test_an_unreadable_or_ambiguous_date_never_rules_a_sign_out():
    for unreadable in (None, "", "sometime in 2022", "32-13-2022", "10/03/2022"):  # 10/03 could be 3 October
        assert _row(review_summary(_qr(unreadable), _guide()))["result"] == "found", unreadable


def test_printed_date_formats_are_read():
    from datetime import date

    from app.loops import _date
    for text in ("2022-03-16", "16-03-2022", "16/03/2022", "16.03.2022", "16 March 2022", "16 Mar 2022",
                 "16-Mar-2022", "16-MAR-2022", "16th March 2022", "March 16, 2022"):
        assert _date(text) == date(2022, 3, 16), text
    assert _date("05/05/2022") == date(2022, 5, 5)  # the same either way
    assert _date("10/03/2022") is None              # could be month-first


def test_a_saved_guide_version_gets_the_date_only_if_it_never_had_one():
    from app import fraud_guides
    saved = _guide().model_dump()
    for sign in saved["signs"]:
        sign.pop("applies_from")  # a version saved before signs had the field
    old = fraud_guides._with_built_in_dates(fraud_guides.Guide.model_validate(saved))
    assert next(s for s in old.signs if s.id == "qr_missing").applies_from == "2023-10-01"
    assert next(s for s in old.signs if s.id == "dates_impossible").applies_from is None
    cleared = _guide().model_copy(update={"signs": [s.model_copy(update={"applies_from": None}) for s in _guide().signs]})
    cleared = fraud_guides.Guide.model_validate(cleared.model_dump())  # a reviewer cleared it on purpose
    assert next(s for s in fraud_guides._with_built_in_dates(cleared).signs if s.id == "qr_missing").applies_from is None


def test_a_bad_date_in_a_guide_is_refused_when_it_loads():
    import pytest

    from app import fraud_guides
    sign = _guide().signs[0].model_dump() | {"applies_from": "1 October 2023"}
    with pytest.raises(ValueError):
        fraud_guides.Sign.model_validate(sign)


# ---- A registration number from another year (PR-15.5) ------------------------------------

def _numbered(year, registration_date="2022-03-16", death_date="2022-03-14", result="ruled_out"):
    from app.schemas import SignResult
    return check(registration_date=registration_date, death_date=death_date, registration_number_year=year,
                 signs=[SignResult(sign_id="registration_details", result=result, note="Looks fine")])


def test_a_registration_number_from_another_year_is_found_in_code():
    """The re-run: a number with 2019 on a 2022 registration passed as likely valid."""
    summary = review_summary(_numbered(2019), _guide())
    row = _row(summary, "registration_details")
    assert row["result"] == "found" and "2019" in row["note"] and "16 March 2022" in row["note"]
    assert "Looks fine" in row["note"]  # what the check had said stays visible
    assert summary["verdict"] == "needs_attention"


def test_a_matching_year_or_a_late_registration_leaves_the_models_result():
    for case in (_numbered(2022),                                                     # the year of registration
                 _numbered(2021, registration_date="2022-01-05", death_date="2021-12-20"),  # numbered by the death
                 _numbered(None),                                                     # no year in the number
                 _numbered(2019, registration_date=None)):                            # nothing to compare with
        assert _row(review_summary(case, _guide()), "registration_details")["result"] == "ruled_out"


def test_code_never_rules_out_what_the_model_found():
    assert _row(review_summary(_numbered(2022, result="found"), _guide()), "registration_details")["result"] == "found"


def test_only_the_year_is_read_never_the_whole_number():
    """Rule 5: ID numbers are kept to their last 4 characters; a year isn't an ID number."""
    from app.schemas import DocumentCheck
    assert "registration_number_year" in DocumentCheck.model_fields
    assert "registration_number" not in DocumentCheck.model_fields
    assert "Never the number itself" in DocumentCheck.model_fields["registration_number_year"].description


def test_the_code_check_is_declared_on_the_sign_and_filled_into_saved_guides():
    from app import fraud_guides
    assert next(s for s in _guide().signs if s.id == "registration_details").code_check == "registration_year"
    saved = _guide().model_dump()
    for sign in saved["signs"]:
        sign.pop("code_check")
    old = fraud_guides._with_built_in_dates(fraud_guides.Guide.model_validate(saved))
    assert next(s for s in old.signs if s.id == "registration_details").code_check == "registration_year"
