"""
Firebase Admin SDK initialization for server-side operations (e.g. deleting auth users).

Supports two modes:
  1. GOOGLE_APPLICATION_CREDENTIALS env var pointing to a service account JSON file
  2. FIREBASE_SERVICE_ACCOUNT_JSON env var containing the JSON string directly
     (useful for Railway / containerised deployments where you can't mount a file)

If neither is set, the module logs a warning and `delete_firebase_user` becomes a
safe no-op so the rest of the app still works.
"""

import json
import logging
import os

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

logger = logging.getLogger(__name__)

_initialized = False


def _init_firebase() -> bool:
    """Attempt to initialise the Firebase Admin SDK. Returns True on success."""
    global _initialized
    if _initialized:
        return True

    # Option 1: standard env var (path to service account JSON)
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path:
        try:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            _initialized = True
            logger.info("[FirebaseAdmin] Initialised from GOOGLE_APPLICATION_CREDENTIALS")
            return True
        except Exception as e:
            logger.error(f"[FirebaseAdmin] Failed to init from file: {e}", exc_info=True)

    # Option 2: JSON string in env var (for Railway / CI)
    cred_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if cred_json:
        try:
            service_info = json.loads(cred_json)
            cred = credentials.Certificate(service_info)
            firebase_admin.initialize_app(cred)
            _initialized = True
            logger.info("[FirebaseAdmin] Initialised from FIREBASE_SERVICE_ACCOUNT_JSON")
            return True
        except Exception as e:
            logger.error(f"[FirebaseAdmin] Failed to init from JSON env var: {e}", exc_info=True)

    logger.warning(
        "[FirebaseAdmin] No Firebase credentials found. "
        "Set GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT_JSON to enable auth-user deletion."
    )
    return False


async def delete_firebase_user(uid: str) -> bool:
    """
    Delete a user from Firebase Authentication.

    Returns True if the user was deleted (or didn't exist), False if Firebase
    Admin is not configured.
    """
    if not _init_firebase():
        logger.warning(f"[FirebaseAdmin] Skipping Firebase user deletion for {uid} — SDK not configured")
        return False

    try:
        firebase_auth.delete_user(uid)
        logger.info(f"[FirebaseAdmin] Deleted Firebase auth user: {uid}")
        return True
    except firebase_admin.exceptions.NotFoundError:
        logger.warning(f"[FirebaseAdmin] Firebase auth user not found (already deleted?): {uid}")
        return True
    except Exception as e:
        logger.error(f"[FirebaseAdmin] Error deleting Firebase auth user {uid}: {e}", exc_info=True)
        raise
