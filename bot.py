from __future__ import annotations

import asyncio
import random
import re
import time
from datetime import datetime
from typing import Any

import db
from config import Config, OFFSET_PATH
from deepseek_client import DeepSeekClient
from output_filter import filter_reply
from telegram_client import TelegramClient


def as_bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def as_int(value: str | None, default: int) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return default


def as_float(value: str | None, default: float) -> float:
    try:
        return float(str(value))
    except Exception:
        return default


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


TEXT_TYPES = {"text", "caption"}
MEDIA_FIELDS = ("photo", "video", "animation", "document", "sticker", "voice", "audio", "video_note", "contact", "location", "venue", "poll", "dice")


class BotRunner:
    def __init__(self) -> None:
        self.telegram = TelegramClient()
        self.deepseek = DeepSeekClient()
        self.task = None
        self.running = False
        self.bot_id = None
        self.bot_info = None
        self.pending_text: dict[str, dict[str, Any]] = {}
        self.pending_media: dict[str, dict[str, Any]] = {}
        self.last_auto_reply: dict[str, float] = {}

    async def start(self):
        if not self.task:
            self.running = True
            self.task = asyncio.create_task(self._run(), name="telegram-polling")

    async def stop(self):
        self.running = False
        for bucket in list(self.pending_text.values()) + list(self.pending_media.values()):
            task = bucket.get("task")
            if task:
                task.cancel()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await self.telegram.close()

    def offset(self):
        try:
            return int(OFFSET_PATH.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def save_offset(self, value: int):
        OFFSET_PATH.write_text(str(value), encoding="utf-8")

    async def _run(self):
        try:
            self.bot_info = await self.telegram.get_me()
            self.bot_id = self.bot_info.get("id")
            await self.telegram.delete_webhook()
            db.log("INFO", "telegram", f"机器人启动成功：@{self.bot_info.get('username','unknown')}，使用 getUpdates 长轮询")
        except Exception as exc:
            db.log("ERROR", "telegram", f"启动检查失败：{exc}")
        while self.running:
            try:
                updates = await self.telegram.get_updates(self.offset(), timeout=50)
                for update in updates:
                    await self.handle_update(update)
                    if update.get("update_id") is not None:
                        self.save_offset(int(update["update_id"]) + 1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                db.log("ERROR", "telegram", f"轮询异常：{exc}")
                await asyncio.sleep(5)

    async def handle_update(self, update: dict[str, Any]):
        if "business_connection" in update:
            bc = update["business_connection"]
            db.upsert_business_connection(bc)
            account = db.get_account_by_connection(bc.get("id") or bc.get("business_connection_id"))
            name = (account or {}).get("account_name") or "未知账号"
            db.log("INFO", "account", f"[{name}] Business 连接已更新：{bc.get('id') or bc.get('business_connection_id')}，enabled={bc.get('is_enabled')}")
        elif "business_message" in update:
            await self.handle_business_message(update["business_message"])
        elif "edited_business_message" in update:
            msg = update["edited_business_message"]
            db.log("INFO", "telegram", f"收到编辑后的 Business 消息：chat={msg.get('chat',{}).get('id')} message={msg.get('message_id')}")
        elif "deleted_business_messages" in update:
            db.log("INFO", "telegram", "收到 Business 消息删除事件")
        elif "message" in update:
            msg = update["message"]
            chat = msg.get("chat") or {}
            db.upsert_chat(chat, db.now_iso())
            if msg.get("text") and chat.get("id") is not None:
                ck = db.conversation_key(None, chat.get("id"))
                db.add_message(chat["id"], msg.get("message_id"), "in", msg.get("text"), "user", ck, message_type="text", raw_json=msg)
            db.log("INFO", "telegram", f"收到普通 Bot 消息并已记录：chat={chat.get('id')}")

    def should_skip_self(self, msg):
        sender = msg.get("from") or {}
        if self.bot_id and sender.get("id") == self.bot_id:
            return True
        if str(sender.get("id") or "") in set(Config.bot_owner_ids):
            return True
        if msg.get("sender_business_bot"):
            return True
        return False

    # Telegram service/system message fields that indicate non-user content
    SERVICE_MESSAGE_FIELDS = {
        "new_chat_members", "left_chat_member", "pinned_message",
        "new_chat_title", "new_chat_photo", "delete_chat_photo",
        "group_chat_created", "supergroup_chat_created", "channel_chat_created",
        "message_auto_delete_timer_changed",
        "forum_topic_created", "forum_topic_edited", "forum_topic_closed", "forum_topic_reopened",
        "general_forum_topic_hidden", "general_forum_topic_unhidden",
        "video_chat_started", "video_chat_ended", "video_chat_participants_invited",
        "web_app_data",
    }

    def classify_actor(self, msg: dict, account: dict | None = None) -> str:
        """
        Classify the sender of a Telegram message.

        Returns one of:
        - "customer": the conversation partner (triggers AI auto-reply)
        - "business_self": the Business account owner (via Telegram client)
        - "assistant_bot": the bot itself
        - "owner_operator": the owner (Kai) sending directly
        - "system": system/service messages
        - "unknown": cannot determine sender
        """
        if not isinstance(msg, dict):
            return "unknown"

        # 0. System/service messages (check before from.id)
        # Use 'in' to check key presence, not truthiness of value
        if any(field in msg for field in self.SERVICE_MESSAGE_FIELDS):
            return "system"

        sender = msg.get("from") or {}
        sender_id = sender.get("id")

        # 1. Bot itself
        if self.bot_id and sender_id == self.bot_id:
            return "assistant_bot"

        # 2. sender_business_bot flag (Telegram marks messages from business account owner)
        if msg.get("sender_business_bot"):
            return "business_self"

        # 3. Owner operator
        if sender_id is not None and str(sender_id) in set(Config.bot_owner_ids):
            return "owner_operator"

        # 4. Check if sender matches the business account identity
        if account and sender_id is not None:
            account_user_id = account.get("business_user_id")
            account_chat_id = account.get("user_chat_id")
            sender_id_str = str(sender_id)
            if account_user_id and sender_id_str == str(account_user_id):
                return "business_self"
            if account_chat_id and sender_id_str == str(account_chat_id):
                return "business_self"

        # 5. Regular customer
        if sender_id is not None:
            return "customer"

        # 6. Cannot determine
        return "unknown"

    def detect_message_type(self, msg: dict[str, Any]) -> str:
        if msg.get("text"):
            return "text"
        if msg.get("caption"):
            return "caption"
        for field in MEDIA_FIELDS:
            if field in msg:
                return field
        return "non_text"

    def decide_reply(self, chat_row, settings, account=None):
        if not as_bool(settings.get("global_enabled")):
            return False, "全局已暂停"
        if account and int(account.get("enabled") or 0) != 1:
            return False, "该 Business 账号已暂停"
        mode = chat_row.get("mode") or "default"
        if mode == "off":
            return False, "此聊天为 off"
        account_takeover = bool(account and int(account.get("full_takeover_enabled") or 0) == 1)
        global_takeover = as_bool(settings.get("full_takeover_enabled"))
        if (account_takeover or global_takeover) and int(chat_row.get("takeover_exempt") or 0) != 1:
            return True, "该账号已开启全面接管" if account_takeover else "全局全面接管已开启"
        if mode == "auto":
            return True, "此聊天为 auto"
        if mode == "manual":
            return False, "此聊天为 manual"
        default_mode = (account or {}).get("default_reply_mode") or settings.get("default_reply_mode", "manual")
        if mode == "default" and default_mode == "auto":
            return True, "聊天跟随账号默认模式 auto"
        return False, f"聊天模式={mode}，账号/全局默认模式={default_mode}"

    async def handle_business_message(self, msg):
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        bc_id = msg.get("business_connection_id")
        ck = db.conversation_key(bc_id, chat_id)
        message_id = msg.get("message_id")
        text = msg.get("text") or msg.get("caption")
        message_type = self.detect_message_type(msg)
        media_group_id = msg.get("media_group_id")
        account = db.get_account_by_connection(bc_id)
        account_name = (account or {}).get("account_name") or "未归属账号"
        db.upsert_chat(chat, db.now_iso(), bc_id, (account or {}).get("id"), (account or {}).get("business_user_id"), account_name)
        if account:
            db.update_account(account["id"], {"last_message_at": db.now_iso()})
        db.add_message(chat_id, message_id, "in", text, "user", ck, media_group_id, message_type, msg)
        db.log("INFO", "telegram", f"[{account_name}] 收到 Business 消息：chat_id={chat_id}，conversation_key={ck}，消息类型={message_type}，media_group_id={media_group_id or '-'}")
        if self.should_skip_self(msg):
            db.log("INFO", "decision", f"跳过回复：消息来自 Kai 本人、业务账号本人或 sender_business_bot，chat_id={chat_id}")
            return
        settings = db.resolve_account_settings(db.get_settings(), account)
        if media_group_id:
            await self.queue_media_group(ck, bc_id, chat_id, message_id, media_group_id, text, message_type, settings)
            return
        if text:
            await self.queue_text_or_reply(ck, bc_id, chat_id, message_id, text, settings)
            return
        await self.handle_non_text(ck, bc_id, chat_id, message_id, message_type, settings, grouped=False)

    async def queue_media_group(self, ck, bc_id, chat_id, message_id, media_group_id, text, message_type, settings):
        key = f"{ck}:media:{media_group_id}"
        bucket = self.pending_media.setdefault(key, {"items": [], "bc_id": bc_id, "chat_id": chat_id, "reply_to": message_id, "media_group_id": media_group_id})
        bucket["items"].append({"text": text, "message_type": message_type, "message_id": message_id})
        task = bucket.get("task")
        if task:
            task.cancel()
        bucket["task"] = asyncio.create_task(self._process_media_group_later(key, 2.0))
        db.log("INFO", "media", f"收到媒体组，等待合并处理，conversation_key={ck}，media_group_id={media_group_id}")

    async def _process_media_group_later(self, key, delay):
        try:
            await asyncio.sleep(delay)
            bucket = self.pending_media.pop(key, None)
            if not bucket:
                return
            items = bucket["items"]
            captions = [i["text"] for i in items if i.get("text")]
            ck = key.split(":media:", 1)[0]
            db.log("INFO", "media", f"收到媒体组，已合并处理，media_group_id={bucket['media_group_id']}，数量={len(items)}")
            settings = db.get_settings()
            if captions:
                await self.queue_text_or_reply(ck, bucket["bc_id"], bucket["chat_id"], bucket["reply_to"], "\n".join(captions), settings, force_now=False)
            else:
                await self.handle_non_text(ck, bucket["bc_id"], bucket["chat_id"], bucket["reply_to"], "media_group", settings, grouped=True)
        except asyncio.CancelledError:
            pass

    async def queue_text_or_reply(self, ck, bc_id, chat_id, message_id, text, settings, force_now=False):
        if not as_bool(settings.get("message_debounce_enabled")) or force_now:
            await self.process_text_batch(ck, bc_id, chat_id, message_id, [text])
            return
        key = ck
        now = time.monotonic()
        bucket = self.pending_text.setdefault(key, {"items": [], "first": now, "bc_id": bc_id, "chat_id": chat_id, "reply_to": message_id})
        bucket["items"].append({"text": text, "message_id": message_id})
        bucket["bc_id"] = bc_id
        bucket["chat_id"] = chat_id
        bucket["reply_to"] = message_id
        task = bucket.get("task")
        if task:
            task.cancel()
        debounce = as_float(settings.get("message_debounce_seconds"), 4.0)
        max_wait = as_float(settings.get("message_burst_max_wait_seconds"), 12.0)
        elapsed = now - bucket["first"]
        wait = max(0.0, min(debounce, max_wait - elapsed))
        bucket["task"] = asyncio.create_task(self._process_text_later(key, wait))
        db.log("INFO", "merge", f"进入消息合并等待：conversation_key={ck}，当前累计={len(bucket['items'])}，等待={wait:.1f}s")

    async def _process_text_later(self, key, delay):
        try:
            await asyncio.sleep(delay)
            bucket = self.pending_text.pop(key, None)
            if not bucket:
                return
            texts = [i["text"] for i in bucket["items"] if i.get("text")]
            db.log("INFO", "merge", f"合并 {len(texts)} 条消息后调用 AI：conversation_key={key}")
            await self.process_text_batch(key, bucket["bc_id"], bucket["chat_id"], bucket["reply_to"], texts)
        except asyncio.CancelledError:
            pass

    def merged_user_text(self, texts: list[str]) -> str:
        if len(texts) <= 1:
            return texts[0] if texts else ""
        lines = ["用户连续发送了以下消息："]
        lines.extend(f"{i}. {t}" for i, t in enumerate(texts, 1))
        return "\n".join(lines)

    async def process_text_batch(self, ck, bc_id, chat_id, reply_to, texts: list[str]):
        base_settings = db.get_settings()
        chat_row = db.get_chat(chat_id) or {"mode":"default", "takeover_exempt":0, "prompt_mode":"global", "chat_id":chat_id}
        account = db.get_account(chat_row.get("business_account_id")) if chat_row.get("business_account_id") else None
        settings = db.resolve_account_settings(base_settings, account)
        should_reply, reason = self.decide_reply(chat_row, settings, account)
        resolved = db.resolve_prompt(chat_row, settings)
        quote = as_bool(settings.get("quote_reply_enabled"))
        account_name = (account or {}).get("account_name") or chat_row.get("business_account_name") or "未归属账号"
        db.log("INFO", "decision", f"[{account_name}] 回复决策：chat_id={chat_id}，conversation_key={ck}，聊天模式={chat_row.get('mode')}，账号全面接管={(account or {}).get('full_takeover_enabled')}，全局全面接管={settings.get('full_takeover_enabled')}，引用回复={quote}，Prompt来源={resolved['source']}，人格={resolved.get('persona_name') or '-'}，模型={resolved['model']}，结果={'调用 DeepSeek' if should_reply else '跳过'}，原因={reason}")
        if not should_reply:
            return
        if self.in_auto_reply_cooldown(ck, chat_row, settings):
            db.log("INFO", "decision", f"跳过回复：auto_reply_cooldown_seconds 冷却中，conversation_key={ck}")
            return
        merged = self.merged_user_text(texts)
        try:
            reply = await self.generate_reply(chat_id, ck, settings, resolved, merged)
        except Exception as exc:
            db.log("ERROR", "deepseek", f"DeepSeek 调用失败：{exc}")
            reply = "AI 服务暂时不可用，请稍后再试。"
        reply = self.cleanup_repeated_intro(reply, chat_id, chat_row, settings)
        if as_bool(settings.get("output_filter_enabled")):
            filtered, triggered = filter_reply(reply, settings.get("fallback_safe_reply") or db.DEFAULT_SETTINGS["fallback_safe_reply"])
            if triggered:
                reply = filtered
                db.update_chat(chat_id, {"last_filter_triggered_at": db.now_iso()})
                db.log("WARNING", "filter", f"AI 输出触发安全过滤，已替换或清理为兜底回复，conversation_key={ck}")
        if reply:
            await self.send_reply(bc_id, chat_id, reply_to, reply, quote, ck, settings)

    def in_auto_reply_cooldown(self, ck, chat_row, settings) -> bool:
        cooldown = as_float(settings.get("auto_reply_cooldown_seconds"), 3.0)
        if cooldown <= 0:
            return False
        last = self.last_auto_reply.get(ck) or parse_iso(chat_row.get("last_auto_reply_at"))
        return bool(last and time.time() - last < cooldown)

    async def handle_non_text(self, ck, bc_id, chat_id, message_id, message_type, settings, grouped=False):
        mode = settings.get("media_handling_mode", "silent")
        db.log("INFO", "media", f"非文本消息处理：conversation_key={ck}，类型={message_type}，模式={mode}，grouped={grouped}")
        if mode == "silent":
            db.log("INFO", "media", f"非文本消息已静默记录，不触发 AI：conversation_key={ck}")
            return
        chat_row = db.get_chat(chat_id) or {}
        cooldown = as_float(settings.get("media_reply_cooldown_seconds"), 120.0)
        last = parse_iso(chat_row.get("last_media_reply_at"))
        if last and time.time() - last < cooldown:
            db.log("INFO", "media", f"非文本回复冷却中，跳过回复：conversation_key={ck}")
            return
        text = settings.get("non_text_reply") or db.DEFAULT_SETTINGS["non_text_reply"]
        if mode == "ask_caption":
            text = "我看到你发了内容，可以简单说下你想让我看什么吗？"
        quote = as_bool(settings.get("quote_reply_enabled"))
        await self.send_reply(bc_id, chat_id, message_id, text, quote, ck, settings, role_log_text="非文本提示")
        db.update_chat(chat_id, {"last_media_reply_at": db.now_iso()})

    async def generate_reply(self, chat_id, ck, settings, resolved, merged_text):
        limit = as_int(settings.get("max_history_messages"), 12)
        context = db.recent_context(chat_id, limit, ck)
        db.log("INFO", "prompt", f"上下文来源 conversation_key={ck}，加载历史消息 {len(context)} 条")
        messages = [{"role":"system", "content":resolved["prompt"]}]
        messages.extend(context)
        messages.append({"role":"user", "content":merged_text})
        return await asyncio.wait_for(self.deepseek.chat(messages, resolved["model"], resolved["temperature"], resolved["max_tokens"]), timeout=45)

    def cleanup_repeated_intro(self, reply: str, chat_id, chat_row, settings) -> str:
        if not as_bool(settings.get("avoid_repeated_intro")):
            return reply
        intro_pattern = r"^\s*(我是\s*Kai\s*的\s*AI\s*助手[，,。.；;：:]?\s*)"
        if not re.search(intro_pattern, reply or "", flags=re.I):
            return reply
        if chat_row.get("last_ai_intro_at"):
            cleaned = re.sub(intro_pattern, "", reply or "", count=1, flags=re.I).strip()
            db.log("INFO", "filter", f"已清理重复自我介绍：chat_id={chat_id}")
            return cleaned or reply
        db.update_chat(chat_id, {"last_ai_intro_at": db.now_iso()})
        return reply

    async def human_delay(self, chat_id, bc_id, text, settings):
        if not as_bool(settings.get("human_like_enabled")):
            return
        min_s = as_float(settings.get("reply_delay_min_seconds"), 2.0)
        max_s = as_float(settings.get("reply_delay_max_seconds"), 8.0)
        per_100 = as_float(settings.get("reply_delay_per_100_chars"), 1.2)
        extra = min(6.0, len(text or "") / 100.0 * per_100)
        delay = min(max_s + extra, max_s + 6.0)
        delay = random.uniform(min_s, max(min_s, delay))
        db.log("INFO", "telegram", f"人类化延迟 {delay:.1f} 秒后回复：chat_id={chat_id}")
        try:
            await self.telegram.send_chat_action(bc_id, chat_id, "typing")
        except Exception:
            pass
        await asyncio.sleep(delay)

    async def send_reply(self, bc_id, chat_id, reply_to, text, quote=False, ck=None, settings=None, role_log_text="自动回复"):
        settings = settings or db.get_settings()
        ck = ck or db.conversation_key(bc_id, chat_id)
        try:
            await self.human_delay(chat_id, bc_id, text, settings)
            sent = await self.telegram.send_message(bc_id, chat_id, text, reply_to, quote_reply=quote)
            db.add_message(chat_id, sent.get("message_id"), "out", text, "assistant", ck, message_type="text")
            self.last_auto_reply[ck] = time.time()
            db.update_chat(chat_id, {"last_auto_reply_at": db.now_iso()})
            db.log("INFO", "telegram", f"{role_log_text}发送成功：chat_id={chat_id} message={sent.get('message_id')} 引用回复={quote} conversation_key={ck}")
        except Exception as exc:
            db.log("ERROR", "telegram", f"发送 Telegram 回复失败：{exc}")

    # -----------------------------------------------------------------------
    # v2 Account Isolation Methods
    # -----------------------------------------------------------------------

    def conversation_key_v2(self, conversation_id: int) -> str:
        """Generate a cache key for v2 conversation debounce/cooldown."""
        return f"conv:{conversation_id}"

    def decide_reply_v2(self, conversation: dict, account: dict | None, settings: dict) -> tuple[bool, str]:
        """
        v2 reply decision based on conversation + account.

        Returns (should_reply: bool, reason: str).
        """
        # Global enabled check
        if not as_bool(settings.get("global_enabled")):
            return False, "全局已暂停"

        # Account enabled check
        if account and int(account.get("enabled") or 0) != 1:
            return False, "该 Business 账号已暂停"

        mode = conversation.get("mode") or "default"

        # Off mode
        if mode == "off":
            return False, "此对话为 off"

        # Takeover check
        account_takeover = bool(account and int(account.get("full_takeover_enabled") or 0) == 1)
        global_takeover = as_bool(settings.get("full_takeover_enabled"))
        if (account_takeover or global_takeover) and int(conversation.get("takeover_exempt") or 0) != 1:
            return True, "该账号已开启全面接管" if account_takeover else "全局全面接管已开启"

        # Explicit modes
        if mode == "auto":
            return True, "此对话为 auto"
        if mode == "manual":
            return False, "此对话为 manual"

        # Default mode: follow account or global default
        default_mode = (account or {}).get("default_reply_mode") or settings.get("default_reply_mode", "manual")
        if mode == "default" and default_mode == "auto":
            return True, "对话跟随账号默认模式 auto"

        return False, f"对话模式={mode}，账号/全局默认模式={default_mode}"

    def _conversation_to_chat_compat(self, conversation: dict) -> dict:
        """
        Convert v2 conversation dict to a chat-like dict compatible with
        legacy db.resolve_prompt() and other legacy functions.
        """
        return {
            "chat_id": conversation.get("peer_chat_id"),
            "id": conversation.get("peer_chat_id"),
            "mode": conversation.get("mode"),
            "prompt_mode": conversation.get("prompt_mode"),
            "prompt_persona_id": conversation.get("persona_id"),
            "custom_prompt": conversation.get("custom_prompt"),
            "custom_prompt_enabled": conversation.get("custom_prompt_enabled"),
            "takeover_exempt": conversation.get("takeover_exempt"),
            "title": conversation.get("peer_title"),
            "username": conversation.get("peer_username"),
            "first_name": conversation.get("peer_first_name"),
            "last_name": conversation.get("peer_last_name"),
            "last_ai_intro_at": conversation.get("last_ai_intro_at"),
        }

    def resolve_prompt_v2(self, conversation: dict, settings: dict) -> dict:
        """
        Resolve the prompt for a v2 conversation.
        Reuses legacy db.resolve_prompt() with a compatibility wrapper.
        """
        chat_compat = self._conversation_to_chat_compat(conversation)
        return db.resolve_prompt(chat_compat, settings)

    def cleanup_repeated_intro_v2(self, reply: str, conversation: dict, settings: str) -> str:
        """
        v2 version of cleanup_repeated_intro.
        Uses conversation dict instead of chat_row.
        """
        if not as_bool(settings.get("avoid_repeated_intro")):
            return reply
        intro_pattern = r"^\s*(我是\s*Kai\s*的\s*AI\s*助手[，,。.；;：:]?\s*)"
        if not re.search(intro_pattern, reply or "", flags=re.I):
            return reply
        conversation_id = conversation.get("id")
        if conversation.get("last_ai_intro_at"):
            cleaned = re.sub(intro_pattern, "", reply or "", count=1, flags=re.I).strip()
            db.log("INFO", "filter", f"已清理重复自我介绍：conversation_id={conversation_id}")
            return cleaned or reply
        db.update_conversation(conversation_id, {"last_ai_intro_at": db.now_iso()})
        return reply

    def in_auto_reply_cooldown_v2(self, conversation_id: int, conversation: dict, settings: dict) -> bool:
        """
        v2 cooldown check using conversation_id as key.
        """
        cooldown = as_float(settings.get("auto_reply_cooldown_seconds"), 3.0)
        if cooldown <= 0:
            return False
        ck = self.conversation_key_v2(conversation_id)
        last = self.last_auto_reply.get(ck) or parse_iso(conversation.get("last_auto_reply_at"))
        return bool(last and time.time() - last < cooldown)

    async def generate_reply_v2(self, conversation_id: int, settings: dict, resolved: dict, merged_text: str) -> str:
        """
        v2 reply generation using recent_context_v2.
        """
        limit = as_int(settings.get("max_history_messages"), 12)
        context = db.recent_context_v2(conversation_id, limit)
        db.log("INFO", "prompt", f"上下文来源 conversation_id={conversation_id}，加载历史消息 {len(context)} 条")
        messages = [{"role": "system", "content": resolved["prompt"]}]
        messages.extend(context)
        messages.append({"role": "user", "content": merged_text})
        return await asyncio.wait_for(
            self.deepseek.chat(messages, resolved["model"], resolved["temperature"], resolved["max_tokens"]),
            timeout=45
        )

    async def human_delay_v2(self, conversation_id: int, peer_chat_id, bc_id: str, text: str, settings: dict):
        """
        v2 human-like delay.
        """
        if not as_bool(settings.get("human_like_enabled")):
            return
        min_s = as_float(settings.get("reply_delay_min_seconds"), 2.0)
        max_s = as_float(settings.get("reply_delay_max_seconds"), 8.0)
        per_100 = as_float(settings.get("reply_delay_per_100_chars"), 1.2)
        extra = min(6.0, len(text or "") / 100.0 * per_100)
        delay = min(max_s + extra, max_s + 6.0)
        delay = random.uniform(min_s, max(min_s, delay))
        db.log("INFO", "telegram", f"人类化延迟 {delay:.1f} 秒后回复：conversation_id={conversation_id}")
        try:
            await self.telegram.send_chat_action(bc_id, peer_chat_id, "typing")
        except Exception:
            pass
        await asyncio.sleep(delay)

    async def send_reply_v2(
        self,
        business_connection_id: str,
        conversation_id: int,
        business_account_id: int,
        peer_chat_id,
        reply_to: int,
        text: str,
        quote: bool = False,
        settings: dict | None = None,
        role_log_text: str = "自动回复",
    ):
        """
        v2 send reply: sends via Telegram, records in messages_v2.
        """
        settings = settings or db.get_settings()
        ck = self.conversation_key_v2(conversation_id)
        try:
            await self.human_delay_v2(conversation_id, peer_chat_id, business_connection_id, text, settings)
            sent = await self.telegram.send_message(business_connection_id, peer_chat_id, text, reply_to, quote_reply=quote)
            # Record outbound in v2 messages
            db.add_message_v2(
                conversation_id, business_account_id,
                sent.get("message_id"), "out", "assistant_bot",
                text, "text", raw_json=sent,
            )
            # Update cooldown
            self.last_auto_reply[ck] = time.time()
            db.update_conversation(conversation_id, {"last_auto_reply_at": db.now_iso()})
            db.log("INFO", "telegram",
                f"{role_log_text}发送成功：conversation_id={conversation_id} "
                f"message={sent.get('message_id')} 引用回复={quote}")
        except Exception as exc:
            db.log("ERROR", "telegram", f"发送 Telegram 回复失败：{exc}")

    async def process_text_batch_v2(
        self,
        conversation_id: int,
        business_connection_id: str,
        business_account_id: int,
        peer_chat_id,
        reply_to: int,
        texts: list[str],
    ):
        """
        v2 text batch processing: decision + AI + send, all based on v2 conversation.
        """
        base_settings = db.get_settings()
        account = db.get_account(business_account_id)
        settings = db.resolve_account_settings(base_settings, account)
        conversation = db.get_conversation_by_id(conversation_id)
        if not conversation:
            db.log("ERROR", "decision", f"conversation_id={conversation_id} 不存在")
            return

        should_reply, reason = self.decide_reply_v2(conversation, account, settings)
        resolved = self.resolve_prompt_v2(conversation, settings)
        quote = as_bool(settings.get("quote_reply_enabled"))
        account_name = (account or {}).get("account_name") or "未归属账号"
        db.log("INFO", "decision",
            f"[{account_name}] 回复决策：conversation_id={conversation_id}，"
            f"对话模式={conversation.get('mode')}，"
            f"账号全面接管={(account or {}).get('full_takeover_enabled')}，"
            f"全局全面接管={settings.get('full_takeover_enabled')}，"
            f"引用回复={quote}，Prompt来源={resolved['source']}，"
            f"人格={resolved.get('persona_name') or '-'}，"
            f"模型={resolved['model']}，"
            f"结果={'调用 DeepSeek' if should_reply else '跳过'}，原因={reason}")

        if not should_reply:
            return

        if self.in_auto_reply_cooldown_v2(conversation_id, conversation, settings):
            db.log("INFO", "decision", f"跳过回复：auto_reply_cooldown_seconds 冷却中，conversation_id={conversation_id}")
            return

        merged = self.merged_user_text(texts)
        try:
            reply = await self.generate_reply_v2(conversation_id, settings, resolved, merged)
        except Exception as exc:
            db.log("ERROR", "deepseek", f"DeepSeek 调用失败：{exc}")
            reply = "AI 服务暂时不可用，请稍后再试。"

        reply = self.cleanup_repeated_intro_v2(reply, conversation, settings)

        if as_bool(settings.get("output_filter_enabled")):
            filtered, triggered = filter_reply(reply, settings.get("fallback_safe_reply") or db.DEFAULT_SETTINGS["fallback_safe_reply"])
            if triggered:
                reply = filtered
                db.update_conversation(conversation_id, {"last_filter_triggered_at": db.now_iso()})
                db.log("WARNING", "filter", f"AI 输出触发安全过滤，已替换或清理为兜底回复，conversation_id={conversation_id}")

        if reply:
            await self.send_reply_v2(
                business_connection_id, conversation_id, business_account_id,
                peer_chat_id, reply_to, reply, quote, settings,
            )

    async def queue_text_or_reply_v2(
        self,
        conversation_id: int,
        business_connection_id: str,
        business_account_id: int,
        peer_chat_id,
        message_id: int,
        text: str,
        settings: dict,
        force_now: bool = False,
    ):
        """
        v2 text debounce: queues text for merging before processing.
        """
        if not as_bool(settings.get("message_debounce_enabled")) or force_now:
            await self.process_text_batch_v2(
                conversation_id, business_connection_id, business_account_id,
                peer_chat_id, message_id, [text],
            )
            return

        key = self.conversation_key_v2(conversation_id)
        now = time.monotonic()
        bucket = self.pending_text.setdefault(key, {
            "items": [], "first": now,
            "bc_id": business_connection_id,
            "conversation_id": conversation_id,
            "account_id": business_account_id,
            "peer_chat_id": peer_chat_id,
            "reply_to": message_id,
        })
        bucket["items"].append({"text": text, "message_id": message_id})
        bucket["bc_id"] = business_connection_id
        bucket["conversation_id"] = conversation_id
        bucket["account_id"] = business_account_id
        bucket["peer_chat_id"] = peer_chat_id
        bucket["reply_to"] = message_id
        task = bucket.get("task")
        if task:
            task.cancel()
        debounce = as_float(settings.get("message_debounce_seconds"), 4.0)
        max_wait = as_float(settings.get("message_burst_max_wait_seconds"), 12.0)
        elapsed = now - bucket["first"]
        wait = max(0.0, min(debounce, max_wait - elapsed))
        bucket["task"] = asyncio.create_task(self._process_text_later_v2(key, wait))
        db.log("INFO", "merge",
            f"进入消息合并等待：conversation_id={conversation_id}，"
            f"当前累计={len(bucket['items'])}，等待={wait:.1f}s")

    async def _process_text_later_v2(self, key: str, delay: float):
        """
        v2 text debounce callback.
        """
        try:
            await asyncio.sleep(delay)
            bucket = self.pending_text.pop(key, None)
            if not bucket:
                return
            texts = [i["text"] for i in bucket["items"] if i.get("text")]
            db.log("INFO", "merge",
                f"合并 {len(texts)} 条消息后调用 AI：conversation_id={bucket['conversation_id']}")
            await self.process_text_batch_v2(
                bucket["conversation_id"], bucket["bc_id"], bucket["account_id"],
                bucket["peer_chat_id"], bucket["reply_to"], texts,
            )
        except asyncio.CancelledError:
            pass

    async def handle_business_message(self, msg):
        """
        Handle incoming Telegram Business messages using v2 conversation model.

        Flow:
        1. Resolve account by business_connection_id
        2. Create/get conversation by account_id + peer_chat_id
        3. Classify actor
        4. Record message in messages_v2
        5. If actor_type != customer: record only, no AI reply
        6. If customer + text: route to v2 reply path
        """
        chat = msg.get("chat") or {}
        peer_chat_id = chat.get("id")
        bc_id = msg.get("business_connection_id")

        if peer_chat_id is None or not bc_id:
            return

        # 1. Resolve account
        account = db.get_account_by_business_connection_id(bc_id)
        if not account:
            # Try to create from the connection data if available
            # This handles the case where we receive a message before connection update
            db.log("WARNING", "telegram",
                f"无法找到 business_connection_id={bc_id} 对应的账号，尝试创建")
            # We don't have connection data here, so we can't create account
            # Just log and return
            db.log("WARNING", "telegram",
                f"无法解析账号，跳过消息处理：peer_chat_id={peer_chat_id}")
            return

        account_id = account["id"]
        account_name = account.get("account_name") or account.get("business_user_id") or "未知"

        # 2. Create/get conversation
        conversation_id = db.create_conversation(
            account_id, peer_chat_id,
            chat.get("type") or "private",
            chat.get("title"), chat.get("username"),
            chat.get("first_name"), chat.get("last_name"),
        )
        db.update_conversation_peer(conversation_id, chat)

        # Update timestamps
        db.update_conversation(conversation_id, {"last_message_at": db.now_iso()})
        db.update_account(account_id, {"last_message_at": db.now_iso()})

        # 3. Classify actor
        actor_type = self.classify_actor(msg, account)

        # 4. Record message
        text = msg.get("text") or msg.get("caption")
        message_type = self.detect_message_type(msg)
        media_group_id = msg.get("media_group_id")

        db.add_message_v2(
            conversation_id, account_id,
            msg.get("message_id"), "in", actor_type,
            text, message_type, raw_json=msg,
        )

        db.log("INFO", "telegram",
            f"[{account_name}] 收到 Business 消息：conversation_id={conversation_id}，"
            f"actor_type={actor_type}，消息类型={message_type}，"
            f"media_group_id={media_group_id or '-'}")

        # 5. Non-customer messages: record only, no AI reply
        if actor_type != "customer":
            db.log("INFO", "decision",
                f"跳过回复：actor_type={actor_type}，不是 customer，"
                f"conversation_id={conversation_id}")
            return

        # 6. Customer message: resolve settings and route to reply path
        settings = db.resolve_account_settings(db.get_settings(), account)

        if media_group_id:
            # For now, use legacy media group handling but log warning
            # TODO: Phase 3B should implement v2 media group handling
            ck = self.conversation_key_v2(conversation_id)
            # Store v2 info in the bucket for later use
            await self._queue_media_group_v2(
                conversation_id, bc_id, account_id, peer_chat_id,
                msg.get("message_id"), media_group_id, text, message_type, settings,
            )
            return

        if text:
            await self.queue_text_or_reply_v2(
                conversation_id, bc_id, account_id, peer_chat_id,
                msg.get("message_id"), text, settings,
            )
            return

        # Non-text without media_group: log and skip for now
        db.log("INFO", "media",
            f"非文本消息已静默记录，不触发 AI：conversation_id={conversation_id}")

    async def _queue_media_group_v2(
        self,
        conversation_id: int,
        bc_id: str,
        account_id: int,
        peer_chat_id,
        message_id: int,
        media_group_id: str,
        text: str | None,
        message_type: str,
        settings: dict,
    ):
        """
        v2 media group handling (simplified for Phase 3A).
        """
        key = f"conv:{conversation_id}:media:{media_group_id}"
        bucket = self.pending_media.setdefault(key, {
            "items": [], "bc_id": bc_id,
            "conversation_id": conversation_id,
            "account_id": account_id,
            "peer_chat_id": peer_chat_id,
            "reply_to": message_id,
            "media_group_id": media_group_id,
        })
        bucket["items"].append({"text": text, "message_type": message_type, "message_id": message_id})
        task = bucket.get("task")
        if task:
            task.cancel()
        bucket["task"] = asyncio.create_task(self._process_media_group_later_v2(key, 2.0))
        db.log("INFO", "media",
            f"收到媒体组，等待合并处理，conversation_id={conversation_id}，"
            f"media_group_id={media_group_id}")

    async def _process_media_group_later_v2(self, key: str, delay: float):
        """
        v2 media group debounce callback.
        """
        try:
            await asyncio.sleep(delay)
            bucket = self.pending_media.pop(key, None)
            if not bucket:
                return
            items = bucket["items"]
            captions = [i["text"] for i in items if i.get("text")]
            db.log("INFO", "media",
                f"收到媒体组，已合并处理，media_group_id={bucket['media_group_id']}，"
                f"数量={len(items)}")
            settings = db.get_settings()
            if captions:
                await self.queue_text_or_reply_v2(
                    bucket["conversation_id"], bucket["bc_id"], bucket["account_id"],
                    bucket["peer_chat_id"], bucket["reply_to"],
                    "\n".join(captions), settings, force_now=False,
                )
            else:
                # Non-text media group: log and skip for now
                db.log("INFO", "media",
                    f"非文本媒体组已静默记录：conversation_id={bucket['conversation_id']}")
        except asyncio.CancelledError:
            pass
