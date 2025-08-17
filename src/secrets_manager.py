import json
import logging
import os
from typing import Optional

SERVICE = "weather-lsa-control"


def _get_keyring():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def get_secret(key: str) -> Optional[str]:
    # Environment override allows injecting at runtime (SECRET_MYKEY)
    env_key = f"SECRET_{key.upper()}"
    if os.getenv(env_key):
        return os.getenv(env_key)
    kr = _get_keyring()
    if not kr:
        return None
    try:
        return kr.get_password(SERVICE, key)
    except Exception:
        return None


def set_secret(key: str, value: str) -> bool:
    kr = _get_keyring()
    if not kr:
        logging.warning("Keyring not available; cannot persist secret '%s'", key)
        return False
    try:
        kr.set_password(SERVICE, key, value)
        return True
    except Exception as e:
        logging.warning("Failed to set secret '%s': %s", key, e)
        return False


def has_secret(key: str) -> bool:
    return get_secret(key) is not None


def get_token_json() -> Optional[str]:
    return get_secret("google_oauth_token")


def set_token_json(token_json: str) -> bool:
    return set_secret("google_oauth_token", token_json)


def migrate_from_files_and_env(
    token_file: str,
    developer_token: Optional[str],
    smtp_password: Optional[str],
) -> dict[str, bool]:
    """Migrate known secrets into keyring. Returns dict of results per key."""
    results = {"developer_token": False, "smtp_password": False, "google_oauth_token": False}
    if developer_token:
        results["developer_token"] = set_secret("developer_token", developer_token)
    if smtp_password:
        results["smtp_password"] = set_secret("smtp_password", smtp_password)
    try:
        if os.path.exists(token_file):
            with open(token_file, encoding="utf-8") as f:
                data = f.read()
            # Validate JSON-ish
            json.loads(data)
            results["google_oauth_token"] = set_token_json(data)
    except Exception as e:
        logging.warning("Failed to migrate token.json: %s", e)
    return results


def masked(value: Optional[str]) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"***{value[-4:]}"
