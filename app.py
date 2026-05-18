from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import db
from bot import BotRunner
from config import get_session_secret, validate_required
from web import router

bot_runner: BotRunner | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_runner
    validate_required()
    db.init_db()
    bot_runner = BotRunner()
    await bot_runner.start()
    yield
    if bot_runner:
        await bot_runner.stop()


app = FastAPI(title="Telegram Business AI Console", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=get_session_secret(), same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
