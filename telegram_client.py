from __future__ import annotations

import httpx

from config import Config


class TelegramClient:
    def __init__(self) -> None:
        self.token = Config.telegram_bot_token
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        self.last_send_payload: dict | None = None

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, payload: dict | None = None) -> dict:
        resp = await self.client.post(f"{self.base}/{method}", json=payload or {})
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "description": f"HTTP {resp.status_code}"}
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description') or resp.status_code}")
        return data

    async def get_me(self) -> dict:
        return (await self.request("getMe"))["result"]

    async def delete_webhook(self) -> None:
        await self.request("deleteWebhook", {"drop_pending_updates": False})

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["business_connection", "business_message", "edited_business_message", "deleted_business_messages", "message"]}
        if offset is not None:
            payload["offset"] = offset
        return (await self.request("getUpdates", payload))["result"]

    async def send_chat_action(self, business_connection_id: str | None, chat_id: int, action: str = "typing") -> None:
        payload: dict = {"chat_id": chat_id, "action": action}
        if business_connection_id:
            payload["business_connection_id"] = business_connection_id
        await self.request("sendChatAction", payload)

    async def send_message(self, business_connection_id: str | None, chat_id: int, text: str, reply_to_message_id: int | None = None, quote_reply: bool = False) -> dict:
        payload: dict = {"chat_id": chat_id, "text": text[:4096]}
        if business_connection_id:
            payload["business_connection_id"] = business_connection_id
        if quote_reply and reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
        self.last_send_payload = payload.copy()
        return (await self.request("sendMessage", payload))["result"]
