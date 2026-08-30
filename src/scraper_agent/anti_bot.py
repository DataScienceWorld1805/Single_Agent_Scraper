"""Headers dinámicos, User-Agents y hooks de proxy para evasión anti-bot."""

from __future__ import annotations

import random
from typing import Any

from scraper_agent.config import Settings, get_settings


BLOCK_STATUS_CODES = {403, 429, 503}
CAPTCHA_HINTS = (
    "captcha",
    "cf-challenge",
    "challenge-platform",
    "attention required",
    "access denied",
    "datadome",
    "akamai",
    "cloudflare",
    "just a moment",
    "verify you are human",
    "account-verification",
    "gz/account-verification",
    "verificaci",
    "ingresa a tu cuenta",
    "soy nuevo",
    "ya tengo cuenta",
    "para continuar, ingresa",
)


def pick_user_agent(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    return random.choice(cfg.user_agent_pool)


def pick_proxy(settings: Settings | None = None) -> str | None:
    cfg = settings or get_settings()
    proxies = cfg.proxies()
    if not proxies:
        return None
    return random.choice(proxies)


def build_headers(settings: Settings | None = None, *, referer: str | None = None) -> dict[str, str]:
    ua = pick_user_agent(settings)
    headers = {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers


def httpx_proxy_mount(proxy: str | None) -> str | None:
    """httpx acepta proxy como URL string en AsyncClient(proxy=...)."""
    return proxy


def playwright_proxy_config(proxy: str | None) -> dict[str, Any] | None:
    if not proxy:
        return None
    return {"server": proxy}


def looks_like_block(status_code: int, body: str, final_url: str | None = None) -> bool:
    if status_code in BLOCK_STATUS_CODES:
        return True
    if final_url:
        fu = final_url.lower()
        if "account-verification" in fu or "/gz/" in fu and "verification" in fu:
            return True
    lowered = body[:12000].lower()
    if "account-verification" in lowered or "/gz/account-verification" in lowered:
        return True
    return any(hint in lowered for hint in CAPTCHA_HINTS)


def looks_like_js_heavy(html: str, extracted_text: str) -> bool:
    """Heurística: mucho script / poco texto útil → necesita browser."""
    if len(extracted_text.strip()) < 200 and html.lower().count("<script") >= 5:
        return True
    if "id=\"__next\"" in html or "id=\"root\"" in html or "ng-app" in html:
        if len(extracted_text.strip()) < 400:
            return True
    return False
