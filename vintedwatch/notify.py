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


def format_sold(item: dict, hours_listed: float | None,
                exact: bool = False, probable: bool = False) -> str:
    """The ONLY message this bot sends — a listing that left the search.

    `exact`    we watched the listing appear, so the elapsed time is real.
    `probable` the sale was inferred from disappearance rather than read off the
               item page, so it could also be a seller deleting the listing.
    """
    e = lambda v: html.escape(str(v)) if v is not None else "—"
    head = "🔴 <b>VENDUTO (probabile)</b>" if probable else "🔴 <b>VENDUTO</b>"
    bits = [f"{head} — {e(item.get('title'))}"]
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
        # Only claim a real time-to-sale for listings we watched appear; for the
        # rest we merely know how long we happened to be watching.
        label = "venduto dopo" if exact else "visto in vendita per"
        amount = (f"~{hours_listed:.0f} h" if hours_listed < 48
                  else f"~{hours_listed / 24:.0f} giorni")
        bits.append(f"⏱ {label} {amount}")
    if item.get("url"):
        bits.append(f'\n<a href="{e(item["url"])}">{e(item["url"])}</a>')
    if probable:
        bits.append("<i>Sparito dalla ricerca — apri il link per verificare.</i>")
    return "\n".join(bits)
