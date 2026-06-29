"""
app/rate_limit.py
-------------------
Shared slowapi Limiter instance. One Limiter per process -- import this
same object everywhere rather than constructing a new one per router, or
each router would track its own independent counters.

Keyed by client IP (get_remote_address) -- fine for a single-instance
dev/staging deployment. Behind a load balancer/proxy in real production,
make sure X-Forwarded-For is trusted correctly (e.g. via a proxy that
strips/sets it) or rate limits will key off the proxy's IP for everyone.

default_limits applies settings.rate_limit_default to every route
automatically (that's how slowapi's global default actually works -- it
has to be passed to the Limiter constructor, not just SlowAPIMiddleware).
Tighter limits (settings.rate_limit_login) are applied directly on
specific endpoints like /auth/login and /auth/register via
@limiter.limit(...), since those are the classic brute-force /
credential-stuffing / spam-signup targets and deserve a stricter cap than
general API traffic.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
)

