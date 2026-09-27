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
