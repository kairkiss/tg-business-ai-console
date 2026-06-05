from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db
from config import Config
from deepseek_client import DeepSeekClient
from telegram_client import TelegramClient

router=APIRouter(); templates=Jinja2Templates(directory="templates")
templates.env.globals["mode_label"] = lambda m: {"auto":"自动","manual":"手动","off":"关闭","default":"默认"}.get(m or "default", m)


def mask_id(value: str | None, keep_start: int = 6, keep_end: int = 4) -> str:
    """
    Mask an ID for display. Shows partial start/end with ... in middle.
    Not for tokens/API keys - just for connection IDs etc.
    """
    if not value:
        return "-"
    s = str(value)
    if len(s) <= keep_start + keep_end + 3:
        return "***"
    return f"{s[:keep_start]}...{s[-keep_end:]}"


templates.env.globals["mask_id"] = mask_id


def csrf_token(request):
    token=request.session.get("csrf")
    if not token: token=secrets.token_urlsafe(32); request.session["csrf"]=token
    return token

def pop_flash(request): return request.session.pop("flash", None)
def flash(request,msg): request.session["flash"]=msg

def require_auth(request:Request):
    if not request.session.get("auth"): raise HTTPException(status_code=303, headers={"Location":"/login"})
    csrf_token(request)

def check_csrf(request, token=None):
    expected=request.session.get("csrf"); actual=token or request.headers.get("x-csrf-token")
    if not expected or actual!=expected: raise HTTPException(status_code=403, detail="CSRF 校验失败")

def context(request, **extra):
    settings=db.get_settings(); data={"request":request,"csrf_token":csrf_token(request),"settings":settings,"flash":pop_flash(request),"personas":db.list_personas(False),"all_personas":db.list_personas(True),"default_persona":db.get_default_persona(settings),"accounts":db.list_accounts()}; data.update(extra); return data

def filter_chats(chats,q="",mode="all",account_id="all",persona_id="all",status="all"):
    q=q.strip().lower(); out=[]
    for c in chats:
        if account_id not in ("", "all") and str(c.get("business_account_id") or "") != str(account_id): continue
        if persona_id not in ("", "all") and str(c.get("prompt_persona_id") or "") != str(persona_id): continue
        if mode in {"auto","manual","off","default"} and c.get("mode")!=mode: continue
        if mode=="custom" and (c.get("prompt_mode")!="custom" and int(c.get("custom_prompt_enabled") or 0)!=1): continue
        if mode=="exempt" and int(c.get("takeover_exempt") or 0)!=1: continue
        if status=="filtered" and not c.get("last_filter_triggered_at"): continue
        if status=="active" and not c.get("last_message_at"): continue
        hay=" ".join(str(c.get(k) or "") for k in ("chat_id","title","username","first_name","last_name","note","persona_name","prompt_mode")).lower()
        if q and q not in hay: continue
        out.append(c)
    return out

@router.get("/login")
async def login_page(request:Request): return templates.TemplateResponse("login.html", {"request":request,"error":None})
@router.post("/login")
async def login(request:Request, username:str=Form(...), password:str=Form(...)):
    if username==Config.web_admin_username and password==Config.web_admin_password:
        request.session.clear(); request.session["auth"]=True; request.session["csrf"]=secrets.token_urlsafe(32); return RedirectResponse("/",303)
    return templates.TemplateResponse("login.html", {"request":request,"error":"用户名或密码错误"}, status_code=401)
@router.post("/logout")
async def logout(request:Request, _:None=Depends(require_auth)): request.session.clear(); return RedirectResponse("/login",303)

@router.get("/")
async def dashboard(request:Request, _:None=Depends(require_auth)):
    client=TelegramClient()
    try: tg=await client.get_me(); tg_status="正常"
    except Exception as exc: tg={"error":str(exc)}; tg_status="异常"
    finally: await client.close()
    return templates.TemplateResponse("dashboard.html", context(request,tg=tg,tg_status=tg_status,deepseek_status="未测试",counts=db.counts(),logs=db.get_logs(10),chats=db.list_chats()[:10]))

@router.get("/settings")
async def old_settings_redirect(): return RedirectResponse("/ai",303)
@router.get("/ai")
async def ai_settings_page(request:Request, _:None=Depends(require_auth)):
    settings=db.get_settings(); return templates.TemplateResponse("ai.html", context(request, preview=db.render_prompt(None, settings)))
@router.post("/ai")
async def ai_settings_save(request:Request, csrf:str=Form(...), global_enabled:str=Form("false"), full_takeover_enabled:str=Form("false"), reply_when_owner_online:str=Form("false"), quote_reply_enabled:str=Form("false"), default_reply_mode:str=Form(...), default_prompt_persona_id:str=Form(""), system_prompt:str=Form(...), deepseek_model:str=Form(...), deepseek_temperature:str=Form(...), deepseek_max_tokens:str=Form(...), max_history_messages:str=Form(...), non_text_reply:str=Form(...), ai_tone:str=Form(...), ai_reply_length:str=Form(...), ai_safety_level:str=Form(...), prompt_variables_enabled:str=Form("false"), media_handling_mode:str=Form("silent"), media_reply_cooldown_seconds:str=Form("120"), message_debounce_enabled:str=Form("false"), message_debounce_seconds:str=Form("4"), message_burst_max_wait_seconds:str=Form("12"), human_like_enabled:str=Form("false"), reply_delay_min_seconds:str=Form("2"), reply_delay_max_seconds:str=Form("8"), reply_delay_per_100_chars:str=Form("1.2"), avoid_repeated_intro:str=Form("false"), intro_once_per_chat:str=Form("false"), auto_reply_cooldown_seconds:str=Form("3"), output_filter_enabled:str=Form("false"), fallback_safe_reply:str=Form(""), url_fetch_enabled:str=Form("false"), system_guardrail_prompt:str=Form(""), action:str=Form("save"), _:None=Depends(require_auth)):
    check_csrf(request, csrf)
    if action=="restore_default": system_prompt=db.DEFAULT_SYSTEM_PROMPT
    db.set_settings({"global_enabled":"true" if global_enabled=="true" else "false","full_takeover_enabled":"true" if full_takeover_enabled=="true" else "false","reply_when_owner_online":"true" if reply_when_owner_online=="true" else "false","quote_reply_enabled":"true" if quote_reply_enabled=="true" else "false","default_reply_mode":default_reply_mode,"default_prompt_persona_id":default_prompt_persona_id,"system_prompt":system_prompt,"deepseek_model":deepseek_model,"deepseek_temperature":deepseek_temperature,"deepseek_max_tokens":deepseek_max_tokens,"max_history_messages":max_history_messages,"non_text_reply":non_text_reply,"ai_tone":ai_tone,"ai_reply_length":ai_reply_length,"ai_safety_level":ai_safety_level,"prompt_variables_enabled":"true" if prompt_variables_enabled=="true" else "false","media_handling_mode":media_handling_mode,"media_reply_cooldown_seconds":media_reply_cooldown_seconds,"message_debounce_enabled":"true" if message_debounce_enabled=="true" else "false","message_debounce_seconds":message_debounce_seconds,"message_burst_max_wait_seconds":message_burst_max_wait_seconds,"human_like_enabled":"true" if human_like_enabled=="true" else "false","reply_delay_min_seconds":reply_delay_min_seconds,"reply_delay_max_seconds":reply_delay_max_seconds,"reply_delay_per_100_chars":reply_delay_per_100_chars,"avoid_repeated_intro":"true" if avoid_repeated_intro=="true" else "false","intro_once_per_chat":"true" if intro_once_per_chat=="true" else "false","auto_reply_cooldown_seconds":auto_reply_cooldown_seconds,"output_filter_enabled":"true" if output_filter_enabled=="true" else "false","fallback_safe_reply":fallback_safe_reply,"url_fetch_enabled":"true" if url_fetch_enabled=="true" else "false","system_guardrail_prompt":system_guardrail_prompt})
    if default_prompt_persona_id: db.set_default_persona(int(default_prompt_persona_id))
    db.log("INFO","web","AI 设置已保存"); flash(request,"AI 设置已保存"); return RedirectResponse("/ai",303)


@router.get("/accounts")
async def accounts_page(request:Request, _:None=Depends(require_auth)):
    return templates.TemplateResponse("accounts.html", context(request, accounts=db.list_accounts()))

@router.get("/accounts/{account_id}")
async def account_detail(request:Request, account_id:int, _:None=Depends(require_auth)):
    account=db.get_account(account_id)
    if not account: raise HTTPException(404,"账号不存在")
    chats=[c for c in db.list_chats() if str(c.get("business_account_id") or "")==str(account_id)]
    return templates.TemplateResponse("account_detail.html", context(request, account=account, chats=chats))

@router.post("/accounts/{account_id}/save")
async def account_save(request:Request, account_id:int, csrf:str=Form(...), account_name:str=Form(...), enabled:str=Form("false"), full_takeover_enabled:str=Form("false"), default_reply_mode:str=Form("manual"), default_prompt_persona_id:str=Form(""), media_handling_mode:str=Form(""), message_debounce_enabled:str=Form(""), human_like_enabled:str=Form(""), output_filter_enabled:str=Form(""), quote_reply_enabled:str=Form(""), note:str=Form(""), _:None=Depends(require_auth)):
    check_csrf(request, csrf)
    def tri(v):
        if v == "inherit" or v == "": return None
        return 1 if v == "true" else 0
    db.update_account(account_id,{"account_name":account_name,"enabled":1 if enabled=="true" else 0,"full_takeover_enabled":1 if full_takeover_enabled=="true" else 0,"default_reply_mode":default_reply_mode,"default_prompt_persona_id":int(default_prompt_persona_id) if default_prompt_persona_id else None,"media_handling_mode":media_handling_mode or None,"message_debounce_enabled":tri(message_debounce_enabled),"human_like_enabled":tri(human_like_enabled),"output_filter_enabled":tri(output_filter_enabled),"quote_reply_enabled":tri(quote_reply_enabled),"note":note})
    db.log("INFO","account",f"账号设置已保存：account_id={account_id} name={account_name}"); flash(request,"账号设置已保存"); return RedirectResponse(f"/accounts/{account_id}",303)

@router.post("/accounts/{account_id}/toggle")
async def account_toggle(request:Request, account_id:int, csrf:str=Form(...), field:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); acc=db.get_account(account_id)
    if field not in {"enabled","full_takeover_enabled"}: raise HTTPException(400,"字段不允许")
    cur=int(acc.get(field) or 0); db.update_account(account_id,{field:0 if cur else 1}); return RedirectResponse("/accounts",303)


# ---------------------------------------------------------------------------
# v2 Account Isolation Read-Only Views
# ---------------------------------------------------------------------------

@router.get("/accounts/{account_id}/conversations")
async def account_conversations(request: Request, account_id: int, _: None = Depends(require_auth)):
    """List v2 conversations for a specific account."""
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "账号不存在")
    conversations = db.list_conversations(account_id)
    conversation_count = len(conversations)
    return templates.TemplateResponse("account_conversations.html", context(
        request,
        account=account,
        conversations=conversations,
        conversation_count=conversation_count,
    ))


@router.get("/accounts/{account_id}/conversations/{conversation_id}")
async def account_conversation_detail(request: Request, account_id: int, conversation_id: int, _: None = Depends(require_auth)):
    """Show conversation detail, validated against account_id."""
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "账号不存在")
    conversation, conv_account = db.get_conversation_with_account(conversation_id)
    if not conversation:
        raise HTTPException(404, "对话不存在")
    # Validate account isolation: conversation must belong to this account
    if conversation["business_account_id"] != account_id:
        raise HTTPException(404, "对话不属于此账号")
    messages = db.list_messages_v2(conversation_id, limit=200)
    return templates.TemplateResponse("conversation_detail.html", context(
        request,
        account=account,
        conversation=conversation,
        messages=messages,
    ))


VALID_MODES = {"auto", "manual", "off", "default"}
VALID_PROMPT_MODES = {"account", "global", "persona", "custom"}


@router.post("/accounts/{account_id}/conversations/{conversation_id}/save")
async def conversation_settings_save(
    request: Request,
    account_id: int,
    conversation_id: int,
    csrf: str = Form(...),
    mode: str = Form("default"),
    prompt_mode: str = Form("account"),
    persona_id: str = Form(""),
    custom_prompt: str = Form(""),
    custom_prompt_enabled: str = Form("false"),
    takeover_exempt: str = Form("false"),
    _: None = Depends(require_auth),
):
    """Save v2 conversation settings, validated against account_id."""
    check_csrf(request, csrf)

    # Validate account exists
    account = db.get_account(account_id)
    if not account:
        raise HTTPException(404, "账号不存在")

    # Validate conversation exists and belongs to this account
    conversation = db.get_conversation_by_id(conversation_id)
    if not conversation:
        raise HTTPException(404, "对话不存在")
    if conversation["business_account_id"] != account_id:
        raise HTTPException(404, "对话不属于此账号")

    # Validate mode
    if mode not in VALID_MODES:
        raise HTTPException(400, f"无效的回复模式: {mode}")
    if prompt_mode not in VALID_PROMPT_MODES:
        raise HTTPException(400, f"无效的提示词模式: {prompt_mode}")

    # Build update values
    values = {
        "mode": mode,
        "prompt_mode": prompt_mode,
        "persona_id": int(persona_id) if persona_id else None,
        "custom_prompt": custom_prompt or None,
        "custom_prompt_enabled": 1 if custom_prompt_enabled == "true" else 0,
        "takeover_exempt": 1 if takeover_exempt == "true" else 0,
    }

    db.update_conversation(conversation_id, values)
    db.log("INFO", "web", f"v2 对话设置已保存：conversation_id={conversation_id} account_id={account_id}")
    flash(request, "对话设置已保存")
    return RedirectResponse(f"/accounts/{account_id}/conversations/{conversation_id}", 303)


@router.get("/conversations/{conversation_id}")
async def conversation_shortcut(request: Request, conversation_id: int, _: None = Depends(require_auth)):
    """Shortcut redirect: /conversations/{id} → /accounts/{account_id}/conversations/{id}"""
    conversation, account = db.get_conversation_with_account(conversation_id)
    if not conversation:
        raise HTTPException(404, "对话不存在")
    account_id = conversation["business_account_id"]
    return RedirectResponse(f"/accounts/{account_id}/conversations/{conversation_id}", 302)


@router.get("/personas")
async def personas_page(request:Request, _:None=Depends(require_auth)):
    return templates.TemplateResponse("personas.html", context(request, personas=db.list_personas(True)))
@router.get("/personas/new")
async def persona_new(request:Request, _:None=Depends(require_auth)):
    return templates.TemplateResponse("persona_form.html", context(request, persona={"enabled":1,"prompt":db.DEFAULT_SYSTEM_PROMPT}, resolved=None))
@router.get("/personas/{persona_id}")
async def persona_edit(request:Request, persona_id:int, _:None=Depends(require_auth)):
    p=db.get_persona(persona_id)
    if not p: raise HTTPException(404,"人格不存在")
    return templates.TemplateResponse("persona_form.html", context(request, persona=p, usage=db.persona_usage_count(persona_id)))
@router.post("/personas/save")
async def persona_save(request:Request, csrf:str=Form(...), persona_id:str=Form(""), name:str=Form(...), description:str=Form(""), prompt:str=Form(...), model:str=Form(""), temperature:str=Form(""), max_tokens:str=Form(""), ai_tone:str=Form(""), ai_reply_length:str=Form(""), ai_safety_level:str=Form(""), enabled:str=Form("false"), action:str=Form("save"), _:None=Depends(require_auth)):
    check_csrf(request, csrf); pid=int(persona_id) if persona_id else None
    new_id=db.save_persona({"name":name,"description":description,"prompt":prompt,"model":model,"temperature":temperature,"max_tokens":max_tokens,"ai_tone":ai_tone,"ai_reply_length":ai_reply_length,"ai_safety_level":ai_safety_level,"enabled":"true" if enabled=="true" else "false"}, pid)
    if action=="save_default": db.set_default_persona(new_id)
    db.log("INFO","web",f"提示词人格已保存：{name}"); flash(request,"提示词人格已保存"); return RedirectResponse(f"/personas/{new_id}",303)
@router.post("/personas/{persona_id}/copy")
async def persona_copy(request:Request, persona_id:int, csrf:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); nid=db.copy_persona(persona_id); flash(request,"人格已复制"); return RedirectResponse(f"/personas/{nid or persona_id}",303)
@router.post("/personas/{persona_id}/default")
async def persona_default(request:Request, persona_id:int, csrf:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); db.set_default_persona(persona_id); flash(request,"已设为全局默认人格"); return RedirectResponse("/personas",303)
@router.post("/personas/{persona_id}/toggle")
async def persona_toggle(request:Request, persona_id:int, csrf:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); p=db.get_persona(persona_id); db.save_persona({**p,"enabled":0 if p.get("enabled") else 1}, persona_id); flash(request,"人格启用状态已更新"); return RedirectResponse("/personas",303)
@router.post("/personas/{persona_id}/delete")
async def persona_delete(request:Request, persona_id:int, csrf:str=Form(...), confirm:str=Form(""), _:None=Depends(require_auth)):
    check_csrf(request, csrf)
    if confirm!="DELETE": flash(request,"删除失败：请输入 DELETE 确认"); return RedirectResponse(f"/personas/{persona_id}",303)
    usage=db.persona_usage_count(persona_id); db.delete_persona(persona_id); db.log("WARNING","web",f"已删除提示词人格：id={persona_id}，影响聊天数={usage}"); flash(request,f"人格已删除，{usage} 个聊天回退到全局默认人格"); return RedirectResponse("/personas",303)
@router.post("/personas/test")
async def persona_test(request:Request, csrf:str=Form(...), prompt:str=Form(...), test_message:str=Form(...), model:str=Form(""), temperature:str=Form(""), max_tokens:str=Form(""), _:None=Depends(require_auth)):
    check_csrf(request, csrf); s=db.get_settings()
    try:
        text=await DeepSeekClient().chat([{"role":"system","content":prompt},{"role":"user","content":test_message}], model or s.get("deepseek_model") or "deepseek-chat", float(temperature or s.get("deepseek_temperature") or 0.7), int(max_tokens or s.get("deepseek_max_tokens") or 800))
        return {"ok":True,"reply":text}
    except Exception as exc: return {"ok":False,"error":str(exc)}

@router.get("/chats")
async def chats_page(request:Request, q:str="", mode:str="all", account_id:str="all", persona_id:str="all", status:str="all", _:None=Depends(require_auth)):
    return templates.TemplateResponse("chats.html", context(request,chats=filter_chats(db.list_chats(),q,mode,account_id,persona_id,status),q=q,mode=mode,account_id=account_id,persona_id=persona_id,status=status))
@router.get("/chats/{chat_id}")
async def chat_detail(request:Request, chat_id:int, _:None=Depends(require_auth)):
    chat=db.get_chat(chat_id)
    if not chat: raise HTTPException(404,"聊天不存在")
    settings=db.get_settings(); account=db.get_account(chat.get("business_account_id")) if chat.get("business_account_id") else None; effective=db.resolve_account_settings(settings, account); resolved=db.resolve_prompt(chat, effective)
    if settings.get("global_enabled") != "true": why="不会回复：全局已暂停"; will_reply=False
    elif account and not account.get("enabled"): why="不会回复：该 Business 账号已暂停"; will_reply=False
    elif chat.get("mode") == "off": why="不会回复：此聊天为 off"; will_reply=False
    elif (account and account.get("full_takeover_enabled")) and not chat.get("takeover_exempt"): why="会回复：该账号已开启全面接管"; will_reply=True
    elif chat.get("mode") == "auto": why="会回复：此聊天为 auto"; will_reply=True
    elif chat.get("mode") == "manual": why="不会回复：此聊天为 manual"; will_reply=False
    else:
        dm=(account or {}).get("default_reply_mode") or settings.get("default_reply_mode"); will_reply=(dm=="auto"); why=("会回复：跟随账号/全局默认 auto" if will_reply else f"不会回复：跟随账号/全局默认 {dm}")
    ck=db.conversation_key(chat.get("business_connection_id"), chat_id); return templates.TemplateResponse("chat_detail.html", context(request,chat=chat,account=account,effective=effective,will_reply=will_reply,why=why,conversation_key=ck,messages=db.list_messages(chat_id,100,ck),resolved=resolved,final_prompt=resolved["prompt"],test_result=request.session.pop("test_result",None)))
@router.post("/chats/{chat_id}/save")
async def chat_save(request:Request, chat_id:int, csrf:str=Form(...), mode:str=Form(...), takeover_exempt:str=Form("false"), prompt_mode:str=Form("global"), prompt_persona_id:str=Form(""), custom_prompt_enabled:str=Form("false"), custom_prompt:str=Form(""), note:str=Form(""), _:None=Depends(require_auth)):
    check_csrf(request, csrf)
    db.update_chat(chat_id,{"mode":mode,"takeover_exempt":1 if takeover_exempt=="true" else 0,"prompt_mode":prompt_mode,"prompt_persona_id":int(prompt_persona_id) if prompt_persona_id else None,"custom_prompt_enabled":1 if (custom_prompt_enabled=="true" or prompt_mode=="custom") else 0,"custom_prompt":custom_prompt,"note":note})
    flash(request,"聊天设置已保存"); db.log("INFO","web",f"聊天设置已保存：chat={chat_id}"); return RedirectResponse(f"/chats/{chat_id}",303)
@router.post("/chats/{chat_id}/test")
async def chat_test_prompt(request:Request, chat_id:int, csrf:str=Form(...), test_message:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); chat=db.get_chat(chat_id); settings=db.get_settings(); resolved=db.resolve_prompt(chat, settings)
    try:
        messages=[{"role":"system","content":resolved["prompt"]}]; messages.extend(db.recent_context(chat_id, int(settings.get("max_history_messages") or 12), db.conversation_key(chat.get("business_connection_id"), chat_id))); messages.append({"role":"user","content":test_message})
        text=await DeepSeekClient().chat(messages, resolved["model"], resolved["temperature"], resolved["max_tokens"]); request.session["test_result"]=text; db.log("INFO","deepseek",f"聊天最终 Prompt 测试成功：chat={chat_id} source={resolved['source']}")
    except Exception as exc: request.session["test_result"]=f"测试失败：{exc}"; db.log("ERROR","deepseek",f"聊天最终 Prompt 测试失败：{exc}")
    return RedirectResponse(f"/chats/{chat_id}",303)
@router.post("/api/chats/{chat_id}/mode")
async def api_chat_mode(request:Request, chat_id:int, mode:str=Form(...), csrf:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); db.set_chat_mode(chat_id, mode); return RedirectResponse("/chats",303)
@router.post("/api/chats/{chat_id}/forget")
async def api_chat_forget(request:Request, chat_id:int, csrf:str=Form(...), _:None=Depends(require_auth)):
    check_csrf(request, csrf); chat=db.get_chat(chat_id) or {}; db.forget_chat(chat_id, db.conversation_key(chat.get("business_connection_id"), chat_id)); flash(request,"当前 conversation_key 的上下文已清空"); return RedirectResponse(f"/chats/{chat_id}",303)
@router.get("/chats/{chat_id}/export.txt")
async def export_chat_txt(request:Request, chat_id:int, _:None=Depends(require_auth)):
    return PlainTextResponse("\n".join(f"[{m['created_at']}] {m['direction']}/{m['role']}: {m.get('text') or ''}" for m in reversed(db.list_messages(chat_id,1000))), headers={"Content-Disposition":f"attachment; filename=chat-{chat_id}.txt"})
@router.get("/chats/{chat_id}/export.json")
async def export_chat_json(request:Request, chat_id:int, _:None=Depends(require_auth)): return JSONResponse({"chat":db.get_chat(chat_id),"messages":list(reversed(db.list_messages(chat_id,1000)))})

@router.get("/connections")
async def connections_page(request:Request, _:None=Depends(require_auth)): return templates.TemplateResponse("connections.html", context(request, connections=db.list_connections()))
@router.get("/logs")
async def logs_page(request:Request, level:str=Query("all"), category:str=Query("all"), _:None=Depends(require_auth)): return templates.TemplateResponse("logs.html", context(request, logs=db.get_logs(200, None if level=="all" else level, category), level=level, category=category))
@router.post("/logs/clear")
async def logs_clear(request:Request, csrf:str=Form(...), confirm:str=Form(""), _:None=Depends(require_auth)):
    check_csrf(request, csrf)
    if confirm!="CLEAR": flash(request,"清空失败：请输入 CLEAR 确认"); return RedirectResponse("/logs",303)
    db.clear_logs(); db.log("WARNING","web","运行日志已被管理员清空"); flash(request,"日志已清空"); return RedirectResponse("/logs",303)
@router.get("/system")
async def system_page(request:Request, _:None=Depends(require_auth)): return templates.TemplateResponse("system.html", context(request, env={"WEB_HOST":Config.web_host,"WEB_PORT":Config.web_port,"WEB_ADMIN_USERNAME":Config.web_admin_username,"BOT_OWNER_ID":Config.bot_owner_id,"TELEGRAM_BOT_TOKEN":"已配置" if Config.telegram_bot_token else "未配置","DEEPSEEK_API_KEY":"已配置" if Config.deepseek_api_key else "未配置"}))
@router.get("/api/status")
async def api_status(request:Request, _:None=Depends(require_auth)): return {"ok":True,"settings":db.get_settings(),"counts":db.counts()}
@router.post("/api/global/toggle")
async def api_global_toggle(request:Request, _:None=Depends(require_auth)):
    check_csrf(request); cur=db.get_settings().get("global_enabled")=="true"; db.set_settings({"global_enabled":"false" if cur else "true"}); return {"ok":True,"global_enabled":not cur}
@router.post("/api/takeover/toggle")
async def api_takeover_toggle(request:Request, _:None=Depends(require_auth)):
    check_csrf(request); cur=db.get_settings().get("full_takeover_enabled")=="true"; db.set_settings({"full_takeover_enabled":"false" if cur else "true"}); return {"ok":True,"full_takeover_enabled":not cur}
@router.post("/api/test/deepseek")
async def api_test_deepseek(request:Request, _:None=Depends(require_auth)):
    check_csrf(request); s=db.get_settings(); text=await DeepSeekClient().chat([{"role":"system","content":"Reply with OK only."},{"role":"user","content":"test"}], s.get("deepseek_model") or "deepseek-chat", float(s.get("deepseek_temperature") or 0.7), 32); db.log("INFO","deepseek","DeepSeek 测试成功"); return {"ok":True,"reply":text}
@router.post("/api/test/telegram")
async def api_test_telegram(request:Request, _:None=Depends(require_auth)):
    check_csrf(request); client=TelegramClient()
    try: me=await client.get_me()
    finally: await client.close()
    db.log("INFO","telegram","Telegram Bot Token 测试成功"); return {"ok":True,"bot":me}
@router.get("/api/logs")
async def api_logs(request:Request, _:None=Depends(require_auth)): return {"ok":True,"logs":db.get_logs(200)}
