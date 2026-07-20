"""SDK key generation and verification (PBKDF2 via Django hashers)."""
from __future__ import annotations

import secrets
from typing import Optional, Tuple

from django.contrib.auth.hashers import check_password, make_password


def generate_sdk_key() -> Tuple[str, str, str]:
    """
    Return (plaintext, prefix, hash).

    Plaintext format: ag_<8 hex>_<32 urlsafe>
    Only the hash should be persisted.
    """
    prefix = secrets.token_hex(4)  # 8 hex chars
    secret = secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:32]
    plaintext = f"ag_{prefix}_{secret}"
    return plaintext, prefix, make_password(plaintext)


def verify_sdk_key(plain: str, stored_hash: str) -> bool:
    if not plain or not stored_hash:
        return False
    return check_password(plain, stored_hash)


def extract_prefix(plain: str) -> Optional[str]:
    """Parse ag_<prefix>_… → prefix."""
    parts = (plain or "").strip().split("_", 2)
    if len(parts) != 3 or parts[0] != "ag":
        return None
    return parts[1]
