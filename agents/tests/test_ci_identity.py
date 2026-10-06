"""The production deploy identity (#110): Google gives GitHub Actions credentials only for the
Deploy workflow, on main, in environment "production", in this repository (by its numeric id);
and the deployer holds only what the deploy needs, with no broad project-wide roles."""

import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
SETUP = (REPO / "infra" / "setup-github-actions.sh").read_text()
GOOGLE_AUTH = "google-github-actions/auth"


def _jobs_signing_in_to_google():
    for path in WORKFLOWS.glob("*.yml"):
        for name, job in (yaml.safe_load(path.read_text()).get("jobs") or {}).items():
            if any(GOOGLE_AUTH in str(step.get("uses", "")) for step in job.get("steps", [])):
                yield path.name, name, job


def test_only_the_deploy_workflow_signs_in_to_google_and_always_as_production():
    jobs = list(_jobs_signing_in_to_google())
    assert jobs, "no job signs in to Google: has the deploy workflow moved?"
    for workflow, name, job in jobs:
        assert workflow == "deploy.yml", f"{workflow} signs in to Google; only deploy.yml may"
        assert job.get("environment") == "production", f"deploy.yml job {name!r} must use environment production"


def test_google_trusts_only_deploy_yml_on_main_in_production_by_repository_id():
    condition = re.search(r'CONDITION="([^"]+)"', SETUP).group(1)
    for clause in ("assertion.repository_id == '${REPO_ID}'", "assertion.ref == 'refs/heads/main'",
                   "assertion.workflow_ref == '${WORKFLOW_REF}'", "assertion.environment == 'production'"):
        assert clause in condition, f"the trust condition must include {clause}"
    assert 'WORKFLOW_REF="${REPO}/.github/workflows/deploy.yml@refs/heads/main"' in SETUP
    assert "attribute.repository_id/${REPO_ID}" in SETUP  # bound by id, not by a re-registrable name


def test_the_deployer_has_no_broad_project_wide_roles():
    granted = re.search(r"for role in (.+?); do\s+gcloud projects add-iam-policy-binding", SETUP, re.S).group(1)
    for broad in ("roles/run.admin", "roles/storage.admin", "roles/iam.serviceAccountUser", "roles/logging.viewer",
                  "roles/editor", "roles/owner"):
        assert broad not in granted, f"{broad} must not be granted across the whole project"
    # Acting as service accounts only on the two the deploy uses, and storage only on its buckets.
    assert 'for sa in "$RUNTIME_SA" "$BUILD_SA"' in SETUP
    assert "gcloud storage buckets add-iam-policy-binding" in SETUP
