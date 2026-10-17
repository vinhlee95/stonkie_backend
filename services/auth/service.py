"""AuthService: the only entry point for the auth router, and the only module in this package that does I/O.

It constructs the user, session and Google ID token connectors (`x or XConnector()`); `tokens.py` holds the
pure token/claim checks.
"""

from connectors.google_id_token import (
    GoogleIdTokenConnector,
    GoogleIdTokenInvalidError,
    GoogleIdTokenNotConfiguredError,
)
from connectors.session import SessionConnector
from connectors.user import UserConnector, UserDto
from services.auth.tokens import (
    AuthError,
    GoogleAuthNotConfiguredError,
    get_backend_jwt_secret,
    google_claims,
    verify_backend_token,
)


class AuthService:
    def __init__(
        self,
        users: UserConnector | None = None,
        sessions: SessionConnector | None = None,
        google: GoogleIdTokenConnector | None = None,
    ):
        self._users = users or UserConnector()
        self._sessions = sessions or SessionConnector()
        self._google = google or GoogleIdTokenConnector()

    def sign_in_with_google(self, id_token: str) -> tuple[str, UserDto]:
        """Swaps a Google ID token for a new opaque session token and the (upserted) user.

        Raises GoogleAuthNotConfiguredError if GOOGLE_IOS_CLIENT_ID is unset, AuthError for a rejected token.
        """
        try:
            claims = google_claims(self._google.verify(id_token))
        except GoogleIdTokenNotConfiguredError as exc:
            raise GoogleAuthNotConfiguredError(str(exc)) from exc
        except GoogleIdTokenInvalidError as exc:
            raise AuthError(str(exc)) from exc

        user = self._users.upsert(
            google_sub=claims.sub, email=claims.email, name=claims.name, avatar_url=claims.picture
        )
        return self._sessions.create(user.id), user

    def logout(self, authorization: str | None) -> None:
        """Revokes the bearer session, if any; a missing, non-bearer or unknown token is a no-op."""
        scheme, _, token = (authorization or "").partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token:
            self._sessions.delete(token)


def authenticate_bearer(
    authorization: str | None,
    users: UserConnector | None = None,
    sessions: SessionConnector | None = None,
) -> UserDto:
    """Verify an `Authorization: Bearer <token>` header and return the user.

    A JWT (web, minted by the Next.js server) upserts the user from its claims; anything else is an
    opaque iOS session token looked up in `user_sessions`.
    Raises AuthNotConfiguredError if a JWT arrives and the secret is unset, AuthError for any missing/invalid token.
    """
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise AuthError("missing bearer token")

    if token.count(".") != 2:
        user = (sessions or SessionConnector()).user_for(token)
        if user is None:
            raise AuthError("unknown or expired session")
        return user

    claims = verify_backend_token(token, get_backend_jwt_secret())
    users = users or UserConnector()
    return users.upsert(google_sub=claims.sub, email=claims.email, name=claims.name, avatar_url=claims.picture)
