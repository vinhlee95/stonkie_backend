from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from connectors.database import SessionLocal
from models.user import User


@dataclass(frozen=True)
class UserDto:
    id: str
    google_sub: str
    email: str
    name: str | None
    avatar_url: str | None
    created_at: datetime
    last_seen_at: datetime


def _to_dto(row: User) -> UserDto:
    return UserDto(
        id=str(row.id),
        google_sub=row.google_sub,
        email=row.email,
        name=row.name,
        avatar_url=row.avatar_url,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


# Every authenticated request calls upsert; skip the write unless the profile changed
# or last_seen_at is older than this, so routine requests stay read-only.
LAST_SEEN_REFRESH_INTERVAL = timedelta(minutes=5)


class UserConnector:
    def upsert(self, *, google_sub: str, email: str, name: str | None, avatar_url: str | None) -> UserDto:
        """Insert the user on first sight; otherwise refresh profile + last_seen_at when changed or stale."""
        profile = {"email": email, "name": name, "avatar_url": avatar_url}
        insert_stmt = insert(User).values(google_sub=google_sub, **profile)
        excluded = insert_stmt.excluded
        stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_users_google_sub",
            set_={**profile, "last_seen_at": func.now()},
            where=or_(
                User.last_seen_at < func.now() - LAST_SEEN_REFRESH_INTERVAL,
                User.email.is_distinct_from(excluded.email),
                User.name.is_distinct_from(excluded.name),
                User.avatar_url.is_distinct_from(excluded.avatar_url),
            ),
        ).returning(User)
        with SessionLocal() as db:
            row = db.execute(select(User).from_statement(stmt)).scalar_one_or_none()
            if row is None:  # conflict but nothing to update: existing row unchanged
                row = db.execute(select(User).where(User.google_sub == google_sub)).scalar_one()
            dto = _to_dto(row)
            db.commit()
        return dto
