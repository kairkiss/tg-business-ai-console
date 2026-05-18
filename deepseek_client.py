from __future__ import annotations

from openai import AsyncOpenAI

from config import Config


class DeepSeekClient:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=Config.deepseek_api_key, base_url=Config.deepseek_base_url, timeout=30.0)

    async def chat(self, messages: list[dict[str, str]], model: str, temperature: float, max_tokens: int) -> str:
        resp = await self.client.chat.completions.create(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        return (resp.choices[0].message.content or "").strip()
