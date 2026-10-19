"""FastAPI dependency limiting the public chat endpoints per caller IP."""

from fastapi import Depends, HTTPException, Request, status

from services.chat_limit import ChatLimitExceeded, ChatLimitService, client_ip


def get_chat_limit_service() -> ChatLimitService:
    return ChatLimitService()


async def limit_chat(request: Request, service: ChatLimitService = Depends(get_chat_limit_service)) -> None:
    ip = client_ip(dict(request.headers), request.client.host if request.client else None)
    try:
        await service.check(ip)
    except ChatLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=exc.window.message,
            headers={"Retry-After": str(exc.window.seconds)},
        ) from exc
