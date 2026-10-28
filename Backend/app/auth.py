"""
Authentication utilities for Watch Party.
Provides host secret generation, hashing, and verification.
"""

import secrets
from typing import Tuple

import bcrypt


def generate_host_secret() -> str:
    """
    Generate a cryptographically random host secret.
    Returns a URL-safe base64 string (43 characters).
    """
    return secrets.token_urlsafe(32)


def hash_secret(plain_secret: str) -> str:
    """
    Hash a secret using bcrypt.
    Returns the hashed secret as a string.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain_secret.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_secret(plain_secret: str, hashed_secret: str) -> bool:
    """
    Verify a plain secret against a hashed secret.
    Returns True if the secret matches, False otherwise.
    """
    try:
        return bcrypt.checkpw(
            plain_secret.encode('utf-8'),
            hashed_secret.encode('utf-8')
        )
    except Exception:
        return False


def generate_host_credentials() -> Tuple[str, str]:
    """
    Generate a new host secret and its hash.
    Returns (plain_secret, hashed_secret).
    """
    plain_secret = generate_host_secret()
    hashed_secret = hash_secret(plain_secret)
    return plain_secret, hashed_secret