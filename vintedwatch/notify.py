"""Telegram delivery for the Vinted watcher (@vinted_ads_bot)."""

from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


def _creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    return token, chat_id


def send(text: str) -> bool:
    """Telegram is reached directly, NOT through the metered proxy."""
    token, chat_id = _creds()
    if not token or not chat_id:
        log.warning("Telegram not configured (chat_id=%r) — message dropped:\n%s", chat_id, text)
        return False
    try:
        r = requests.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        log.error("Telegram send failed: %s", exc)
        return False
    if r.status_code != 200:
        log.error("Telegram HTTP %d: %s", r.status_code, r.text[:300])
        return False
    return True


def format_sold(item: dict, hours_listed: float | None) -> str:
    """The ONLY message this bot sends — a listing that sold."""
    e = lambda v: html.escape(str(v)) if v is not None else "—"
    bits = [f"🔴 <b>SOLD</b> — {e(item.get('title'))}"]
    line = " · ".join(
        str(x)
        for x in (
            e(item.get("brand")),
            f"taglia {e(item['size'])}" if item.get("size") else None,
            e(item.get("condition")),
        )
        if x and x != "—"
    )
    if line:
        bits.append(line)
    price = item.get("price")
    if price:
        cur = item.get("currency", "EUR")
        total = item.get("total_price")
        bits.append(
            f"💶 <b>{e(price)} {e(cur)}</b>"
            + (f"  (con protezione: {e(total)} {e(cur)})" if total and total != price else "")
        )
    if hours_listed is not None:
        # We only know how long WE watched it, not how long it was listed —
        # most listings were already on sale when tracking began.
        bits.append(
            f"⏱ visto in vendita per ~{hours_listed:.0f} h"
            if hours_listed < 48
            else f"⏱ visto in vendita per ~{hours_listed / 24:.0f} giorni"
        )
    if item.get("url"):
        bits.append(f'\n<a href="{e(item["url"])}">{e(item["url"])}</a>')
    return "\n".join(bits)
