from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.api.dependencies.auth import AuthClaims, auth_service


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, public_paths: set[str] | None = None) -> None:
        super().__init__(app)
        self._public_paths = public_paths or set()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in self._public_paths:
            return await call_next(request)

        try:
            claims = await auth_service.verify_bearer_token(
                request.headers.get("Authorization")
            )
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        request.state.claims = AuthClaims.model_validate(claims)
        return await call_next(request)

