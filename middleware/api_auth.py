from __future__ import annotations

import os
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, header_name: str = "X-API-Key", env_var: str = "FINANCE_EXTRACTOR_CORE_API_KEY") -> None:
        super().__init__(app)
        self.header_name = header_name
        self.env_var = env_var

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/openapi.json", "/docs", "/redoc"}:
            return await call_next(request)

        expected = os.getenv(self.env_var) or os.getenv("FINANCE_EXTRACTOR_CORE_API_KEY")
        if not expected:
            return JSONResponse(
                status_code=500,
                content={"detail": "Server API key is not configured"},
            )

        provided = request.headers.get(self.header_name)
        if not provided or provided != expected:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)

