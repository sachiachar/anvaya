"""Vault key escrow.

How the vault stays unreadable to us and to the AI:
  1. The owner's device encrypts each secret with a data key (AES-GCM).
  2. The vault recipient's device generates a key pair when they accept the role.
  3. The owner's device wraps the data key with the recipient's PUBLIC key.
  4. The server wraps that blob again with Cloud KMS and stores it.
  5. On release, the server asks KMS to unwrap the outer layer and hands the
     inner blob to the recipient, whose device unwraps it with their PRIVATE key.

The server only ever holds a blob that it cannot open (step 3), so a leaked
database or a curious AI tool never sees the data key. KMS adds a second,
access-controlled layer: only the release service account can decrypt, and every
decrypt is audit-logged.

Dev mode (no KMS_KEY_NAME): the outer layer is skipped. Step 3 still protects
the data key, but there is no KMS audit trail. Never run production in dev mode.
"""

from __future__ import annotations

import base64

from .config import settings

DEV_PREFIX = "dev:"


def wrap(recipient_wrapped_key_b64: str) -> str:
    if not settings.kms_key_name:
        if settings.store == "firestore":  # SEC-8.2: dev mode never reaches real storage
            raise RuntimeError("KMS_KEY_NAME isn't set, so the vault escrow can't be wrapped")
        return DEV_PREFIX + recipient_wrapped_key_b64
    from google.cloud import kms

    response = kms.KeyManagementServiceClient().encrypt(
        request={"name": settings.kms_key_name, "plaintext": base64.b64decode(recipient_wrapped_key_b64)}
    )
    return base64.b64encode(response.ciphertext).decode()


def unwrap(escrow_blob: str) -> str:
    if escrow_blob.startswith(DEV_PREFIX):
        return escrow_blob[len(DEV_PREFIX):]
    from google.cloud import kms

    response = kms.KeyManagementServiceClient().decrypt(
        request={"name": settings.kms_key_name, "ciphertext": base64.b64decode(escrow_blob)}
    )
    return base64.b64encode(response.plaintext).decode()
