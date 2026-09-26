from fastapi import APIRouter, Depends
from pydantic import BaseModel

from connectors.user import UserDto
from core.current_user import get_current_user

router = APIRouter(prefix="/api/me", tags=["me"])


class MeResponse(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None


@router.get("", response_model=MeResponse)
def get_me(user: UserDto = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=user.id, email=user.email, name=user.name, avatar_url=user.avatar_url)
