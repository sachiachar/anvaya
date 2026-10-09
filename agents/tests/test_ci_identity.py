"""The production deploy identity (#110): Google gives GitHub Actions credentials only for the
Deploy workflow, on main, in environment "production", in this repository (by its numeric id);
and the deployer holds only what the deploy needs, with no broad project-wide roles."""

import json
import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
SETUP = (REPO / "infra" / "setup-github-actions.sh").read_text()
GOOGLE_AUTH = "google-github-actions/auth"


def _jobs_signing_in_to_google():
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):  # GitHub runs both
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


ACCESS = json.loads((REPO / "infra" / "deployer-access.json").read_text())


def test_every_release_checks_the_deployers_access_before_anything_deploys():
    """A missing permission stops the release at the start, listed, never half way through a deploy."""
    jobs = yaml.safe_load((WORKFLOWS / "deploy.yml").read_text())["jobs"]
    assert jobs["access"]["environment"] == "production"
    for part in ("api", "web"):
        assert "access" in jobs[part]["needs"], f"the {part} job must wait for the access check"
    run = next(s["run"] for s in jobs["access"]["steps"] if s.get("name") == "Check the deployer's access")
    assert "infra/deployer-access.json" in run
    for section in ("project", "service_accounts", "buckets", "artifact_repositories"):
        assert ACCESS[section], f"{section} is empty"


def test_the_setup_script_grants_what_the_access_list_names():
    # The one permission no predefined role gives narrowly: listing buckets, through the custom role.
    assert "storage.buckets.list" in ACCESS["project"]
    assert re.search(r'BUCKET_PERMISSIONS="[^"]*storage\.buckets\.list', SETUP)
    assert 'roles/${BUCKET_ROLE}' in SETUP
    # The same resources: the source bucket, the image repository and the two service accounts.
    assert list(ACCESS["buckets"]) == ["run-sources-{project}-{region}"]
    assert 'SOURCE_BUCKET="run-sources-${PROJECT_ID}-${REGION}"' in SETUP
    assert list(ACCESS["artifact_repositories"]) == ["cloud-run-source-deploy"] and 'AR_REPO="cloud-run-source-deploy"' in SETUP
    assert set(ACCESS["service_accounts"]) == {"life-after-agents@{project}.iam.gserviceaccount.com",
                                               "{number}-compute@developer.gserviceaccount.com",
                                               "fraud-scheduler@{project}.iam.gserviceaccount.com"}
    assert 'SCHEDULER_SA="fraud-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"' in SETUP
    assert 'RUNTIME_SA="life-after-agents@${PROJECT_ID}.iam.gserviceaccount.com"' in SETUP
    assert 'BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"' in SETUP


def test_broad_roles_are_removed_only_after_every_narrow_grant_is_in_place():
    """Removing first would leave the deployer with nothing if a narrow grant then failed."""
    last_grant = SETUP.index("gcloud artifacts repositories add-iam-policy-binding")
    removal = SETUP.index('gcloud projects remove-iam-policy-binding "$PROJECT_ID"')
    assert removal > last_grant
    assert 'if [[ "$NARROW_READY" == true ]]' in SETUP[last_grant:removal + 200]
