#!/usr/bin/env python3
"""Run the NetGuard API: uvicorn app.main:app --reload --port 8000"""

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
