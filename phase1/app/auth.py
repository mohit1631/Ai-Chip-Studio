"""
app/auth.py
------------
Password hashing (bcrypt via passlib) and JWT issuing/decoding for Sprint 5.

OAuth note: the roadmap lists "Auth (email/OAuth)". Email/password is fully
implemented below. OAuth (Google/GitHub etc.) needs a real provider SDK and
redirect-flow wiring that doesn't make sense to stub meaningfully here --
`oauth_login_stub` marks where that integration plugs in.
"""

from __future__ import annotations

import datetime

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Returns the subject (user email) if valid, else None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None


def oauth_login_stub(provider: str, provider_token: str) -> str:
    """
    Placeholder for OAuth (Google/GitHub/etc.). Real implementation:
    1. Verify provider_token against the provider's tokeninfo endpoint
    2. Look up or create a User by the verified email
    3. Return create_access_token(user.email) like the password flow

    Raises until wired to a real provider so it fails loudly instead of
    silently "succeeding" with no verification.
    """
    raise NotImplementedError(
        f"OAuth provider '{provider}' is not wired up yet. "
        "Plug in a real token-verification call before using this path."
    )
