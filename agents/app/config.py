"""Runtime configuration, read once from environment variables.

Gemini itself is configured by the google-genai SDK's own variables:
  GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
(or GOOGLE_API_KEY for local experiments without Vertex AI).
"""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Which model each agent uses is in registry.yaml, and which it may use is in policies.yaml,
    # never in environment variables (docs/proposals/agent-security.md, PR-10.11).
    # Voice for spoken replies and live calls (the assistant's own voice, never a clone of anyone).
    tts_voice: str = os.getenv("GEMINI_TTS_VOICE", "Sulafat")
    # Web search for the fraud research agent (Exa, exa.ai; web_search.py). Empty: research and
    # scenario checks stop with "web search isn't set up yet".
    exa_api_key: str = os.getenv("EXA_API_KEY", "")

    # Sign-in (Firebase Auth) and the demo allowlist; see auth.py.
    auth_required: bool = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
    firebase_project_id: str = os.getenv("FIREBASE_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    allowed_emails: list[str] = field(default_factory=lambda: [
        e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()])

    # "file" for local demos (survives restarts), "memory" for tests, "firestore" on Google Cloud.
    store: str = os.getenv("STORE", "memory")
    local_store_path: str = os.getenv("LOCAL_STORE_PATH", "./data/store.json")

    # Cloud KMS key that wraps the vault escrow blob, e.g.
    # projects/P/locations/asia-southeast1/keyRings/life-after/cryptoKeys/vault-escrow
    # Empty means dev mode (see escrow.py).
    kms_key_name: str = os.getenv("KMS_KEY_NAME", "")
    # Cloud KMS key that encrypts the model keys demo users add themselves (PR-10.10), separate
    # from the vault's, e.g. projects/P/locations/asia-southeast1/keyRings/life-after/cryptoKeys/model-keys
    # Empty: dev mode locally; with STORE=firestore, saving a key is refused ("isn't set up yet").
    model_keys_kms_key_name: str = os.getenv("MODEL_KEYS_KMS_KEY_NAME", "")

    # Cloud Storage bucket for recordings and documents. Empty keeps media local.
    media_bucket: str = os.getenv("MEDIA_BUCKET", "")
    local_media_dir: str = os.getenv("LOCAL_MEDIA_DIR", "./data/media")

    # Vault release waiting period. Production would use days; the demo uses seconds.
    release_wait_seconds: int = int(os.getenv("RELEASE_WAIT_SECONDS", "60"))

    # Platform admin portal (PR-12). PLATFORM_ROLES maps emails or @domains to a role:
    #   PLATFORM_ROLES=sachi@example.com:platform_admin, @team.com:risk_reviewer
    # Roles: platform_admin, budget_approver, risk_reviewer, viewer. Anyone else who can
    # sign in gets PLATFORM_DEFAULT_ROLE (read-only "viewer" by default; "none" to refuse).
    platform_roles: dict[str, str] = field(default_factory=lambda: {
        k.strip().lower(): v.strip() for k, v in (
            e.split(":", 1) for e in os.getenv("PLATFORM_ROLES", "").split(",") if ":" in e)})
    platform_default_role: str = os.getenv("PLATFORM_DEFAULT_ROLE", "viewer")
    # Monthly budget the portal compares actual spend against (US dollars), and the salt used
    # to turn user IDs into keys the portal can show without naming anyone.
    monthly_budget_usd: float = float(os.getenv("MONTHLY_BUDGET_USD", "300"))
    user_key_salt: str = os.getenv("USER_KEY_SALT", "anvaya-demo")
    # Cloud Billing export to BigQuery, the only source of the portal's dollar figures:
    # project.dataset (the detailed usage cost table is found there) or project.dataset.table.
    # Empty means the portal says billing data isn't available yet.
    billing_export_table: str = os.getenv("BILLING_EXPORT_TABLE", "")

    # Comma-separated origins allowed to call the API directly (local dev only;
    # in production Firebase Hosting proxies /api to Cloud Run on the same origin).
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")


settings = Settings()
