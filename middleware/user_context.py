from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class UserContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/verification"):
            return await call_next(request)

        user_id = request.headers.get("X-User-Id")
        if not user_id:
            return JSONResponse(
                status_code=400,
                content={"detail": "Missing X-User-Id header"},
            )

        request.state.user_id = user_id
        return await call_next(request)

