from fastapi import APIRouter

from app.api.routes import items, login, private, steam_accounts, users, utils
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(steam_accounts.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
