"""WebSocket auth helpers (2026-08-24).

Browsers can't send custom headers on a WebSocket handshake, so the JWT
has to travel in the query string: `ws://host/path?token=<jwt>`. This
module validates that token the same way `auth.get_current_user` does
for HTTP requests, then returns the resolved `models.User`.

On any failure we raise `WebSocketException` with code 1008 (policy
violation) so the client gets a clean close reason instead of a raw
401.
"""
from fastapi import WebSocketException, status

from auth import decode_access_token
import models


def authenticate_ws_token(token: str, db) -> models.User:
    """Validate a JWT from the query string and return the user.

    Raises WebSocketException(1008) on any failure path (missing token,
    bad signature, expired, unknown user). 1008 is the standard close
    code for policy violations and is what browsers expect for auth
    failures on WS."""
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    try:
        payload = decode_access_token(token)
    except Exception:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    username = (payload or {}).get("sub")
    if not username:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    user = (
        db.query(models.User).filter(models.User.username == username).first()
    )
    if not user:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return user