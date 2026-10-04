#!/usr/bin/env bash
# Deploy Anvaya to Google Cloud: agents on Cloud Run, web app on Firebase Hosting.
# Usage: PROJECT_ID=my-project [ALLOWED_EMAILS=a@x.com,@team.com] [BILLING_EXPORT_TABLE=project.dataset] ./infra/deploy.sh
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-asia-southeast1}"          # Singapore
SERVICE="life-after-agents"
SA="life-after-agents@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET="${PROJECT_ID}-life-after-media"
KEYRING="life-after"
KEY="vault-escrow"
KMS_KEY_NAME="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${KEY}"
MODEL_KEYS_KEY="model-keys"   # people's own model keys (PR-10.10), separate from the vault's
MODEL_KEYS_KMS_KEY_NAME="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KEYRING}/cryptoKeys/${MODEL_KEYS_KEY}"

# Who may use the demo: emails and/or @domains, comma-separated. Defaults to agents/.env.
ALLOWED_EMAILS="${ALLOWED_EMAILS:-$(grep -E '^ALLOWED_EMAILS=' agents/.env | cut -d= -f2-)}"
: "${ALLOWED_EMAILS:?Set ALLOWED_EMAILS (e.g. you@example.com,@yourcompany.com)}"
# The Cloud Billing export the admin portal reads actual spend from (project.dataset). Optional.
BILLING_EXPORT_TABLE="${BILLING_EXPORT_TABLE:-}"

gcloud config set project "$PROJECT_ID"

echo "== Enable APIs"
gcloud services enable run.googleapis.com aiplatform.googleapis.com firestore.googleapis.com \
  cloudkms.googleapis.com storage.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

echo "== Service account (least privilege)"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create life-after-agents --display-name "Anvaya agents"
for role in roles/aiplatform.user roles/datastore.user; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" --role "$role" --condition=None >/dev/null
done

echo "== Firestore (native mode)"
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 || \
  gcloud firestore databases create --location="$REGION"

echo "== Private media bucket"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA" --role roles/storage.objectAdmin >/dev/null

echo "== Cloud KMS key for vault escrow (only the agents service account may use it)"
gcloud kms keyrings describe "$KEYRING" --location "$REGION" >/dev/null 2>&1 || \
  gcloud kms keyrings create "$KEYRING" --location "$REGION"
gcloud kms keys describe "$KEY" --keyring "$KEYRING" --location "$REGION" >/dev/null 2>&1 || \
  gcloud kms keys create "$KEY" --keyring "$KEYRING" --location "$REGION" --purpose encryption
gcloud kms keys add-iam-policy-binding "$KEY" --keyring "$KEYRING" --location "$REGION" \
  --member "serviceAccount:$SA" --role roles/cloudkms.cryptoKeyEncrypterDecrypter >/dev/null

echo "== Cloud KMS key for people's own model keys (only the agents service account may use it)"
gcloud kms keys describe "$MODEL_KEYS_KEY" --keyring "$KEYRING" --location "$REGION" >/dev/null 2>&1 || \
  gcloud kms keys create "$MODEL_KEYS_KEY" --keyring "$KEYRING" --location "$REGION" --purpose encryption
gcloud kms keys add-iam-policy-binding "$MODEL_KEYS_KEY" --keyring "$KEYRING" --location "$REGION" \
  --member "serviceAccount:$SA" --role roles/cloudkms.cryptoKeyEncrypterDecrypter >/dev/null

echo "== Gemini API key (AI Studio) in Secret Manager"
# Gemini runs through the AI Studio API key, not Vertex AI, so the same models
# (including gemini-3.8-live) work locally and on Cloud Run.
gcloud services enable secretmanager.googleapis.com
if ! gcloud secrets describe gemini-api-key >/dev/null 2>&1; then
  KEY=$(grep -E '^GOOGLE_API_KEY=' agents/.env | cut -d= -f2-)
  : "${KEY:?Put GOOGLE_API_KEY=<your AI Studio key> in agents/.env first}"
  printf '%s' "$KEY" | gcloud secrets create gemini-api-key --data-file=- --replication-policy=automatic
fi
gcloud secrets add-iam-policy-binding gemini-api-key --member "serviceAccount:$SA" \
  --role roles/secretmanager.secretAccessor >/dev/null

echo "== Deploy agents to Cloud Run"
gcloud run deploy "$SERVICE" --source agents --region "$REGION" --service-account "$SA" \
  --allow-unauthenticated --memory 1Gi --timeout 3600 --cpu-boost \
  --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest" \
  --set-env-vars "^;^GOOGLE_GENAI_USE_VERTEXAI=false;STORE=firestore;MEDIA_BUCKET=$BUCKET;KMS_KEY_NAME=$KMS_KEY_NAME;MODEL_KEYS_KMS_KEY_NAME=$MODEL_KEYS_KMS_KEY_NAME;RELEASE_WAIT_SECONDS=60;AUTH_REQUIRED=true;FIREBASE_PROJECT_ID=$PROJECT_ID;ALLOWED_EMAILS=$ALLOWED_EMAILS;BILLING_EXPORT_TABLE=$BILLING_EXPORT_TABLE"

echo "== Build and deploy the web app to Firebase Hosting"
# Firebase Hosting cannot proxy WebSockets, so live calls go straight to Cloud Run.
RUN_URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
(cd web && npm ci && VITE_LIVE_URL="${RUN_URL/https:/wss:}" npm run build)
firebase deploy --project "$PROJECT_ID" --only hosting,firestore:rules

echo "Done. Open https://anvaya-saathi.web.app (the ${PROJECT_ID}.web.app address redirects there)"
