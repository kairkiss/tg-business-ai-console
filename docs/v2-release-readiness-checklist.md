# v2 Release Readiness Checklist

> Branch: v2.0-account-isolation
> Last updated: 2026-06-05

---

## Current Branch

- **Branch**: `v2.0-account-isolation`
- **Do not tag yet**
- **Do not merge to main until owner approval**

---

## Completed

- [x] v2 schema / user_version=6
- [x] `conversations` table with `UNIQUE(business_account_id, peer_chat_id)`
- [x] `messages_v2` table with `conversation_id` foreign key
- [x] v2 db helpers (create_conversation, add_message_v2, recent_context_v2, etc.)
- [x] account resolution helpers (get_account_by_business_connection_id, etc.)
- [x] actor classification (customer / business_self / assistant_bot / owner_operator / system / unknown)
- [x] Bot v2 text path (handle_business_message using v2 model)
- [x] context isolation (messages scoped by conversation_id)
- [x] context de-duplication (exclude_message_ids for current batch)
- [x] Web account-scoped read views (/accounts/{id}/conversations)
- [x] Web conversation settings save (POST /accounts/{id}/conversations/{cid}/save)
- [x] CSRF validation on all POST routes
- [x] cross-account POST rejection (404)
- [x] persona_id type validation (non-numeric returns 400)
- [x] mode/prompt_mode validation (invalid returns 400)
- [x] connection id masking in web UI
- [x] tests passing (59 passed, 0 xfailed)
- [x] README updated with v2 status

---

## Must Verify Before Merge

- [ ] `pytest -q` passes on clean checkout
- [ ] No `.env`, `secret.key`, `data.sqlite3`, `backups/` committed
- [ ] No token/API key in repository
- [ ] Web v2 pages do not display `raw_json`
- [ ] Connection IDs are masked in web UI
- [ ] Legacy `/chats` is clearly marked as legacy
- [ ] README explains v2 route usage
- [ ] Owner manually reviews UI on local machine

---

## Not Stable Until

- [ ] Owner manually tests with at least two Telegram Business accounts
- [ ] Same `peer_chat_id` under two accounts is verified isolated
- [ ] Non-customer actors (business_self, owner_operator, etc.) do not trigger AI
- [ ] v2 conversation settings save is verified working
- [ ] Backup procedure is verified (`scripts/backup_sqlite.sh`)
- [ ] Access control is behind trusted network / Cloudflare Access / tunnel

---

## Do Not Do Automatically

- ❌ Do not tag
- ❌ Do not merge main
- ❌ Do not delete legacy tables (`chats`, `messages`)
- ❌ Do not delete legacy routes (`/chats`, `/chats/{id}`)
- ❌ Do not publish release notes as stable

---

## Recommended Merge Procedure

1. Owner reviews all changes on local machine
2. Owner tests with real Telegram Business accounts
3. Owner verifies multi-account isolation
4. Owner runs `scripts/backup_sqlite.sh` to backup current data
5. Owner merges `v2.0-account-isolation` into `main`
6. Owner creates tag only after merge verification

---

## Post-Merge Tasks

- [ ] Update deployment documentation
- [ ] Consider productizing templates (UI polish)
- [ ] Monitor for edge cases in production
- [ ] Plan legacy table cleanup timeline
