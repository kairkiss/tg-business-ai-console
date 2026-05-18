from __future__ import annotations

import re

ACTION_PREFIX_RE = re.compile(r"^\s*(?:（[^）]{1,24}）|\([^)]{1,32}\))\s*", re.UNICODE)
ACTION_ONLY_RE = re.compile(r"^\s*(?:（[^）]{1,32}）|\([^)]{1,40}\))\s*[。.!！?？…]*\s*$", re.UNICODE)
COMMON_ACTION_WORDS = (
    "点烟", "冷笑", "沉默", "烟头", "看着你", "叹气", "laugh", "sigh", "looks at you",
    "shrug", "smile", "微笑", "皱眉", "摊手", "低头", "抬头",
)


def filter_reply(text: str, fallback: str) -> tuple[str, bool]:
    original = text or ""
    stripped = original.strip()
    if not stripped:
        return fallback, True
    if ACTION_ONLY_RE.match(stripped):
        return fallback, True
    filtered = ACTION_PREFIX_RE.sub("", stripped, count=1).strip()
    changed = filtered != stripped
    first_chunk = stripped[:50].lower()
    action_like = any(word.lower() in first_chunk for word in COMMON_ACTION_WORDS)
    if changed and not filtered:
        return fallback, True
    if changed and action_like:
        return filtered or fallback, True
    return filtered or fallback, changed
