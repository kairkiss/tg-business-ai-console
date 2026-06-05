# Telegram Business AI Console

Lightweight FastAPI + SQLite Telegram Business auto-reply console. Run it only on a trusted LAN or behind Cloudflare Access. Do not expose this admin console directly to the public Internet.

Settings that change reply behavior live in SQLite and are editable from the web console. Secrets and startup credentials live in `.env`.

---

## v2.0 Account Isolation Status

**Current branch: `v2.0-account-isolation`**

This branch contains a major refactoring for multi-account isolation. Key changes:

- **v2 data model**: `conversations` and `messages_v2` tables replace the legacy `chats`/`messages` for new data
- **Conversation isolation**: Each conversation is uniquely identified by `(business_account_id, peer_chat_id)`
- **Actor classification**: Messages are classified as `customer`, `business_self`, `assistant_bot`, `owner_operator`, `system`, or `unknown`
- **AI reply guard**: Only `customer` messages trigger AI auto-reply
- **Context isolation**: LLM context is scoped per conversation, with de-duplication for current batch
- **Web v2 views**: Account-scoped conversation views at `/accounts/{id}/conversations`

**Do not tag or release this branch as stable until owner approval.**

---

## Recommended Entry Points (v2)

| Route | Description |
|-------|-------------|
| `/accounts` | List all Business accounts |
| `/accounts/{id}/conversations` | List v2 conversations for an account |
| `/accounts/{id}/conversations/{cid}` | View/edit conversation detail |

## Legacy Entry Points (compatibility only)

| Route | Description |
|-------|-------------|
| `/chats` | Legacy chat list (not account-isolated) |
| `/chats/{id}` | Legacy chat detail (not account-isolated) |

**Legacy routes are based on the old `chat_id` model and are NOT safe for multi-account settings.** Use the v2 routes above for account-isolated configuration.

---

## Security

- **Do not expose to public Internet.** Place behind Cloudflare Access, Tailscale, SSH tunnel, or reverse proxy authentication.
- `.env`, `secret.key`, `data.sqlite3`, and `backups/` should NEVER be committed to git.
- Connection IDs are masked in the web UI.
- `raw_json` is not displayed on v2 conversation detail pages.

---

## Quick Start

```bash
# Copy and fill in secrets
cp .env.example .env

# Install dependencies
pip install -r requirements.txt

# Run
uvicorn app:app --host 0.0.0.0 --port 8787
```

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

Current expected result: **59 passed, 0 xfailed**

---

## Architecture (v2)

```
app.py              → FastAPI entry point
config.py           → .env loading
db.py               → SQLite operations (v1 + v2 tables)
bot.py              → Telegram Bot polling + v2 message handling
telegram_client.py  → Telegram API wrapper
deepseek_client.py  → DeepSeek API wrapper
web.py              → Web console routes (v1 + v2)
output_filter.py    → AI output safety filter
templates/          → Jinja2 HTML templates
static/             → CSS
```

### Data Model (v2)

- `business_accounts` — Telegram Business account configurations
- `conversations` — Per-account, per-peer conversation state
- `messages_v2` — Messages scoped by `conversation_id`
- `prompt_personas` — AI personality templates (global)
- `settings` — Global default settings

### Message Flow (v2)

```
Telegram Update
  → resolve account (business_connection_id)
  → resolve/create conversation (account_id + peer_chat_id)
  → classify actor
  → record message in messages_v2
  → if actor != customer: stop (record only)
  → if customer: decide_reply → generate_reply → send_reply
```
