import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from connectors.database import SessionLocal
from connectors.user import UserDto, _to_dto
from models.user import User
from models.user_session import UserSession

SESSION_TTL = timedelta(days=90)
# Sliding expiry is bumped at most this often, so routine requests stay read-only.
SLIDE_INTERVAL = timedelta(days=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionConnector:
    """Opaque bearer sessions (iOS). Callers handle raw tokens; only their hash is stored."""

    def create(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with SessionLocal() as db:
            db.add(
                UserSession(user_id=user_id, token_hash=hash_token(token), expires_at=datetime.now(UTC) + SESSION_TTL)
            )
            db.commit()
        return token

    def user_for(self, token: str) -> UserDto | None:
        """The session's user, sliding its expiry; None (and the row deleted) when unknown or expired."""
        token_hash = hash_token(token)
        now = datetime.now(UTC)
        with SessionLocal() as db:
            found = db.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(UserSession.token_hash == token_hash)
            ).one_or_none()
            if found is None:
                return None
            session, user = found
            if session.expires_at <= now:
                db.execute(delete(UserSession).where(UserSession.id == session.id))
                db.commit()
                return None
            dto = _to_dto(user)
            if session.last_used_at < now - SLIDE_INTERVAL:
                db.execute(
                    update(UserSession)
                    .where(UserSession.id == session.id)
                    .values(last_used_at=now, expires_at=now + SESSION_TTL)
                )
                db.commit()
        return dto

    def delete(self, token: str) -> None:
        with SessionLocal() as db:
            db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
            db.commit()
