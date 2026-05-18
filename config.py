from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "data.sqlite3"
OFFSET_PATH = BASE_DIR / "offset.txt"
SECRET_PATH = BASE_DIR / "secret.key"

load_dotenv(ENV_PATH)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class Config:
    telegram_bot_token = _env("TELEGRAM_BOT_TOKEN")
    deepseek_api_key = _env("DEEPSEEK_API_KEY")
    deepseek_base_url = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    web_admin_username = _env("WEB_ADMIN_USERNAME", "admin")
    web_admin_password = _env("WEB_ADMIN_PASSWORD")
    web_host = _env("WEB_HOST", "0.0.0.0")
    web_port = int(_env("WEB_PORT", "8787") or "8787")
    bot_owner_id = _env("BOT_OWNER_ID")
    bot_owner_ids_raw = _env("BOT_OWNER_IDS")
    bot_owner_ids = [x.strip() for x in (bot_owner_ids_raw or bot_owner_id).split(",") if x.strip()]
    default_reply_mode = _env("DEFAULT_REPLY_MODE", "manual")


def validate_required() -> None:
    missing = []
    if not Config.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not Config.deepseek_api_key:
        missing.append("DEEPSEEK_API_KEY")
    if not Config.web_admin_password:
        missing.append("WEB_ADMIN_PASSWORD")
    if missing:
        raise RuntimeError("Missing required .env values: " + ", ".join(missing))


def get_session_secret() -> str:
    existing = _env("SESSION_SECRET")
    if existing:
        return existing
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    import secrets
    secret = secrets.token_urlsafe(48)
    SECRET_PATH.write_text(secret, encoding="utf-8")
    os.chmod(SECRET_PATH, 0o600)
    return secret
