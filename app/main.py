"""FastAPI 애플리케이션 진입점.

실행: uvicorn app.main:app --reload
"""
from fastapi import FastAPI

from app.core.config import get_settings
from app.routers import health

app = FastAPI(title=get_settings().app_name, version="0.1.0")

app.include_router(health.router)
