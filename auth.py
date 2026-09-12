

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from database import get_db
import models
from config import SECRET_KEY

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # tokens last 1 day

# This tells FastAPI's docs page where to send login requests,
# and lets it show an "Authorize" button for testing.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


def hash_password(password: str) -> str:
    """Turns a plain password into a secure hash before saving to the database."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Checks a login attempt's password against the stored hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    """Creates a signed login token containing the username."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Reads and verifies a token. Raises an error if it's invalid or expired."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please log in again."
        )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> models.User:
    """
    Used to protect endpoints. Add `current_user = Depends(get_current_user)`
    to any endpoint to require a valid login token.
    """
    payload = decode_access_token(token)
    username = payload.get("sub")
    if username is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def get_optional_user(
    token: Optional[str] = Depends(OAuth2PasswordBearer(tokenUrl="login", auto_error=False)),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    """Like ``get_current_user`` but tolerates missing/invalid tokens.

    Returns ``None`` when the request has no Authorization header or the token
    is malformed/expired, instead of raising 401. Used by endpoints that are
    useful both unauthenticated (e.g. the patient-signup doctor-picker
    dropdown) and authenticated (e.g. the patient's "Add another doctor"
    modal — though that one still has its own auth check before mutating).
    """
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except HTTPException:
        return None
    username = payload.get("sub")
    if not username:
        return None
    return db.query(models.User).filter(models.User.username == username).first()
