"""Auth feature. Routers import only from here: AuthService is the entry point; every other module in this
package is a private helper of it."""

from services.auth.service import AuthService
from services.auth.tokens import (
    AuthError,
    AuthNotConfiguredError,
    GoogleAuthNotConfiguredError,
    TokenClaims,
    get_backend_jwt_secret,
    verify_backend_token,
)

__all__ = [
    "AuthError",
    "AuthNotConfiguredError",
    "AuthService",
    "GoogleAuthNotConfiguredError",
    "TokenClaims",
    "get_backend_jwt_secret",
    "verify_backend_token",
]
