"""FastAPI dependency limiting the public chat endpoints per caller IP."""

from fastapi import Depends, HTTPException, Request, status

from services.chat_limit import ChatLimitExceeded, ChatLimitService


def get_chat_limit_service() -> ChatLimitService:
    return ChatLimitService()


async def limit_chat(request: Request, service: ChatLimitService = Depends(get_chat_limit_service)) -> None:
    try:
        await service.check(request.headers, request.client.host if request.client else None)
    except ChatLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.message,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
