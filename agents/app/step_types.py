"""The closed lists a next step is typed from (PR-13.3). They live
apart from step_guard.py so the fraud guides can check a tactic's tags against them too."""

STEP_TYPES = ("tell_family", "record_with_institution", "legal_document", "keep_papers", "add_to_vault",
              "gather_information", "talk_more", "plan_with_family")
CHANNELS = ("institution_official", "registered_professional", "family_named_by_owner", "owner_self", "anvaya_app")
FLAGS = ("payment", "secret", "started_by_other", "deadline")
