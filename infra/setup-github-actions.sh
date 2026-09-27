#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy without storing any keys.
# GitHub gets short-lived Google credentials through Workload Identity Federation,
# and only for this repository.
#
# Usage: PROJECT_ID=my-project REPO=owner/repo ./infra/setup-github-actions.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REPO:?Set REPO (owner/repo)}"
REGION="${REGION:-asia-southeast1}"
POOL="github"
PROVIDER="github-oidc"
DEPLOYER="github-deployer"
DEPLOYER_SA="${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com \
  firebasehosting.googleapis.com firebaserules.googleapis.com

echo "== Deployer service account"
gcloud iam service-accounts describe "$DEPLOYER_SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$DEPLOYER" --display-name "GitHub Actions deployer"
for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer roles/storage.admin \
            roles/iam.serviceAccountUser roles/serviceusage.serviceUsageConsumer roles/logging.viewer \
            roles/firebasehosting.admin roles/firebaserules.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$DEPLOYER_SA" \
    --role "$role" --condition=None >/dev/null
done

echo "== Workload Identity Federation (only $REPO)"
gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location global --workload-identity-pool "$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location global --workload-identity-pool "$POOL" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${REPO}'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" >/dev/null

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

echo "Done. Pushes to main now deploy only what changed."
