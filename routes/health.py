from __future__ import annotations

import time

from fastapi import APIRouter, Request

router = APIRouter(tags=["Health"])


@router.get("/health")
def health(request: Request):
    start_time = getattr(request.app.state, "start_time", None)
    uptime_seconds = int(time.time() - start_time) if start_time else None
    return {
        "status": "ok",
        "version": request.app.version,
        "uptime_seconds": uptime_seconds,
    }

