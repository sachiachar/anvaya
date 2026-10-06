#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy without storing any keys.
# GitHub gets short-lived Google credentials through Workload Identity Federation, and only
# for the Deploy workflow (.github/workflows/deploy.yml) running on main, in the "production"
# environment, in this repository. A workflow on any other branch, or any other workflow file,
# gets nothing (issue #110).
#
# The deployer can only do what the deploy needs: build from source (Cloud Build, the source
# bucket, the Artifact Registry repository), update the API's Cloud Run service, act as the
# API's and the build's service accounts (and no other), and publish Firebase Hosting and the
# Firestore rules. Running this again removes the broader project-wide roles it used to grant.
#
# Usage: PROJECT_ID=my-project REPO=owner/repo ./infra/setup-github-actions.sh
# Needs gcloud (project owner) and gh (repository admin), and the API deployed once
# (infra/deploy.sh), so its service account, source bucket and Artifact Registry repository exist.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REPO:?Set REPO (owner/repo)}"
REGION="${REGION:-asia-southeast1}"
POOL="github"
PROVIDER="github-oidc"
DEPLOYER="github-deployer"
DEPLOYER_SA="${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com"
RUNTIME_SA="life-after-agents@${PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"  # what source deploys build as
SOURCE_BUCKET="run-sources-${PROJECT_ID}-${REGION}"                  # where source deploys upload
BUILD_BUCKET="${PROJECT_ID}_cloudbuild"
AR_REPO="cloud-run-source-deploy"
# The numeric id survives a rename, and a deleted name can't be taken over by someone else.
REPO_ID=$(gh api "repos/${REPO}" --jq .id)
WORKFLOW_REF="${REPO}/.github/workflows/deploy.yml@refs/heads/main"
MEMBER="serviceAccount:${DEPLOYER_SA}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com \
  firebasehosting.googleapis.com firebaserules.googleapis.com

echo "== Deployer service account"
gcloud iam service-accounts describe "$DEPLOYER_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$DEPLOYER" --display-name "GitHub Actions deployer"

echo "== Project-wide roles: only what has no narrower place to grant it"
for role in roles/run.developer roles/cloudbuild.builds.editor roles/serviceusage.serviceUsageConsumer \
            roles/firebasehosting.admin roles/firebaserules.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "$MEMBER" --role "$role" --condition=None >/dev/null
done
# Remove the broad roles earlier versions of this script granted across the whole project.
for role in roles/run.admin roles/storage.admin roles/iam.serviceAccountUser roles/logging.viewer \
            roles/artifactregistry.writer; do
  gcloud projects remove-iam-policy-binding "$PROJECT_ID" --member "$MEMBER" --role "$role" \
    --condition=None >/dev/null 2>&1 || true
done

echo "== Narrow grants: the API's and the build's service accounts, the source and build buckets, the image repository"
for sa in "$RUNTIME_SA" "$BUILD_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --member "$MEMBER" \
    --role roles/iam.serviceAccountUser >/dev/null
done
for bucket in "$SOURCE_BUCKET" "$BUILD_BUCKET"; do
  if gcloud storage buckets describe "gs://${bucket}" >/dev/null 2>&1; then
    gcloud storage buckets add-iam-policy-binding "gs://${bucket}" --member "$MEMBER" --role roles/storage.admin >/dev/null
  else
    echo "   (bucket ${bucket} doesn't exist yet: deploy once with infra/deploy.sh, then run this again)"
  fi
done
gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --location "$REGION" --member "$MEMBER" \
  --role roles/artifactregistry.writer >/dev/null

echo "== Workload Identity Federation: only deploy.yml, on main, in environment production, in ${REPO} (#${REPO_ID})"
MAPPING="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref,attribute.environment=assertion.environment"
CONDITION="assertion.repository_id == '${REPO_ID}' && assertion.ref == 'refs/heads/main' && assertion.workflow_ref == '${WORKFLOW_REF}' && assertion.environment == 'production'"
gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" --location global --workload-identity-pool "$POOL" >/dev/null 2>&1; then
  # Already set up (for example, before this change, or after renaming the repository): tighten it.
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" --location global --workload-identity-pool "$POOL" \
    --attribute-mapping "$MAPPING" --attribute-condition "$CONDITION" >/dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location global --workload-identity-pool "$POOL" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "$MAPPING" --attribute-condition "$CONDITION"
fi
POOL_PATH="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --role roles/iam.workloadIdentityUser \
  --member "${POOL_PATH}/attribute.repository_id/${REPO_ID}" >/dev/null
# The old binding by repository name: a renamed repository's old name could be registered by someone else.
gcloud iam service-accounts remove-iam-policy-binding "$DEPLOYER_SA" --role roles/iam.workloadIdentityUser \
  --member "${POOL_PATH}/attribute.repository/${REPO}" >/dev/null 2>&1 || true

echo "== GitHub environment \"production\": deployments from main only"
gh api -X PUT "repos/${REPO}/environments/production" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api "repos/${REPO}/environments/production/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx main || \
  gh api -X POST "repos/${REPO}/environments/production/deployment-branch-policies" -f name=main -f type=branch >/dev/null

echo "== GitHub repository variables (no secrets: these are ids and public web config)"
RUN_URL=$(gcloud run services describe life-after-agents --region "$REGION" --format 'value(status.url)')
gh variable set GCP_PROJECT_ID --repo "$REPO" --body "$PROJECT_ID"
gh variable set GCP_REGION --repo "$REPO" --body "$REGION"
gh variable set GCP_DEPLOYER_SA --repo "$REPO" --body "$DEPLOYER_SA"
gh variable set GCP_WIF_PROVIDER --repo "$REPO" \
  --body "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
gh variable set VITE_LIVE_URL --repo "$REPO" --body "${RUN_URL/https:/wss:}"
while IFS='=' read -r key value; do
  [[ "$key" == VITE_FIREBASE_* ]] && gh variable set "$key" --repo "$REPO" --body "$value"
done < web/.env

echo "Done. Only deploy.yml on main, in environment production, can deploy; pushes to main deploy only what changed."
