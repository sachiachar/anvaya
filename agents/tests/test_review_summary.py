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


def _qr(registration_date, result="found"):
    from app.schemas import SignResult
    return check(registration_date=registration_date,
                 signs=[SignResult(sign_id="qr_missing", result=result, note="No QR code")])


def _row(summary, sign_id="qr_missing"):
    return next(s for s in summary["signs"] if s["id"] == sign_id)


def test_no_qr_code_cant_count_on_a_certificate_registered_before_it_was_required():
    """The first measured-quality run: every genuine certificate from before October 2023 was flagged."""
    summary = review_summary(_qr("16-03-2022"), _guide())
    assert _row(summary)["result"] == "ruled_out" and "before 01 October 2023" in _row(summary)["note"]
    assert summary["verdict"] == "likely_valid"


def test_no_qr_code_still_counts_from_the_date_it_applies():
    summary = review_summary(_qr("16-03-2025"), _guide())
    assert _row(summary)["result"] == "found" and summary["verdict"] == "needs_attention"
    assert _row(review_summary(_qr("01-10-2023"), _guide()))["result"] == "found"  # on the day itself


def test_an_unreadable_registration_date_never_rules_a_sign_out():
    for unreadable in (None, "", "sometime in 2022", "32-13-2022"):
        assert _row(review_summary(_qr(unreadable), _guide()))["result"] == "found", unreadable


def test_printed_date_formats_are_read():
    from datetime import date

    from app.loops import _date
    for text in ("16-03-2022", "16/03/2022", "16.03.2022", "2022-03-16", "16 March 2022", "16 Mar 2022", "March 16, 2022"):
        assert _date(text) == date(2022, 3, 16), text


def test_a_saved_guide_version_gets_the_date_from_the_built_in_sign():
    from app import fraud_guides
    old = _guide().model_copy(update={"signs": [s.model_copy(update={"applies_from": None}) for s in _guide().signs]})
    fixed = fraud_guides._with_built_in_dates(old)
    assert next(s for s in fixed.signs if s.id == "qr_missing").applies_from == "2023-10-01"
    assert next(s for s in fixed.signs if s.id == "dates_impossible").applies_from is None
