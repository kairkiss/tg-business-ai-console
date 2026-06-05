# v2 Runtime Status

> 分支: v2.0-account-isolation
> 更新: 2026-06-05

---

## Current Status

### Data Layer

| 组件 | 状态 |
|------|------|
| user_version | 6 |
| conversations 表 | ✅ 已创建 |
| messages_v2 表 | ✅ 已创建 |
| messages 按 conversation_id 隔离 | ✅ |
| conversations 唯一约束 (business_account_id + peer_chat_id) | ✅ |
| legacy chats/messages 保留 | ✅ 兼容 |

### Bot Text Path (v2)

| 步骤 | 状态 |
|------|------|
| 通过 business_connection_id 解析 account | ✅ |
| 通过 account_id + peer_chat_id 创建/加载 conversation | ✅ |
| classify_actor() 分类消息发送者 | ✅ |
| 将 inbound 消息记录到 messages_v2 | ✅ |
| 非 customer actor 不触发 AI | ✅ |
| 使用 recent_context_v2() 获取上下文 | ✅ |
| 排除当前批次消息避免重复 (exclude_message_ids) | ✅ |
| 将 outbound assistant_bot 记录到同一个 conversation_id | ✅ |
| 媒体组使用账号级 settings | ✅ |
| 消息去抖使用 conversation_id 维度 | ✅ |

### Account Resolution

| 函数 | 状态 |
|------|------|
| get_account_by_business_connection_id() | ✅ |
| resolve_account_for_business_connection() | ✅ |
| get_latest_connection_for_account() | ✅ |

### Actor Classification

| actor_type | 说明 | 触发 AI |
|------------|------|---------|
| customer | 对话对方 | ✅ 可以 |
| business_self | 账号本人 | ❌ 不可以 |
| assistant_bot | Bot 自己 | ❌ 不可以 |
| owner_operator | Owner | ❌ 不可以 |
| system | 系统消息 | ❌ 不可以 |
| unknown | 未知 | ❌ 不可以 |

### Context Isolation

```
recent_context_v2(conversation_id, limit, exclude_message_ids):
  - actor_type == customer → role=user
  - actor_type == assistant_bot + direction=out → role=assistant
  - business_self / owner_operator / system / unknown → 不进入 LLM 上下文
  - exclude_message_ids 中的消息被排除 (避免当前批次重复)
```

---

## Known Incomplete Areas

### Web UI

- ✅ `/accounts` 列出所有 Business 账号
- ✅ `/accounts` 页面同时显示 v2 conversation_count 与 legacy chat_count
- ✅ connection id 在模板中脱敏显示 (mask_id)
- ✅ `/accounts/{id}/conversations` 显示该账号的 v2 conversations
- ✅ `/accounts/{id}/conversations/{cid}` 显示 conversation 详情 (含消息)
- ✅ `/accounts/{id}/conversations/{cid}/save` 保存 conversation 设置 (account-scoped)
- ✅ conversation 设置保存只影响目标 conversation，不影响同 peer 其它账号
- ✅ 跨账号 POST 被拒绝 (404)
- ✅ 非法 mode/prompt_mode 返回 400
- ✅ `/conversations/{cid}` 快捷重定向到 account-scoped URL
- ✅ 跨账号访问返回 404
- ✅ conversation settings save 校验 CSRF、persona_id 类型、mode/prompt_mode 合法性
- 旧 `/chats/{chat_id}` 路由保留 (标记为 Legacy)

### Media Group

- 媒体组处理已接入 v2 conversation
- 非文本媒体组只记录不回复 (简化处理)
- 媒体组 caption 使用账号级 settings ✅

### Legacy Compatibility

- 旧 `chats` 表保留，用于兼容
- 旧 `messages` 表保留，用于兼容
- 旧 bot.py 方法 (decide_reply, process_text_batch 等) 保留但不再被主路径使用
- 新数据写入 v2 表 (conversations, messages_v2)

### Documentation

- README 尚未完整更新 v2 运行说明
- 部署文档需要更新

---

## Do Not Release As Stable Until

- [x] Web 只读视图完成 (Phase 4A)
- [ ] Web 设置修改路由完成
- [ ] templates 完全产品化
- [ ] README 更新
- [ ] 部署文档更新
- [ ] 创建 release/tag

---

## Test Coverage

```
40 passed, 0 xfailed
```

测试覆盖:
- 对话隔离 (conversation isolation)
- actor 分类 (actor classification)
- 回复决策 (reply decision)
- 消息记录与上下文 (message recording & context)
- 上下文去重 (context de-duplication)
- 异步 handle_business_message (async message handling)
- 媒体组账号设置 (media group account settings)
- 账号解析 (account resolution)
