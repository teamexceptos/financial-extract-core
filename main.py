from __future__ import annotations

import time

from fastapi import FastAPI

from middleware.api_auth import APIKeyMiddleware
from middleware.user_context import UserContextMiddleware
from routes.health import router as health_router
from routes.verification import router as verification_router

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

app = FastAPI(
    title="Verime",
    description="API for user's verification systems and flow",
    version="1.0.0",
)

app.state.start_time = time.time()

app.add_middleware(APIKeyMiddleware)
app.add_middleware(UserContextMiddleware)

app.include_router(health_router)
app.include_router(verification_router)
