# v2.0 多账号隔离大修设计文档

> 分支: v2.0-account-isolation
> 日期: 2026-06-04
> 状态: Bot 侧已完成，Web 侧待实施

---

## Implementation Status / 实施状态

### ✅ 已完成

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 0 | pytest 基础设施与 SQLite 备份脚本 | ✅ 完成 |
| Phase 1 | v2 schema、conversations/messages_v2 表、user_version=6、v2 db helpers | ✅ 完成 |
| Phase 2 | business_connection/account resolution helpers、actor classification | ✅ 完成 |
| Phase 3A | Business 文本消息主路径接入 v2 conversation/message 模型 | ✅ 完成 |
| Phase 3B | 上下文去重、真实 handle_business_message async 测试、媒体组账号级 settings | ✅ 完成 |
| Phase 3C | 测试描述清理、文档同步 | ✅ 完成 |

### ⏳ 未完成

- Web 路由仍主要基于旧 `/chats/{chat_id}`
- templates 尚未完全账号隔离
- legacy chats/messages 仍保留
- README 尚未完整更新 v2 运行说明
- 还没有 release/tag

### 关键说明

**Bot 侧文本自动回复主路径现在以 `business_account_id + peer_chat_id` 的 conversation 为隔离单位。**

Web 侧还没有完成账号隔离，因此不要把 v2 分支合并到 main 作为成品发布。

---

## 1. 当前问题确认

### 1.1 chats.chat_id PRIMARY KEY 是错误抽象

**现状**:
```sql
CREATE TABLE chats (
    chat_id INTEGER PRIMARY KEY,  -- ← 这是 Telegram 的私聊/群组 ID
    ...
);
```

**问题**: Telegram 的 `chat_id` 代表"对话对象"（用户 A、群组 G），不代表"谁在跟这个对象聊"。当 Business 账号 X 和 Y 同时与用户 A 聊天时，`chat_id` 相同，但这是两个完全独立的业务上下文。

**后果**: `ON CONFLICT(chat_id) DO UPDATE` 导致后写覆盖先写，两个账号的配置、状态、消息历史全部混淆。

### 1.2 多个 Telegram Business 账号下，同一个 peer chat_id 会互相覆盖

**场景**:
```
Business 账号 X (Kai 的主号) ←→ 用户 A (chat_id=1001)
Business 账号 Y (Kai 的副号) ←→ 用户 A (chat_id=1001)
```

两个账号的 chats 表记录会互相覆盖：
- `business_connection_id` 被覆盖为最新到达的
- `business_account_id` 被覆盖为最新到达的
- `mode`, `custom_prompt`, `prompt_persona_id` 等配置全部共享
- `last_auto_reply_at`, `last_ai_intro_at` 等状态互相影响

### 1.3 Web 端 /chats/{chat_id} 无法区分账号

**现状路由**: `/chats/1001` 只能查到一条记录，无法区分是账号 X 的聊天还是账号 Y 的聊天。

**后果**: 管理员无法为同一用户在不同账号下设置不同的回复模式、人格、提示词。

### 1.4 messages 虽然有 conversation_key，但仍然依赖 chat_id

**现状**:
```python
def list_messages(chat_id, limit=100, conversation_key_value=None):
    if conversation_key_value:
        rows = conn.execute("SELECT * FROM messages WHERE conversation_key=? ...")
    else:
        rows = conn.execute("SELECT * FROM messages WHERE chat_id=? ...")  # ← 回退到 chat_id
```

**问题**: 多数调用路径不传 `conversation_key_value`，导致回退到 `chat_id` 查询，可能查到其他账号的消息。

### 1.5 自己发的话、Bot 发的话、客户发的话没有被结构化建模

**现状**:
- `direction`: 'in' / 'out' — 只区分方向，不区分发送者
- `role`: 'user' / 'assistant' / 'system' — 语义模糊
- Owner 发的消息直接跳过不记录

**问题**: 无法回答"这条消息是谁发的"这个基本问题。

### 1.6 账号系统应该平起平坐，不应该存在主账号/从账号概念

**现状**: `settings` 表是全局的，`business_accounts` 表可以覆盖部分设置。这暗示 settings 是"主"，accounts 是"从"。

**正确模型**: 每个 Business 账号都是独立实体，有自己的配置、对话、上下文。全局设置只是"默认值模板"，不是"主账号"。

---

## 2. v2 核心原则

### 2.1 Business Account 是最高隔离层

```
business_account
    ├── account_settings (覆盖全局默认)
    ├── conversation_1 (与用户 A 的对话)
    │   ├── messages
    │   ├── mode, prompt_mode, persona_id
    │   └── last_auto_reply_at, last_ai_intro_at
    ├── conversation_2 (与用户 B 的对话)
    └── ...
```

### 2.2 所有账号平起平坐

- 没有"主账号"概念
- 全局 `settings` 表只是"默认值模板"，不是主配置
- 每个 account 可以独立覆盖任何设置项
- Web 控制台以 account 为单位组织，不是以全局配置为中心

### 2.3 Conversation 必须属于某一个 business_account_id

- 不存在"无主对话"
- 每个 conversation 必须有明确的 `business_account_id`
- 如果无法确定账号，宁可不创建 conversation，记录日志等待人工处理

### 2.4 不允许用 chat_id 作为全局唯一聊天主键

- `chat_id` 只是 Telegram 的对话对象标识
- 唯一标识一个业务对话需要 `(business_account_id, peer_chat_id)`

### 2.5 Conversation 唯一键必须是 business_account_id + peer_chat_id

```sql
UNIQUE(business_account_id, peer_chat_id)
```

这意味着：
- 同一个 `peer_chat_id` 在不同 account 下是不同的 conversation
- 同一个 account 对同一个 `peer_chat_id` 只有一个 conversation

### 2.6 Message 必须挂到 conversation_id

- 每条 message 必须有 `conversation_id` 外键
- 不再使用 `chat_id` 作为消息查询维度
- `conversation_key` 字段废弃，用 `conversation_id` 替代

### 2.7 只有 actor_type == customer 的消息可以触发 AI 自动回复

**actor_type 枚举**:
| actor_type | 说明 | 能否触发 AI |
|------------|------|-------------|
| `customer` | 对话对方（客户、朋友、群友） | ✅ 可以 |
| `business_self` | Business 账号本人（Kai 用手机发的） | ❌ 不可以 |
| `assistant_bot` | Bot 自己发的 | ❌ 不可以 |
| `owner_operator` | Owner 通过 Bot 发的（未来功能） | ❌ 不可以 |
| `system` | 系统消息 | ❌ 不可以 |
| `unknown` | 无法识别 | ❌ 不可以 |

### 2.8 business_self / assistant_bot / owner_operator / system / unknown 只能记录，不能触发 AI

- 所有消息都必须记录（包括 business_self）
- 但只有 `actor_type == customer` 的消息进入 AI 决策流程
- 这解决了"Owner 发的消息不记录"的问题

---

## 3. 新数据库设计

### 3.1 settings (全局默认值模板)

**不变**。保持现有结构，作为"默认值模板"。

```sql
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

**语义变更**: 这不是"主配置"，而是"当 account 没有覆盖时使用的默认值"。

### 3.2 business_accounts (Business 账号)

**保留现有结构，新增少量字段**。

```sql
CREATE TABLE business_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_user_id TEXT UNIQUE NOT NULL,      -- Telegram 用户 ID (账号唯一标识)
    user_chat_id TEXT,                           -- 与 Bot 的私聊 ID
    account_name TEXT,                           -- 显示名称
    account_username TEXT,                       -- Telegram username
    first_name TEXT,
    last_name TEXT,
    latest_business_connection_id TEXT,          -- 最新的 business_connection_id

    -- 账号级配置 (覆盖全局 settings)
    enabled INTEGER NOT NULL DEFAULT 1,
    full_takeover_enabled INTEGER NOT NULL DEFAULT 0,
    default_reply_mode TEXT NOT NULL DEFAULT 'manual',
    default_prompt_persona_id INTEGER,
    media_handling_mode TEXT,
    message_debounce_enabled INTEGER,
    human_like_enabled INTEGER,
    output_filter_enabled INTEGER,
    quote_reply_enabled INTEGER,

    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_message_at TEXT,

    -- v2 新增
    FOREIGN KEY (default_prompt_persona_id) REFERENCES prompt_personas(id) ON DELETE SET NULL
);
```

**变更说明**:
- 新增 `FOREIGN KEY` 约束
- 其余字段保留，不破坏现有数据

### 3.3 business_connections (Telegram Business 连接)

**不变**。

```sql
CREATE TABLE business_connections (
    business_connection_id TEXT PRIMARY KEY,
    user_id INTEGER,
    user_chat_id INTEGER,
    is_enabled INTEGER NOT NULL DEFAULT 0,
    rights_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 3.4 conversations (对话) — 新表，替代 chats

```sql
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 归属
    business_account_id INTEGER NOT NULL,

    -- 对话对象 (peer)
    peer_chat_id INTEGER NOT NULL,               -- Telegram 的 chat_id
    peer_type TEXT NOT NULL DEFAULT 'private',    -- private / group / supergroup / channel
    peer_title TEXT,                              -- 群组名称
    peer_username TEXT,                           -- @username
    peer_first_name TEXT,
    peer_last_name TEXT,

    -- 对话级配置
    mode TEXT NOT NULL DEFAULT 'default',         -- default / auto / manual / off
    prompt_mode TEXT NOT NULL DEFAULT 'global',   -- global / persona / custom / legacy
    persona_id INTEGER,
    custom_prompt TEXT,
    custom_prompt_enabled INTEGER NOT NULL DEFAULT 0,
    takeover_exempt INTEGER NOT NULL DEFAULT 0,
    note TEXT,

    -- 状态 (按 conversation 维度，不再按 chat_id)
    last_message_at TEXT,
    last_ai_intro_at TEXT,
    last_media_reply_at TEXT,
    last_filter_triggered_at TEXT,
    last_auto_reply_at TEXT,

    -- 时间
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    -- 约束
    UNIQUE(business_account_id, peer_chat_id),
    FOREIGN KEY (business_account_id) REFERENCES business_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (persona_id) REFERENCES prompt_personas(id) ON DELETE SET NULL
);

-- 索引
CREATE INDEX idx_conversations_account ON conversations(business_account_id);
CREATE INDEX idx_conversations_peer ON conversations(peer_chat_id);
CREATE INDEX idx_conversations_last_message ON conversations(business_account_id, last_message_at DESC);
```

**与旧 chats 表的对应关系**:
| 旧字段 | 新字段 | 说明 |
|--------|--------|------|
| chat_id | peer_chat_id | 重命名，不再是主键 |
| (无) | business_account_id | 新增，归属账号 |
| (无) | id | 新增，自增主键 |
| (无) | peer_type | 新增，对话类型 |
| title | peer_title | 重命名 |
| username | peer_username | 重命名 |
| first_name | peer_first_name | 重命名 |
| last_name | peer_last_name | 重命名 |
| business_connection_id | (移除) | 不再需要，通过 account 关联 |
| business_account_id | business_account_id | 保留 |
| business_user_id | (移除) | 不再需要，通过 account 关联 |
| business_account_name | (移除) | 不再需要，通过 account 关联 |
| account_override_mode | (移除) | 废弃 |
| last_ai_intro_at | last_ai_intro_at | 保留 |
| last_media_reply_at | last_media_reply_at | 保留 |
| last_filter_triggered_at | last_filter_triggered_at | 保留 |
| last_auto_reply_at | last_auto_reply_at | 保留 |

### 3.5 messages (消息)

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 归属
    conversation_id INTEGER NOT NULL,
    business_account_id INTEGER NOT NULL,         -- 冗余，方便查询

    -- Telegram 信息
    telegram_message_id INTEGER,                  -- Telegram 的 message_id

    -- 方向与角色
    direction TEXT NOT NULL,                      -- 'in' / 'out'
    actor_type TEXT NOT NULL DEFAULT 'unknown',   -- customer / business_self / assistant_bot / owner_operator / system / unknown

    -- 内容
    text TEXT,
    message_type TEXT NOT NULL DEFAULT 'text',    -- text / caption / photo / video / ...

    -- 媒体
    media_group_id TEXT,

    -- 原始数据
    raw_json TEXT,

    -- 时间
    created_at TEXT NOT NULL,

    -- 约束
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (business_account_id) REFERENCES business_accounts(id) ON DELETE CASCADE
);

-- 索引
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at DESC, id DESC);
CREATE INDEX idx_messages_account ON messages(business_account_id, created_at DESC);
CREATE INDEX idx_messages_media_group ON messages(media_group_id) WHERE media_group_id IS NOT NULL;
```

**与旧 messages 表的对应关系**:
| 旧字段 | 新字段 | 说明 |
|--------|--------|------|
| chat_id | (移除) | 不再使用 |
| conversation_key | conversation_id | 改为外键 |
| message_id | telegram_message_id | 重命名，避免歧义 |
| direction | direction | 保留 |
| role | actor_type | 重命名，语义更清晰 |
| text | text | 保留 |
| message_type | message_type | 保留 |
| media_group_id | media_group_id | 保留 |
| raw_json | raw_json | 保留 |
| (无) | business_account_id | 新增，冗余字段 |

**actor_type 取值**:
| 值 | 说明 | 来源判断 |
|----|------|----------|
| `customer` | 对话对方 | 不是 bot、不是 owner、不是 sender_business_bot |
| `business_self` | Business 账号本人 | `sender_business_bot` 为 true 且不是 Bot 自己 |
| `assistant_bot` | Bot 自己 | `from.id == self.bot_id` |
| `owner_operator` | Owner 操作 | `from.id in Config.bot_owner_ids` |
| `system` | 系统消息 | 系统生成的提示消息 |
| `unknown` | 无法识别 | 兜底 |

### 3.6 prompt_personas (提示词人格)

**不变**。保持全局共享。

```sql
CREATE TABLE prompt_personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    prompt TEXT NOT NULL,
    model TEXT,
    temperature REAL,
    max_tokens INTEGER,
    ai_tone TEXT,
    ai_reply_length TEXT,
    ai_safety_level TEXT,
    is_default INTEGER DEFAULT 0,
    is_builtin INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);
```

**设计决策**: persona 保持全局共享，不按账号隔离。原因：
1. persona 是"回复风格模板"，本身不包含账号信息
2. 同一个 persona 可以被多个 account/conversation 引用
3. 如果需要账号级 persona，通过 `conversations.persona_id` 或 `business_accounts.default_prompt_persona_id` 指定即可

### 3.7 logs (运行日志)

**不变**。

```sql
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### 3.8 废弃的表/字段

**废弃的表**:
- `chats` — 被 `conversations` 替代

**废弃的字段**:
- `messages.chat_id` — 被 `conversation_id` 替代
- `messages.conversation_key` — 被 `conversation_id` 替代
- `messages.role` — 被 `actor_type` 替代
- `chats.business_connection_id` — 通过 account 关联
- `chats.business_user_id` — 通过 account 关联
- `chats.business_account_name` — 通过 account 关联
- `chats.account_override_mode` — 废弃

**保留的兼容层** (Phase 6 清理):
- 旧 `chats` 表重命名为 `chats_legacy`，保留到确认迁移无误后删除
- 旧 `messages` 表结构保留，但新数据写入新表

---

## 4. Bot 流程设计

### 4.1 整体流程

```
Telegram Update
    │
    ├── business_connection
    │   └── upsert_business_connection()
    │       └── sync_business_account()
    │
    ├── business_message
    │   └── handle_business_message()
    │       ├── resolve_account()           → business_account
    │       ├── resolve_conversation()      → conversation
    │       ├── classify_actor()            → actor_type
    │       ├── record_message()            → 写入 messages 表
    │       ├── [if actor_type != customer] → 记录完毕，不回复
    │       ├── [if actor_type == customer]
    │       │   ├── decide_reply()          → 是否回复
    │       │   ├── generate_reply()        → 调用 AI
    │       │   ├── filter_reply()          → 安全过滤
    │       │   └── send_reply()            → 发送并记录
    │       └── [结束]
    │
    └── message (普通 Bot 消息)
        └── handle_bot_message()
            └── 记录到对应 conversation (如果能找到)
```

### 4.2 伪代码

```python
async def handle_business_message(msg: dict):
    """
    处理 Telegram Business 消息
    """
    # === 第一步：解析 business_connection_id ===
    bc_id = msg.get("business_connection_id")
    if not bc_id:
        log("WARNING", "收到 business_message 但没有 business_connection_id")
        return

    # === 第二步：解析 account ===
    account = resolve_account(bc_id)
    if not account:
        log("WARNING", f"无法找到 business_connection_id={bc_id} 对应的账号")
        return

    account_id = account["id"]
    account_name = account.get("account_name") or account.get("business_user_id") or "未知"

    # === 第三步：解析 conversation ===
    chat = msg.get("chat") or {}
    peer_chat_id = chat.get("id")
    if peer_chat_id is None:
        log("WARNING", f"[{account_name}] 消息没有 chat.id")
        return

    conversation = resolve_conversation(account_id, peer_chat_id, chat)

    # === 第四步：classify_actor ===
    actor_type = classify_actor(msg, account)

    # === 第五步：记录消息 ===
    text = msg.get("text") or msg.get("caption")
    message_type = detect_message_type(msg)
    media_group_id = msg.get("media_group_id")

    record_message(
        conversation_id=conversation["id"],
        business_account_id=account_id,
        telegram_message_id=msg.get("message_id"),
        direction="in",
        actor_type=actor_type,
        text=text,
        message_type=message_type,
        media_group_id=media_group_id,
        raw_json=msg,
    )

    # 更新 conversation 和 account 的 last_message_at
    update_conversation_timestamp(conversation["id"])
    update_account_timestamp(account_id)

    log("INFO", "telegram",
        f"[{account_name}] 收到消息：conversation_id={conversation['id']}，"
        f"actor_type={actor_type}，消息类型={message_type}")

    # === 第六步：只有 customer 才可能触发 AI ===
    if actor_type != "customer":
        log("INFO", "decision",
            f"跳过回复：actor_type={actor_type}，不是 customer，"
            f"conversation_id={conversation['id']}")
        return

    # === 第七步：消息去抖 ===
    if media_group_id:
        await queue_media_group(conversation, account, msg, text, message_type)
        return

    if text:
        await queue_text_or_reply(conversation, account, msg, text)
        return

    await handle_non_text(conversation, account, msg, message_type)


def resolve_account(bc_id: str) -> dict | None:
    """
    通过 business_connection_id 找到 business_account
    """
    return db.get_account_by_connection(bc_id)


def resolve_conversation(account_id: int, peer_chat_id: int, chat: dict) -> dict:
    """
    通过 account_id + peer_chat_id 找到或创建 conversation
    """
    conversation = db.get_conversation(account_id, peer_chat_id)
    if conversation:
        # 更新 peer 信息 (可能改名、改 username)
        db.update_conversation_peer(conversation["id"], chat)
        return conversation

    # 创建新 conversation
    conversation_id = db.create_conversation(
        business_account_id=account_id,
        peer_chat_id=peer_chat_id,
        peer_type=chat.get("type", "private"),
        peer_title=chat.get("title"),
        peer_username=chat.get("username"),
        peer_first_name=chat.get("first_name"),
        peer_last_name=chat.get("last_name"),
    )
    return db.get_conversation_by_id(conversation_id)


def classify_actor(msg: dict, account: dict) -> str:
    """
    判断消息发送者类型
    """
    sender = msg.get("from") or {}

    # Bot 自己发的
    if self.bot_id and sender.get("id") == self.bot_id:
        return "assistant_bot"

    # Owner 发的
    if str(sender.get("id") or "") in set(Config.bot_owner_ids):
        return "owner_operator"

    # Business 账号本人发的 (通过 Telegram Business 界面发的)
    if msg.get("sender_business_bot"):
        return "business_self"

    # 对话对方发的
    return "customer"


def record_message(
    conversation_id: int,
    business_account_id: int,
    telegram_message_id: int,
    direction: str,
    actor_type: str,
    text: str,
    message_type: str,
    media_group_id: str | None,
    raw_json: dict | None,
):
    """
    记录消息到数据库
    """
    db.add_message_v2(
        conversation_id=conversation_id,
        business_account_id=business_account_id,
        telegram_message_id=telegram_message_id,
        direction=direction,
        actor_type=actor_type,
        text=text,
        message_type=message_type,
        media_group_id=media_group_id,
        raw_json=raw_json,
    )


def decide_reply(conversation: dict, account: dict, settings: dict) -> tuple[bool, str]:
    """
    决定是否自动回复
    输入: conversation (已确认 actor_type == customer)
    """
    # 检查全局开关
    if not as_bool(settings.get("global_enabled")):
        return False, "全局已暂停"

    # 检查账号开关
    if int(account.get("enabled") or 0) != 1:
        return False, "该 Business 账号已暂停"

    # 检查 conversation 模式
    mode = conversation.get("mode") or "default"
    if mode == "off":
        return False, "此对话为 off"

    # 检查全面接管
    account_takeover = bool(int(account.get("full_takeover_enabled") or 0) == 1)
    global_takeover = as_bool(settings.get("full_takeover_enabled"))
    if (account_takeover or global_takeover) and int(conversation.get("takeover_exempt") or 0) != 1:
        return True, "该账号已开启全面接管" if account_takeover else "全局全面接管已开启"

    # 检查对话模式
    if mode == "auto":
        return True, "此对话为 auto"
    if mode == "manual":
        return False, "此对话为 manual"

    # 跟随账号默认模式
    default_mode = account.get("default_reply_mode") or settings.get("default_reply_mode", "manual")
    if mode == "default" and default_mode == "auto":
        return True, "对话跟随账号默认模式 auto"

    return False, f"对话模式={mode}，账号/全局默认模式={default_mode}"


async def generate_reply(conversation: dict, account: dict, settings: dict, merged_text: str) -> str:
    """
    调用 AI 生成回复
    """
    conversation_id = conversation["id"]

    # 获取历史上下文 (按 conversation_id)
    limit = as_int(settings.get("max_history_messages"), 12)
    context = db.recent_context_v2(conversation_id, limit)

    # 解析 prompt
    resolved = db.resolve_prompt_v2(conversation, settings, account)

    # 构建消息
    messages = [{"role": "system", "content": resolved["prompt"]}]
    messages.extend(context)
    messages.append({"role": "user", "content": merged_text})

    # 调用 DeepSeek
    reply = await self.deepseek.chat(
        messages, resolved["model"], resolved["temperature"], resolved["max_tokens"]
    )
    return reply


async def send_reply(conversation: dict, account: dict, reply_to: int, text: str, quote: bool, settings: dict):
    """
    发送回复并记录
    """
    bc_id = account.get("latest_business_connection_id")
    peer_chat_id = conversation["peer_chat_id"]
    conversation_id = conversation["id"]
    account_id = account["id"]

    # 人类化延迟
    await self.human_delay(conversation, text, settings)

    # 发送
    sent = await self.telegram.send_message(bc_id, peer_chat_id, text, reply_to, quote_reply=quote)

    # 记录 outbound message
    db.add_message_v2(
        conversation_id=conversation_id,
        business_account_id=account_id,
        telegram_message_id=sent.get("message_id"),
        direction="out",
        actor_type="assistant_bot",
        text=text,
        message_type="text",
        media_group_id=None,
        raw_json=sent,
    )

    # 更新状态
    db.update_conversation(conversation_id, {"last_auto_reply_at": db.now_iso()})
```

### 4.3 process_text_batch 改造

```python
async def process_text_batch(conversation: dict, account: dict, texts: list[str]):
    """
    处理一批文本消息 (去抖后)
    """
    settings = db.resolve_account_settings(db.get_settings(), account)
    should_reply, reason = self.decide_reply(conversation, account, settings)

    if not should_reply:
        log("INFO", "decision",
            f"[{account.get('account_name')}] 跳过回复：conversation_id={conversation['id']}，原因={reason}")
        return

    # 冷却检查
    if self.in_auto_reply_cooldown(conversation, settings):
        log("INFO", "decision",
            f"跳过回复：auto_reply_cooldown_seconds 冷却中，conversation_id={conversation['id']}")
        return

    merged = self.merged_user_text(texts)

    # 生成回复
    try:
        reply = await self.generate_reply(conversation, account, settings, merged)
    except Exception as exc:
        log("ERROR", "deepseek", f"DeepSeek 调用失败：{exc}")
        reply = "AI 服务暂时不可用，请稍后再试。"

    # 清理重复自我介绍
    reply = self.cleanup_repeated_intro(reply, conversation, settings)

    # 安全过滤
    if as_bool(settings.get("output_filter_enabled")):
        filtered, triggered = filter_reply(reply, settings.get("fallback_safe_reply") or db.DEFAULT_SETTINGS["fallback_safe_reply"])
        if triggered:
            reply = filtered
            db.update_conversation(conversation["id"], {"last_filter_triggered_at": db.now_iso()})

    # 发送
    if reply:
        quote = as_bool(settings.get("quote_reply_enabled"))
        await self.send_reply(conversation, account, texts[-1]["message_id"], reply, quote, settings)
```

### 4.4 handle_outbound_business_message (新增)

```python
async def handle_outbound_business_message(msg: dict):
    """
    处理 Business 账号本人发出的消息 (通过 Telegram 客户端发的)
    这些消息需要记录，但不触发 AI
    """
    bc_id = msg.get("business_connection_id")
    account = resolve_account(bc_id)
    if not account:
        return

    chat = msg.get("chat") or {}
    peer_chat_id = chat.get("id")
    conversation = resolve_conversation(account["id"], peer_chat_id, chat)

    text = msg.get("text") or msg.get("caption")
    message_type = detect_message_type(msg)

    record_message(
        conversation_id=conversation["id"],
        business_account_id=account["id"],
        telegram_message_id=msg.get("message_id"),
        direction="out",
        actor_type="business_self",
        text=text,
        message_type=message_type,
        media_group_id=msg.get("media_group_id"),
        raw_json=msg,
    )
```

---

## 5. Web 路由设计

### 5.1 新路由结构

| 路径 | 功能 | 模板 | 说明 |
|------|------|------|------|
| `/` | 总览仪表盘 | dashboard.html | 不变 |
| `/accounts` | Business 账号列表 | accounts.html | 不变 |
| `/accounts/{id}` | 账号详情/设置 | account_detail.html | 不变 |
| `/accounts/{id}/conversations` | 该账号的对话列表 | account_conversations.html | **新增** |
| `/accounts/{id}/conversations/{cid}` | 对话详情 | conversation_detail.html | **新增** |
| `/conversations/{cid}` | 对话详情 (快捷入口) | conversation_detail.html | **新增** |
| `/personas` | 提示词人格列表 | personas.html | 不变 |
| `/personas/{id}` | 人格编辑 | persona_form.html | 不变 |
| `/ai` | AI 全局设置 | ai.html | 不变 |
| `/connections` | Telegram 连接信息 | connections.html | 不变 |
| `/logs` | 运行日志 | logs.html | 不变 |
| `/system` | 系统设置 | system.html | 不变 |

### 5.2 废弃的路由

| 旧路径 | 处理方式 |
|--------|----------|
| `/chats` | **重定向** → `/accounts` (让用户先选账号) |
| `/chats/{chat_id}` | **重定向** → 查找 conversation 并跳转，或显示 404 |

### 5.3 redirect 策略

```python
@router.get("/chats")
async def old_chats_redirect():
    """旧路由兼容：重定向到账号列表"""
    return RedirectResponse("/accounts", 301)

@router.get("/chats/{chat_id}")
async def old_chat_detail_redirect(chat_id: int):
    """旧路由兼容：尝试找到对应的 conversation"""
    # 查找所有包含此 peer_chat_id 的 conversation
    conversations = db.find_conversations_by_peer_chat_id(chat_id)
    if len(conversations) == 1:
        return RedirectResponse(f"/conversations/{conversations[0]['id']}", 301)
    elif len(conversations) > 1:
        # 多个账号都有此对话，重定向到第一个账号的对话列表
        return RedirectResponse(f"/accounts/{conversations[0]['business_account_id']}/conversations", 301)
    else:
        raise HTTPException(404, "对话不存在")
```

### 5.4 新增 API 端点

| 路径 | 方法 | 功能 |
|------|------|------|
| `/api/conversations/{cid}/mode` | POST | 切换对话模式 |
| `/api/conversations/{cid}/forget` | POST | 清空对话上下文 |
| `/api/conversations/{cid}/export.txt` | GET | 导出 TXT |
| `/api/conversations/{cid}/export.json` | GET | 导出 JSON |

### 5.5 Web UI 如何体现账号平级

**导航栏变更**:
```
总览 | Business 账号 | 提示词人格 | AI 设置 | Telegram 连接 | 运行日志 | 系统设置
```

- 移除顶级 "聊天管理" 入口
- 聊天管理移到每个账号详情页内

**账号详情页扩展**:
```
/accounts/{id}
    ├── 账号概览 (状态、统计)
    ├── 账号设置 (配置覆盖)
    ├── 对话列表 (该账号下所有对话)
    └── 快速操作 (启用/暂停、全面接管)
```

**对话详情页**:
```
/accounts/{id}/conversations/{cid}
    ├── 对话概览 (peer 信息、所属账号)
    ├── 回复决策预览 (是否会回复、原因)
    ├── 对话设置 (mode、persona、custom_prompt)
    ├── 最终 Prompt 预览
    ├── 测试对话
    ├── 上下文管理 (清空、导出)
    └── 消息历史
```

### 5.6 全局设置和账号设置如何区分

**AI 设置页 (/ai)**:
- 标题改为 "AI 全局默认设置"
- 说明文字: "以下是所有 Business 账号的默认设置。单个账号可以覆盖这些设置。"
- 每个设置项旁标注: "可在账号设置中覆盖"

**账号设置页 (/accounts/{id})**:
- 显示当前账号的设置
- 对于已覆盖的设置，显示 "已覆盖全局默认"
- 对于未覆盖的设置，显示 "继承全局默认: {值}"

### 5.7 聊天列表如何按账号隔离

**方案**: 聊天列表不再作为独立顶级页面，而是嵌入到账号详情页中。

```
/accounts/{id}
    └── 对话列表
        ├── 筛选: 模式、人格、状态
        ├── 搜索: 昵称、username、备注
        └── 列表: 该账号下所有 conversation
```

**全局对话搜索** (可选，Phase 4+):
- 在 `/accounts` 页面顶部添加全局搜索框
- 搜索结果显示: 对话名称、所属账号、最近消息时间
- 点击跳转到 `/conversations/{cid}`

---

## 6. 迁移策略

### 6.1 PRAGMA user_version 升级计划

```
当前: user_version = 5
迁移后: user_version = 6
```

### 6.2 迁移步骤

```sql
-- Step 1: 备份旧表
ALTER TABLE chats RENAME TO chats_v5_legacy;
ALTER TABLE messages RENAME TO messages_v5_legacy;

-- Step 2: 创建新 conversations 表
CREATE TABLE conversations (...);  -- 如 3.4 定义

-- Step 3: 创建新 messages 表
CREATE TABLE messages (...);  -- 如 3.5 定义

-- Step 4: 迁移 chats 数据
-- 只迁移有明确 business_account_id 的记录
INSERT INTO conversations (
    business_account_id, peer_chat_id, peer_type, peer_title, peer_username,
    peer_first_name, peer_last_name, mode, prompt_mode, persona_id,
    custom_prompt, custom_prompt_enabled, takeover_exempt, note,
    last_message_at, last_ai_intro_at, last_media_reply_at,
    last_filter_triggered_at, last_auto_reply_at, created_at, updated_at
)
SELECT
    c.business_account_id,
    c.chat_id,
    'private',  -- 默认，后续可通过 raw_json 修正
    c.title,
    c.username,
    c.first_name,
    c.last_name,
    c.mode,
    c.prompt_mode,
    c.prompt_persona_id,
    c.custom_prompt,
    c.custom_prompt_enabled,
    c.takeover_exempt,
    c.note,
    c.last_message_at,
    c.last_ai_intro_at,
    c.last_media_reply_at,
    c.last_filter_triggered_at,
    c.last_auto_reply_at,
    c.created_at,
    c.updated_at
FROM chats_v5_legacy c
WHERE c.business_account_id IS NOT NULL;

-- Step 5: 迁移 messages 数据
-- 只迁移能匹配到 conversation 的消息
INSERT INTO messages (
    conversation_id, business_account_id, telegram_message_id,
    direction, actor_type, text, message_type, media_group_id,
    raw_json, created_at
)
SELECT
    conv.id,
    conv.business_account_id,
    m.message_id,
    m.direction,
    CASE
        WHEN m.role = 'user' THEN 'customer'
        WHEN m.role = 'assistant' THEN 'assistant_bot'
        WHEN m.role = 'system' THEN 'system'
        ELSE 'unknown'
    END,
    m.text,
    m.message_type,
    m.media_group_id,
    m.raw_json,
    m.created_at
FROM messages_v5_legacy m
JOIN conversations conv ON conv.peer_chat_id = m.chat_id
    AND conv.business_account_id = (
        SELECT c.business_account_id
        FROM chats_v5_legacy c
        WHERE c.chat_id = m.chat_id
        LIMIT 1
    )
WHERE m.conversation_key IS NOT NULL
   OR m.chat_id IN (SELECT peer_chat_id FROM conversations);

-- Step 6: 更新 PRAGMA user_version
PRAGMA user_version = 6;
```

### 6.3 如果旧 chat 没有 business_account_id，如何处理

**原则: 不猜测，不强行合并**

```sql
-- 对于没有 business_account_id 的 chats，保留在 legacy 表中
-- 不迁移到 conversations 表
-- 在 Web 控制台显示 "未归属对话 (legacy)" 提示

-- 统计未归属记录数
SELECT COUNT(*) FROM chats_v5_legacy WHERE business_account_id IS NULL;
```

**Web 控制台处理**:
- 在 `/accounts` 页面底部显示 "未归属对话" 区域
- 管理员可以手动将未归属对话分配给某个账号
- 或者选择删除这些历史数据

### 6.4 如何避免乱合并

**规则**:
1. 一个 `chat_id` 在不同 `business_account_id` 下生成不同的 conversation
2. 迁移时严格按 `(business_account_id, chat_id)` 去重
3. 如果同一个 `chat_id` 在多个 account 下都有记录，分别创建 conversation
4. 不做跨账号的消息合并

**验证 SQL**:
```sql
-- 检查是否有 chat_id 在多个 account 下出现
SELECT chat_id, COUNT(DISTINCT business_account_id) as account_count
FROM chats_v5_legacy
WHERE business_account_id IS NOT NULL
GROUP BY chat_id
HAVING account_count > 1;

-- 对于这些 chat_id，确保创建了多个 conversation
SELECT peer_chat_id, COUNT(*) as conversation_count
FROM conversations
GROUP BY peer_chat_id
HAVING conversation_count > 1;
```

### 6.5 如何保留旧数据备份

**方案 A: 重命名旧表 (推荐)**
```sql
ALTER TABLE chats RENAME TO chats_v5_legacy;
ALTER TABLE messages RENAME TO messages_v5_legacy;
```
- 旧表保留在数据库中，不删除
- 确认迁移无误后，Phase 6 再删除

**方案 B: 导出备份文件**
```bash
sqlite3 data.sqlite3 ".backup data_v5_backup.sqlite3"
```
- 在迁移前创建完整备份
- 保留到项目稳定运行 1 周后

### 6.6 是否保留 legacy 表

**保留到 Phase 6**:
- `chats_v5_legacy` 和 `messages_v5_legacy` 保留
- Phase 6 确认所有功能正常后，删除 legacy 表
- 删除前创建 `.sqlite3` 备份文件

### 6.7 如何回滚

**回滚步骤**:
```sql
-- 1. 删除新表
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS messages;

-- 2. 恢复旧表名
ALTER TABLE chats_v5_legacy RENAME TO chats;
ALTER TABLE messages_v5_legacy RENAME TO messages;

-- 3. 恢复 PRAGMA user_version
PRAGMA user_version = 5;
```

**代码回滚**:
```bash
git checkout main
```

---

## 7. 测试计划

### 7.1 测试基础设施

```python
# tests/conftest.py
import pytest
import sqlite3
import db

@pytest.fixture
def test_db(tmp_path):
    """创建临时测试数据库"""
    db_path = tmp_path / "test.sqlite3"
    # 重写 DB_PATH 指向临时数据库
    original = db.DB_PATH
    db.DB_PATH = db_path
    db.init_db()
    yield db_path
    db.DB_PATH = original

@pytest.fixture
def sample_accounts(test_db):
    """创建示例 Business 账号"""
    with db.connect() as conn:
        conn.execute("""
            INSERT INTO business_accounts (business_user_id, account_name, enabled, default_reply_mode, created_at, updated_at)
            VALUES ('1001', '账号 X', 1, 'auto', datetime('now'), datetime('now'))
        """)
        conn.execute("""
            INSERT INTO business_accounts (business_user_id, account_name, enabled, default_reply_mode, created_at, updated_at)
            VALUES ('1002', '账号 Y', 1, 'manual', datetime('now'), datetime('now'))
        """)
    return {'x': 1, 'y': 2}
```

### 7.2 必须新增的 pytest 测试

#### 测试 1: 同一个 peer_chat_id 在两个 business_account_id 下生成两个 conversation

```python
def test_same_peer_different_accounts(test_db, sample_accounts):
    """同一个 peer_chat_id 在不同账号下应该创建独立的 conversation"""
    account_x = sample_accounts['x']
    account_y = sample_accounts['y']
    peer_chat_id = 5001

    # 账号 X 创建 conversation
    conv_x = db.create_conversation(account_x, peer_chat_id, 'private', '用户 A')
    assert conv_x is not None

    # 账号 Y 创建 conversation (同一个 peer_chat_id)
    conv_y = db.create_conversation(account_y, peer_chat_id, 'private', '用户 A')
    assert conv_y is not None

    # 两个 conversation 应该是不同的
    assert conv_x != conv_y

    # 验证
    fetched_x = db.get_conversation(account_x, peer_chat_id)
    fetched_y = db.get_conversation(account_y, peer_chat_id)
    assert fetched_x['id'] != fetched_y['id']
    assert fetched_x['business_account_id'] == account_x
    assert fetched_y['business_account_id'] == account_y
```

#### 测试 2: 账号 A 的消息不会进入账号 B 的上下文

```python
def test_message_isolation(test_db, sample_accounts):
    """账号 A 的消息不会出现在账号 B 的上下文中"""
    account_x = sample_accounts['x']
    account_y = sample_accounts['y']
    peer_chat_id = 5001

    conv_x = db.create_conversation(account_x, peer_chat_id, 'private', '用户 A')
    conv_y = db.create_conversation(account_y, peer_chat_id, 'private', '用户 A')

    # 账号 X 添加消息
    db.add_message_v2(conv_x, account_x, 1, 'in', 'customer', '你好 X', 'text')

    # 账号 Y 添加消息
    db.add_message_v2(conv_y, account_y, 2, 'in', 'customer', '你好 Y', 'text')

    # 查询账号 X 的上下文
    ctx_x = db.recent_context_v2(conv_x, 10)
    assert len(ctx_x) == 1
    assert ctx_x[0]['content'] == '你好 X'

    # 查询账号 Y 的上下文
    ctx_y = db.recent_context_v2(conv_y, 10)
    assert len(ctx_y) == 1
    assert ctx_y[0]['content'] == '你好 Y'
```

#### 测试 3: business_self 消息不会触发 AI

```python
def test_business_self_no_trigger(test_db, sample_accounts):
    """business_self 消息不触发 AI 回复"""
    # classify_actor 应该返回 'business_self'
    msg = {
        "from": {"id": 9999},
        "sender_business_bot": True,
        "text": "我发的消息"
    }
    account = {"business_user_id": "1001"}
    actor_type = classify_actor(msg, account)
    assert actor_type == "business_self"

    # decide_reply 不应该被调用 (因为 actor_type != customer)
    # 这个逻辑在 handle_business_message 中控制
```

#### 测试 4: assistant_bot 消息不会触发 AI

```python
def test_assistant_bot_no_trigger(test_db):
    """assistant_bot 消息不触发 AI 回复"""
    msg = {
        "from": {"id": 12345},  # 假设 bot_id = 12345
        "text": "Bot 发的消息"
    }
    # 需要 mock self.bot_id = 12345
    actor_type = classify_actor(msg, account)
    assert actor_type == "assistant_bot"
```

#### 测试 5: owner_operator 消息不会触发 AI

```python
def test_owner_operator_no_trigger(test_db):
    """owner_operator 消息不触发 AI 回复"""
    msg = {
        "from": {"id": 100},  # 假设 owner_id = 100
        "text": "Owner 发的消息"
    }
    # 需要 mock Config.bot_owner_ids = ["100"]
    actor_type = classify_actor(msg, account)
    assert actor_type == "owner_operator"
```

#### 测试 6: customer 消息在 auto 模式下才触发 AI

```python
def test_customer_auto_mode_triggers(test_db, sample_accounts):
    """customer 消息在 auto 模式下触发 AI"""
    account = sample_accounts['x']
    peer_chat_id = 5001

    conv = db.create_conversation(account, peer_chat_id, 'private', '用户 A')
    db.update_conversation(conv, {'mode': 'auto'})

    settings = db.get_settings()
    account_data = db.get_account(account)
    conv_data = db.get_conversation_by_id(conv)

    should_reply, reason = decide_reply(conv_data, account_data, settings)
    assert should_reply is True
    assert "auto" in reason
```

#### 测试 7: manual/off/default/auto 模式决策正确

```python
def test_mode_decisions(test_db, sample_accounts):
    """各种模式下的回复决策正确"""
    account = sample_accounts['x']
    settings = db.get_settings()
    account_data = db.get_account(account)

    # auto 模式
    conv = db.create_conversation(account, 5001, 'private', 'A')
    db.update_conversation(conv, {'mode': 'auto'})
    conv_data = db.get_conversation_by_id(conv)
    should_reply, _ = decide_reply(conv_data, account_data, settings)
    assert should_reply is True

    # manual 模式
    db.update_conversation(conv, {'mode': 'manual'})
    conv_data = db.get_conversation_by_id(conv)
    should_reply, _ = decide_reply(conv_data, account_data, settings)
    assert should_reply is False

    # off 模式
    db.update_conversation(conv, {'mode': 'off'})
    conv_data = db.get_conversation_by_id(conv)
    should_reply, _ = decide_reply(conv_data, account_data, settings)
    assert should_reply is False

    # default 模式 (跟随账号默认)
    db.update_conversation(conv, {'mode': 'default'})
    conv_data = db.get_conversation_by_id(conv)
    should_reply, reason = decide_reply(conv_data, account_data, settings)
    # 账号 X 默认是 auto
    assert should_reply is True
    assert "账号默认模式" in reason
```

#### 测试 8: 账号 A 的默认模式不影响账号 B

```python
def test_account_mode_isolation(test_db, sample_accounts):
    """账号 A 的默认模式不影响账号 B"""
    settings = db.get_settings()

    # 账号 X 默认是 auto
    account_x = db.get_account(sample_accounts['x'])
    conv_x = db.create_conversation(sample_accounts['x'], 5001, 'private', 'A')
    db.update_conversation(conv_x, {'mode': 'default'})
    conv_x_data = db.get_conversation_by_id(conv_x)
    should_reply_x, _ = decide_reply(conv_x_data, account_x, settings)
    assert should_reply_x is True

    # 账号 Y 默认是 manual
    account_y = db.get_account(sample_accounts['y'])
    conv_y = db.create_conversation(sample_accounts['y'], 5001, 'private', 'A')
    db.update_conversation(conv_y, {'mode': 'default'})
    conv_y_data = db.get_conversation_by_id(conv_y)
    should_reply_y, _ = decide_reply(conv_y_data, account_y, settings)
    assert should_reply_y is False
```

#### 测试 9: 删除/忘记上下文只影响当前 conversation

```python
def test_forget_only_affects_current(test_db, sample_accounts):
    """清空上下文只影响当前 conversation"""
    account_x = sample_accounts['x']
    account_y = sample_accounts['y']

    conv_x = db.create_conversation(account_x, 5001, 'private', 'A')
    conv_y = db.create_conversation(account_y, 5001, 'private', 'A')

    # 两个 conversation 都添加消息
    db.add_message_v2(conv_x, account_x, 1, 'in', 'customer', '消息 X', 'text')
    db.add_message_v2(conv_y, account_y, 2, 'in', 'customer', '消息 Y', 'text')

    # 清空账号 X 的上下文
    db.forget_conversation(conv_x)

    # 账号 X 的上下文应该为空
    ctx_x = db.recent_context_v2(conv_x, 10)
    assert len(ctx_x) == 0

    # 账号 Y 的上下文应该不受影响
    ctx_y = db.recent_context_v2(conv_y, 10)
    assert len(ctx_y) == 1
    assert ctx_y[0]['content'] == '消息 Y'
```

#### 测试 10: Web 查询 conversation 时不会跨账号

```python
def test_web_no_cross_account(test_db, sample_accounts):
    """Web 查询不会跨账号"""
    account_x = sample_accounts['x']
    account_y = sample_accounts['y']

    conv_x = db.create_conversation(account_x, 5001, 'private', 'A')
    conv_y = db.create_conversation(account_y, 5001, 'private', 'A')

    # 查询账号 X 的对话列表
    convs_x = db.list_conversations(account_x)
    assert len(convs_x) == 1
    assert convs_x[0]['id'] == conv_x

    # 查询账号 Y 的对话列表
    convs_y = db.list_conversations(account_y)
    assert len(convs_y) == 1
    assert convs_y[0]['id'] == conv_y
```

### 7.3 测试文件结构

```
tests/
├── conftest.py                    # 测试基础设施
├── test_conversation_isolation.py # 对话隔离测试 (测试 1, 2, 9, 10)
├── test_actor_classification.py   # 消息分类测试 (测试 3, 4, 5)
├── test_reply_decision.py         # 回复决策测试 (测试 6, 7, 8)
└── test_migration.py              # 迁移测试
```

---

## 8. 分阶段实施计划

### Phase 0: 安全备份与测试骨架

**目标**: 创建安全网，确保后续改动可验证、可回滚。

**修改文件**:
- `tests/conftest.py` — 新建
- `tests/test_conversation_isolation.py` — 新建
- `tests/test_actor_classification.py` — 新建
- `tests/test_reply_decision.py` — 新建
- `tests/test_migration.py` — 新建
- `pytest.ini` 或 `pyproject.toml` — 新建 (测试配置)

**风险**: 无。只新增文件，不修改业务代码。

**验收标准**:
- [ ] `pytest` 可以运行（即使测试全部 skip）
- [ ] 测试骨架文件存在且语法正确
- [ ] 备份脚本可以运行

---

### Phase 1: 新增 v2 schema 与 migration

**目标**: 在数据库中创建新表，迁移现有数据。

**修改文件**:
- `db.py` — 新增 `init_db_v2()` 和 `migrate_v5_to_v6()`
- `db.py` — 新增 `CONVERSATIONS_TABLE_DDL` 和 `MESSAGES_V2_TABLE_DDL`

**风险**:
- 迁移脚本可能遗漏边界情况
- 大量数据迁移可能耗时

**验收标准**:
- [ ] `init_db_v2()` 创建 conversations 和 messages_v2 表
- [ ] `migrate_v5_to_v6()` 迁移有 business_account_id 的 chats
- [ ] 迁移后 PRAGMA user_version = 6
- [ ] 旧表重命名为 `*_v5_legacy`
- [ ] 迁移后查询 conversations 表数据正确
- [ ] 迁移后查询 messages_v2 表数据正确
- [ ] `test_migration.py` 测试通过

---

### Phase 2: 新增 v2 db helper，不删除旧 helper

**目标**: 实现新的数据库操作函数，与旧函数并存。

**修改文件**:
- `db.py` — 新增以下函数:
  - `create_conversation()`
  - `get_conversation()`
  - `get_conversation_by_id()`
  - `list_conversations()`
  - `update_conversation()`
  - `update_conversation_peer()`
  - `forget_conversation()`
  - `find_conversations_by_peer_chat_id()`
  - `add_message_v2()`
  - `recent_context_v2()`
  - `list_messages_v2()`
  - `resolve_prompt_v2()`

**风险**:
- 新旧函数可能有微妙的行为差异
- 需要确保新函数使用正确的表名

**验收标准**:
- [ ] 所有新函数有对应的单元测试
- [ ] 新函数使用 conversations 和 messages_v2 表
- [ ] 旧函数保持不变，不影响现有功能
- [ ] `test_conversation_isolation.py` 测试通过

---

### Phase 3: 改 bot.py 使用 v2 conversation/message 模型

**目标**: 修改消息处理流程，使用新的 conversation 模型。

**修改文件**:
- `bot.py` — 修改以下函数:
  - `handle_business_message()` — 使用新流程
  - `process_text_batch()` — 使用 conversation 而非 chat_id
  - `decide_reply()` — 使用 conversation + account
  - `generate_reply()` — 使用 conversation_id 查询上下文
  - `cleanup_repeated_intro()` — 使用 conversation_id
  - `send_reply()` — 使用 conversation
  - `human_delay()` — 使用 conversation
- `bot.py` — 新增:
  - `classify_actor()`
  - `resolve_conversation()`
  - `handle_outbound_business_message()`

**风险**:
- 消息处理逻辑改动较大，可能引入 bug
- 需要仔细处理 actor_type 分类
- 需要确保 outbound 消息也正确记录

**验收标准**:
- [ ] `test_actor_classification.py` 测试通过
- [ ] `test_reply_decision.py` 测试通过
- [ ] 新消息写入 messages_v2 表
- [ ] 新对话写入 conversations 表
- [ ] business_self 消息被记录但不触发 AI
- [ ] assistant_bot 消息被记录但不触发 AI
- [ ] owner_operator 消息被记录但不触发 AI
- [ ] 只有 customer 消息在 auto 模式下触发 AI

---

### Phase 4: 改 web.py 与 templates 使用账号隔离路由

**目标**: Web 控制台支持账号隔离的对话管理。

**修改文件**:
- `web.py` — 新增/修改:
  - `/accounts/{id}/conversations` — 对话列表
  - `/accounts/{id}/conversations/{cid}` — 对话详情
  - `/conversations/{cid}` — 快捷入口
  - `/chats` — 重定向
  - `/chats/{chat_id}` — 重定向
  - `/api/conversations/{cid}/mode` — 切换模式
  - `/api/conversations/{cid}/forget` — 清空上下文
- `templates/account_conversations.html` — 新建
- `templates/conversation_detail.html` — 新建 (或重命名 chat_detail.html)
- `templates/accounts.html` — 修改 (添加对话列表链接)
- `templates/account_detail.html` — 修改 (嵌入对话列表)
- `templates/base.html` — 修改 (移除 "聊天管理" 导航)
- `templates/dashboard.html` — 修改 (对话统计)

**风险**:
- 模板改动可能影响现有 UI
- 旧路由的 redirect 需要正确处理

**验收标准**:
- [ ] `/accounts/{id}/conversations` 正确显示该账号的对话列表
- [ ] `/accounts/{id}/conversations/{cid}` 正确显示对话详情
- [ ] `/conversations/{cid}` 可以作为快捷入口
- [ ] `/chats` 重定向到 `/accounts`
- [ ] `/chats/{chat_id}` 重定向到正确的 conversation
- [ ] 对话详情页可以修改模式、清空上下文
- [ ] 导航栏不再显示 "聊天管理"
- [ ] `test_web_no_cross_account` 测试通过 (如果实现了 Web 测试)

---

### Phase 5: 补测试

**目标**: 补充集成测试和边界测试。

**修改文件**:
- `tests/test_integration.py` — 新建 (端到端测试)
- `tests/test_edge_cases.py` — 新建 (边界情况)
- `tests/test_migration.py` — 补充

**风险**: 无。只新增测试文件。

**验收标准**:
- [ ] 所有测试通过
- [ ] 测试覆盖主要业务流程
- [ ] 测试覆盖边界情况 (NULL 值、空字符串、重复数据等)

---

### Phase 6: 清理 legacy 兼容层

**目标**: 删除旧代码和旧表。

**修改文件**:
- `db.py` — 删除旧函数:
  - `upsert_chat()` — 废弃
  - `get_chat()` — 废弃
  - `list_chats()` — 废弃
  - `set_chat_mode()` — 废弃
  - `update_chat()` — 废弃
  - `add_message()` — 废弃 (被 add_message_v2 替代)
  - `list_messages()` — 废弃 (被 list_messages_v2 替代)
  - `recent_context()` — 废弃 (被 recent_context_v2 替代)
  - `forget_chat()` — 废弃 (被 forget_conversation 替代)
  - `conversation_key()` — 废弃
- `db.py` — 删除旧表:
  - `DROP TABLE IF EXISTS chats_v5_legacy`
  - `DROP TABLE IF EXISTS messages_v5_legacy`
- `web.py` — 删除旧路由:
  - `/chats` 重定向保留，但代码简化
  - `/chats/{chat_id}` 重定向保留，但代码简化

**风险**:
- 删除旧代码可能遗漏依赖
- 需要仔细检查所有引用

**验收标准**:
- [ ] 旧函数全部删除或标记为 deprecated
- [ ] 旧表删除
- [ ] 所有测试通过
- [ ] 应用正常启动和运行
- [ ] 创建 `.sqlite3` 备份文件

---

### Phase 7: README 更新

**目标**: 更新项目文档。

**修改文件**:
- `README.md` — 更新:
  - 项目描述
  - 架构说明
  - 安装/运行说明
  - 多账号使用说明
  - API 文档

**风险**: 无。

**验收标准**:
- [ ] README 准确反映当前架构
- [ ] 多账号使用说明清晰
- [ ] 无过时信息

---

## 9. 附录

### 9.1 关键设计决策记录

| 决策 | 选择 | 原因 |
|------|------|------|
| conversation 主键 | 自增 ID + UNIQUE 约束 | 自增 ID 便于外键引用，UNIQUE 确保业务唯一性 |
| conversation_key 是否保留 | 不保留 | 用 conversation_id 替代，更简洁 |
| messages 是否冗余 business_account_id | 是 | 方便按账号查询，避免 JOIN |
| persona 是否按账号隔离 | 否 | 保持全局共享，通过引用实现灵活性 |
| settings 是否重构 | 否 | 保持现有结构，作为默认值模板 |
| actor_type 枚举 | 6 个值 | 覆盖所有发送者类型，语义清晰 |

### 9.2 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 迁移丢失数据 | 高 | 迁移前备份，保留 legacy 表 |
| 新旧代码并存导致 bug | 中 | 充分测试，Phase 3 改动时仔细验证 |
| Web 路由变更影响用户 | 低 | 使用 301 重定向，保留旧路由兼容 |
| 性能下降 | 低 | conversations 表比 chats 表多一个字段，影响可忽略 |

### 9.3 时间估算

| 阶段 | 预估时间 | 说明 |
|------|----------|------|
| Phase 0 | 1-2 小时 | 测试骨架 |
| Phase 1 | 2-3 小时 | Schema 迁移 |
| Phase 2 | 3-4 小时 | db helper |
| Phase 3 | 4-6 小时 | bot.py 改造 |
| Phase 4 | 4-6 小时 | Web 改造 |
| Phase 5 | 2-3 小时 | 补测试 |
| Phase 6 | 1-2 小时 | 清理 |
| Phase 7 | 1 小时 | 文档 |
| **总计** | **18-27 小时** | |
