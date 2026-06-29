"""
app/routers/auth.py
----------------------
Sprint 5 (User Accounts): email/password registration + login.
OAuth path is defined in app/auth.py::oauth_login_stub and intentionally
raises until a real provider is wired in.

NOTE: deliberately does NOT use `from __future__ import annotations` (PEP
563 postponed evaluation), unlike most other files in this app. slowapi's
@limiter.limit decorator wraps register()/login() with functools.wraps;
when annotations are postponed (stored as strings), FastAPI/Pydantic have
to resolve them via typing.get_type_hints() against the function's
__globals__ at startup -- and the wrapped function produced by
@limiter.limit doesn't carry that in a way Pydantic can use, causing
`PydanticUndefinedAnnotation: name 'UserCreate' is not defined` even
though UserCreate is correctly imported right above. Keeping annotations
eagerly-evaluated here (no postponed-annotations import) sidesteps that
resolution failure entirely, since the real types are already bound by
the time the decorator runs.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit import limiter
from app.schemas import Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.rate_limit_login)  # same stricter cap as login: anti spam-signup
def register(request: Request, payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
@limiter.limit(settings.rate_limit_login)  # anti brute-force / credential-stuffing
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # OAuth2PasswordRequestForm uses "username" as the field name; we treat
    # it as the email address.
    user = db.scalar(select(User).where(User.email == form_data.username))
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password"
        )
    return Token(access_token=create_access_token(subject=user.email))


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
