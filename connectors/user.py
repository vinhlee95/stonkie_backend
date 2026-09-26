from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
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
    last_login_at: datetime


def _to_dto(row: User) -> UserDto:
    return UserDto(
        id=str(row.id),
        google_sub=row.google_sub,
        email=row.email,
        name=row.name,
        avatar_url=row.avatar_url,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


class UserConnector:
    def upsert(self, *, google_sub: str, email: str, name: str | None, avatar_url: str | None) -> UserDto:
        """Insert the user on first sight, otherwise refresh profile fields and last_login_at."""
        profile = {"email": email, "name": name, "avatar_url": avatar_url}
        stmt = (
            insert(User)
            .values(google_sub=google_sub, **profile)
            .on_conflict_do_update(
                constraint="uq_users_google_sub",
                set_={**profile, "last_login_at": func.now()},
            )
            .returning(User)
        )
        with SessionLocal() as db:
            row = db.execute(select(User).from_statement(stmt)).scalar_one()
            dto = _to_dto(row)
            db.commit()
        return dto
