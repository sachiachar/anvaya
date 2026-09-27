"""Runtime configuration, read once from environment variables.

Gemini itself is configured by the google-genai SDK's own variables:
  GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
(or GOOGLE_API_KEY for local experiments without Vertex AI).
"""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # Fast model for extraction and bulk work; reasoning model for judgement-heavy steps.
    flash_model: str = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.1-flash-lite")
    pro_model: str = os.getenv("GEMINI_PRO_MODEL", "gemini-3.1-flash-lite")
    # Used when the main model is overloaded (429/5xx) after retries.
    fallback_model: str = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
    # Voice for spoken replies (the assistant's own voice, never a clone of anyone).
    # Tried in order; each has its own quota. GEMINI_TTS_MODEL (one model) still works.
    tts_models: list[str] = field(default_factory=lambda: [m.strip() for m in os.getenv(
        "GEMINI_TTS_MODELS",
        os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-lite-tts") + ",gemini-3.8-flash-tts,gemini-3.1-flash-tts-preview",
    ).split(",") if m.strip()])
    tts_voice: str = os.getenv("GEMINI_TTS_VOICE", "Sulafat")
    # Live, two-way voice calls (Gemini Live API). Uses the same voice.
    live_model: str = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")

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

    # Cloud Storage bucket for recordings and documents. Empty keeps media local.
    media_bucket: str = os.getenv("MEDIA_BUCKET", "")
    local_media_dir: str = os.getenv("LOCAL_MEDIA_DIR", "./data/media")

    # Vault release waiting period. Production would use days; the demo uses seconds.
    release_wait_seconds: int = int(os.getenv("RELEASE_WAIT_SECONDS", "60"))

    # Comma-separated origins allowed to call the API directly (local dev only;
    # in production Firebase Hosting proxies /api to Cloud Run on the same origin).
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")


settings = Settings()
